from __future__ import annotations

import hashlib
import importlib.util
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.datastructures import UploadFile as StarletteUploadFile

from app import __version__
from app.adapters import (
    DouyinAdapter,
    TikTokAdapter,
    canonical_url,
    detect_platform,
    extract_aweme_id,
)
from app.models import (
    ASRMode,
    AcquisitionAnalysisRequest,
    AcquisitionJobRequest,
    AcquisitionJobResponse,
    AnalysisResponse,
    AnalyzeRequest,
    DemoResponse,
    HealthResponse,
    PlatformInfo,
    PlatformsResponse,
    TranscriptionResponse,
    TranscriptionStatus,
)
from app.services import (
    ASRProviderError,
    ASRRouter,
    AcquisitionJobManager,
    AcquisitionJobNotFoundError,
    ContentGenerationError,
    ContentGenerationResult,
    ContentGenerationRouter,
)


SERVICE_NAME = "自媒体通关搭档"
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
MAX_MULTIPART_OVERHEAD_BYTES = 1024 * 1024
ALLOWED_MEDIA_EXTENSIONS = {
    ".aac",
    ".flac",
    ".m4a",
    ".mp3",
    ".mp4",
    ".mpeg",
    ".mpga",
    ".mov",
    ".ogg",
    ".wav",
    ".webm",
}
ALLOWED_MEDIA_TYPES = {
    "application/octet-stream",
    "audio/aac",
    "audio/flac",
    "audio/m4a",
    "audio/mp3",
    "audio/mp4",
    "audio/mpeg",
    "audio/ogg",
    "audio/wav",
    "audio/webm",
    "audio/x-m4a",
    "audio/x-wav",
    "video/mp4",
    "video/quicktime",
    "video/webm",
}
PLATFORMS = [
    PlatformInfo(
        id="douyin",
        name="抖音",
        status="active",
        description="支持登记演示样本与用户补充字幕的证据降级流程。",
    ),
    PlatformInfo(
        id="tiktok",
        name="TikTok",
        status="active",
        description=(
            "支持链接识别与研究稿流程；未接入媒体时不会伪造抓取或转写结果。"
        ),
    ),
    PlatformInfo(
        id="youtube",
        name="YouTube",
        status="planned",
        description="已保留平台适配器边界，尚未接入。",
    ),
    PlatformInfo(
        id="facebook",
        name="Facebook",
        status="planned",
        description="已保留平台适配器边界，尚未接入。",
    ),
    PlatformInfo(
        id="x",
        name="X",
        status="planned",
        description="已保留平台适配器边界，尚未接入。",
    ),
]
PLATFORM_STATUS = {item.id: item.status for item in PLATFORMS}

app = FastAPI(
    title=SERVICE_NAME,
    version=__version__,
    description="以可拍摄交付为前台、以证据与风险边界支持发布前审核的内容分析接口。",
)
douyin_adapter = DouyinAdapter()
tiktok_adapter = TikTokAdapter()
asr_router = ASRRouter()
content_router = ContentGenerationRouter()
acquisition_jobs = AcquisitionJobManager()

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


class UploadValidationError(ValueError):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


def _multipart_available() -> bool:
    return importlib.util.find_spec("multipart") is not None


def _transcription_json(
    *,
    status_code: int,
    status: TranscriptionStatus,
    message: str,
    source: dict[str, Any],
    provider: str | None = None,
    model: str | None = None,
) -> JSONResponse:
    response = TranscriptionResponse(
        status=status,
        message=message,
        transcript=None,
        provider=provider,
        model=model,
        language=None,
        segments=None,
        segments_status="not_available",
        source=source,
        confidence=None,
        confidence_status="not_available",
    )
    return JSONResponse(status_code=status_code, content=response.model_dump(mode="json"))


async def _read_uploaded_media(
    upload: StarletteUploadFile,
) -> tuple[bytes, str, str]:
    filename = Path(upload.filename or "upload.media").name
    suffix = Path(filename).suffix.lower()
    content_type = (upload.content_type or "application/octet-stream").split(";", 1)[0].lower()

    if suffix not in ALLOWED_MEDIA_EXTENSIONS:
        raise UploadValidationError(
            415,
            f"不支持的文件扩展名 {suffix or '[无扩展名]'}。",
        )
    if content_type not in ALLOWED_MEDIA_TYPES:
        raise UploadValidationError(415, f"不支持的媒体类型 {content_type}。")

    content = bytearray()
    while True:
        chunk = await upload.read(1024 * 1024)
        if not chunk:
            break
        content.extend(chunk)
        if len(content) > MAX_UPLOAD_BYTES:
            raise UploadValidationError(413, "上传文件超过 25 MB 限制。")
    if not content:
        raise UploadValidationError(400, "上传文件为空。")
    return bytes(content), filename, content_type


async def _parse_bounded_multipart(request: Request) -> Any:
    original_receive = request.receive
    received_bytes = 0

    async def bounded_receive() -> dict[str, Any]:
        nonlocal received_bytes
        message = await original_receive()
        if message.get("type") == "http.request":
            received_bytes += len(message.get("body", b""))
            if received_bytes > MAX_UPLOAD_BYTES + MAX_MULTIPART_OVERHEAD_BYTES:
                raise UploadValidationError(413, "上传请求超过允许大小。")
        return message

    bounded_request = Request(request.scope, receive=bounded_receive)
    return await bounded_request.form(
        max_files=1,
        max_fields=4,
        max_part_size=MAX_UPLOAD_BYTES,
    )


def _localization_state(payload: AnalyzeRequest | None) -> dict[str, Any]:
    requested = (
        payload.market.model_dump(mode="json")
        if payload
        else {"region": None, "country": None, "language": None}
    )
    return {
        "status": "future_disabled",
        "enabled": False,
        "applied": False,
        "requested": requested,
        "message": "v0.2 仅保存地区、国家与语言选择，不据此改写内容。",
    }


def _product_requirements(payload: AnalyzeRequest | None) -> dict[str, Any]:
    product = payload.product if payload else None
    legacy_context = payload.product_context if payload else None
    structured = product.model_dump(mode="json", exclude_none=True) if product else None

    missing: list[str] = []
    if not product or not (product.name or product.sku):
        missing.append("商品名称或 SKU")
    if not product or not product.category:
        missing.append("商品品类")
    if not product or not product.selling_points:
        missing.append("商品核心卖点")
    if not product or not product.specifications:
        missing.append("商品规格参数")
    if not product or not product.approved_claims:
        missing.append("甲方批准使用的宣传表述")
    if not product or not product.evidence_urls:
        missing.append("商品证明材料")

    return {
        "status": "needs_input" if missing else "submitted_needs_verification",
        "submitted": {
            "legacy_context": legacy_context,
            "structured": structured,
        },
        "verification_status": "unverified_user_submission" if product or legacy_context else "missing",
        "missing_fields": missing,
        "placeholder_policy": "缺失项保留占位符，不得推断为商品事实。",
    }


def _asr_state(payload: AnalyzeRequest | None, transcript_supplied: bool) -> dict[str, Any]:
    mode = payload.asr.mode if payload else "auto"
    return asr_router.plan(mode=mode, transcript_supplied=transcript_supplied)


def _shooting_table_from_fixture(report: dict[str, Any]) -> dict[str, Any]:
    script = report.get("content_package", {}).get("script", {})
    rows = []
    for segment in script.get("segments", []):
        rows.append(
            {
                "time": segment.get("time"),
                "visual": segment.get("visual"),
                "voiceover": segment.get("voiceover"),
                "subtitle": segment.get("screen_text"),
                "product_proof": "[待补：该镜头对应的商品事实或证明材料]",
                "sound": "[待定：现场声、配乐或音效]",
                "purpose": segment.get("purpose"),
            }
        )
    return {
        "status": "research_draft",
        "columns": ["time", "visual", "voiceover", "subtitle", "product_proof", "sound"],
        "rows": rows,
        "missing_fields": ["product_proof", "sound"],
    }


def _quick_result_from_report(report: dict[str, Any]) -> dict[str, Any]:
    """Select a plain-language first view without exposing the legacy report."""
    distillation = report.get("distillation")
    distillation = distillation if isinstance(distillation, dict) else {}
    content_package = report.get("content_package")
    content_package = content_package if isinstance(content_package, dict) else {}
    hook = distillation.get("hook_mechanism")
    hook = hook if isinstance(hook, dict) else {}
    evidence = report.get("evidence_boundary")
    evidence = evidence if isinstance(evidence, dict) else {}

    def text_list(value: Any, limit: int = 5) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item).strip() for item in value if str(item).strip()][:limit]

    summary = str(distillation.get("topic") or report.get("title") or "这条内容的核心方法")
    what_happens = text_list(distillation.get("confirmed_structure"))
    if not what_happens:
        what_happens = [summary]
    why_it_works = text_list(distillation.get("transferable_patterns"), 3)
    if hook.get("pattern"):
        why_it_works.insert(0, str(hook["pattern"]))
    why_it_works = why_it_works[:5]
    transferable = text_list(distillation.get("transferable_patterns"))
    original_angle = str(
        content_package.get("original_angle")
        or distillation.get("transfer_rule")
        or "先讲清方法，再结合自己的真实资料生成原创内容。"
    )
    return {
        "summary": summary,
        "what_happens": what_happens,
        "why_it_works": why_it_works,
        "transferable": transferable,
        "original_angle": original_angle,
        "evidence_boundary": {
            "facts": text_list(evidence.get("facts") or evidence.get("customer_facts")),
            "inferences": text_list(evidence.get("inferences")),
            "pending": text_list(evidence.get("pending") or report.get("source", {}).get("missing")),
        },
    }


def _fixture_report_v02(
    original_report: dict[str, Any],
    payload: AnalyzeRequest | None,
) -> dict[str, Any]:
    legacy = deepcopy(original_report)
    content_package = legacy.get("content_package", {})
    script = content_package.get("script", {})
    risk_gate = deepcopy(legacy.get("risk_gate", {}))
    product_requirements = _product_requirements(payload)

    source_missing = legacy.get("source", {}).get("missing", [])
    fixture_customer_facts = legacy.get("evidence_boundary", {}).get("customer_facts", [])
    fixture_product_missing = any(
        "产品" in str(item) or "商品" in str(item)
        for item in source_missing
    )
    request_changes_product = bool(payload and (payload.product or payload.product_context))
    product_evidence_ready = bool(fixture_customer_facts) and not fixture_product_missing
    source_gate_publishable = bool(risk_gate.get("publishable", False))
    publishable = source_gate_publishable and product_evidence_ready
    publishable = publishable and not request_changes_product
    if not publishable:
        risk_gate["publishable"] = False
    if source_gate_publishable and not publishable:
        risk_gate["status"] = "needs_human_review"
        risk_gate["message"] = "内容方案已生成；商品证据和补充资料完成复核后即可进入发布确认。"
    legacy["risk_gate"] = risk_gate
    delivery_status = "publish_ready" if publishable else "research_draft"
    preferred = {
        "report_schema_version": "0.2",
        "quick_result": _quick_result_from_report(legacy),
        "delivery": {
            "status": delivery_status,
            "publishable": publishable,
            "label": "唯一推荐稿",
            "message": (
                "这是唯一推荐研究稿；商品资料与人工审核完成前不得标记为可发布。"
            ),
        },
        "recommended_script": {
            "status": delivery_status,
            "is_primary": True,
            "publishable": publishable,
            "title": script.get("title"),
            "duration_seconds": script.get("duration_seconds"),
            "full_text": script.get("full_text"),
            "selection_reason": "沿用已审阅演示样本中的唯一完整脚本，不在请求时补写事实。",
            "source_basis": "registered_fixture_content_package",
        },
        "shooting_table": _shooting_table_from_fixture(legacy),
        "publishing_package": {
            "status": delivery_status,
            "publishable": publishable,
            "titles": content_package.get("post_copy", {}).get("title_options", []),
            "post_copy": content_package.get("post_copy", {}).get("body"),
            "tags": content_package.get("post_copy", {}).get("tags", []),
            "cta": content_package.get("cta"),
            "comment_replies": content_package.get("comment_replies", []),
        },
        "localization": _localization_state(payload),
        "product_requirements": product_requirements,
        "evidence_and_risk": {
            "source_evidence": legacy.get("source", {}).get("evidence", []),
            "evidence_boundary": legacy.get("evidence_boundary"),
            "risk_gate": risk_gate,
            "transcript_status": "missing_or_incomplete",
            "product_verification": product_requirements["verification_status"],
        },
        "asr": _asr_state(payload, transcript_supplied=bool(payload and payload.transcript)),
        "legacy_report_schema_version": legacy.get("report_schema_version", "0.1"),
    }
    preferred.update(
        {key: value for key, value in legacy.items() if key != "report_schema_version"}
    )
    return preferred


def _completed_response(
    case: dict[str, Any],
    payload: AnalyzeRequest | None = None,
) -> AnalysisResponse:
    report = _fixture_report_v02(case["report"], payload)
    source = report.get("source", {})
    missing = list(source.get("missing", []))
    return AnalysisResponse(
        status="completed",
        platform=str(source.get("platform", "douyin")),
        message=(
            "已加载登记演示样本。该结果来自预先审阅的演示样本，"
            "不是本次请求对平台页面进行的实时抓取。"
        ),
        source=source,
        report=report,
        missing=missing,
        next_action={
            "type": "review_report",
            "label": "检查推荐稿、拍摄表与人工审核项",
        },
    )


def _unsupported_response(raw_url: str, platform: str) -> AnalysisResponse:
    name = next((item.name for item in PLATFORMS if item.id == platform), platform)
    planned = PLATFORM_STATUS.get(platform) == "planned"
    message = (
        f"{name} 已列入后续平台计划，v0.2 暂不支持。"
        if planned
        else "当前链接不属于 v0.2 支持的平台。"
    )
    return AnalysisResponse(
        status="unsupported",
        platform=platform,
        message=message,
        source={
            "platform": platform,
            "url": canonical_url(raw_url),
            "acquisition_mode": "not_run",
            "evidence": [],
        },
        report=None,
        missing=["受支持的抖音或 TikTok 公开作品链接"],
        next_action={
            "type": "submit_supported_url",
            "label": "改用抖音或 TikTok 公开作品链接",
        },
    )


def _submitted_source(raw_url: str, platform: str) -> dict[str, Any]:
    if platform == "tiktok":
        return tiktok_adapter.inspect_submission(raw_url).as_source()

    url = canonical_url(raw_url)
    return {
        "platform": "douyin",
        "url": url,
        "aweme_id": extract_aweme_id(url),
        "acquisition_mode": "user_supplied_url",
        "retrieval_status": "not_run",
        "evidence": [
            {
                "type": "public_url",
                "label": "用户提交链接",
                "value": url,
                "confidence": "submitted",
            }
        ],
    }


def _needs_input_response(payload: AnalyzeRequest, platform: str) -> AnalysisResponse:
    source = _submitted_source(payload.url, platform)
    missing = ["视频字幕或口播稿"]
    asr = _asr_state(payload, transcript_supplied=False)
    if asr.get("media_required"):
        missing.append("可供 ASR 使用的媒体文件")
    source["missing"] = missing
    source["asr"] = asr
    platform_name = "TikTok" if platform == "tiktok" else "抖音"

    return AnalysisResponse(
        status="needs_input",
        platform=platform,
        message=(
            f"链接属于 {platform_name}，但没有可验证的字幕或媒体输入。"
            "当前版本不会伪造视频内容；请补充字幕，或在后续媒体上传入口接入 ASR。"
        ),
        source=source,
        report=None,
        missing=missing,
        next_action={
            "type": "provide_transcript_or_media",
            "label": "粘贴字幕或补充可转写媒体",
            "fields": ["transcript"],
            "media_upload_status": "future",
        },
    )


def _research_report(payload: AnalyzeRequest, platform: str) -> tuple[dict[str, Any], list[str]]:
    transcript = payload.transcript or ""
    excerpt_limit = 360
    excerpt = transcript[:excerpt_limit]
    if len(transcript) > excerpt_limit:
        excerpt += "..."

    product_requirements = _product_requirements(payload)
    missing = [
        "实时公开指标",
        "评论原文",
        "经模型或人工完成的内容蒸馏",
        *product_requirements["missing_fields"],
    ]
    risk_gate = {
        "status": "not_run",
        "publishable": False,
        "message": "这是可继续改编的研究稿；商品事实与行业风险仍需在发布前完成复核。",
        "blocking_items": missing,
    }
    source = _submitted_source(payload.url, platform)
    source["acquisition_mode"] = "transcript_fallback"
    source["evidence"].append(
        {
            "type": "user_transcript",
            "label": "用户补充字幕",
            "characters": len(transcript),
            "confidence": "submitted",
        }
    )
    source["missing"] = missing

    report = {
        "report_schema_version": "0.2",
        "quick_result": None,
        "delivery": {
            "status": "research_draft",
            "publishable": False,
            "label": "研究稿",
            "message": "资料已入库，但尚不足以生成可发布成稿。",
        },
        "recommended_script": {
            "status": "blocked_needs_analysis",
            "is_primary": True,
            "publishable": False,
            "title": None,
            "duration_seconds": None,
            "full_text": None,
            "selection_reason": "未完成内容蒸馏，不把来源字幕冒充原创推荐稿。",
            "source_material_excerpt": excerpt,
            "missing_fields": ["title", "duration_seconds", "full_text"],
        },
        "shooting_table": {
            "status": "blocked_needs_script",
            "columns": ["time", "visual", "voiceover", "subtitle", "product_proof", "sound"],
            "rows": [],
            "missing_fields": [
                "time",
                "visual",
                "voiceover",
                "subtitle",
                "product_proof",
                "sound",
            ],
        },
        "publishing_package": {
            "status": "blocked_needs_script",
            "publishable": False,
            "titles": [],
            "post_copy": None,
            "tags": [],
            "cta": None,
            "comment_replies": [],
        },
        "localization": _localization_state(payload),
        "product_requirements": product_requirements,
        "evidence_and_risk": {
            "source_evidence": source["evidence"],
            "evidence_boundary": {
                "level": "partial",
                "facts": [
                    "用户提交了公开链接。",
                    f"用户补充了 {len(transcript)} 个字符的字幕或口播稿。",
                ],
                "inferences": [],
                "pending": missing,
            },
            "risk_gate": risk_gate,
            "transcript_status": "user_supplied_unverified",
            "product_verification": product_requirements["verification_status"],
        },
        "asr": _asr_state(payload, transcript_supplied=True),
        "source": source,
        "material": {
            "transcript_excerpt": excerpt,
            "transcript_characters": len(transcript),
            "product_context_received": bool(payload.product_context or payload.product),
        },
        "distillation": None,
        "traffic_assessment": None,
        "content_package": None,
        "risk_gate": risk_gate,
        "analysis_mode": "research_draft",
    }
    return report, missing


def _partial_response(payload: AnalyzeRequest, platform: str) -> AnalysisResponse:
    report, missing = _research_report(payload, platform)
    return AnalysisResponse(
        status="partial",
        platform=platform,
        message=(
            "已建立字幕证据记录和 v0.2 研究稿骨架；当前未执行深度模型分析，"
            "不会伪造蒸馏、脚本、商品事实或合规结论。"
        ),
        source=report["source"],
        report=report,
        missing=missing,
        next_action={
            "type": "human_or_model_analysis",
            "label": "补齐商品资料并进入受控深度分析",
        },
    )


def _apply_generated_research_draft(
    response: AnalysisResponse,
    generated: ContentGenerationResult,
) -> AnalysisResponse:
    report = response.report
    if report is None:
        return response
    data = generated.data
    recommended = dict(data["recommended_script"])
    recommended.update(
        {
            "status": "research_draft",
            "is_primary": True,
            "publishable": False,
            "source_basis": "verified_transcript_and_client_product_input",
        }
    )
    rows = data["shooting_table"]
    publishing = dict(data["publishing_package"])
    publishing.update({"status": "research_draft", "publishable": False})

    report["delivery"] = {
        "status": "research_draft",
        "publishable": False,
        "label": "唯一推荐研究稿",
        "message": "DeepSeek 已生成研究稿；商品事实与人工审核仍需在发布前完成。",
    }
    report["recommended_script"] = recommended
    report["shooting_table"] = {
        "status": "research_draft",
        "columns": [
            "time",
            "visual",
            "voiceover",
            "subtitle",
            "product_proof",
            "sound",
        ],
        "rows": rows,
        "missing_fields": report.get("product_requirements", {}).get(
            "missing_fields", []
        ),
    }
    report["publishing_package"] = publishing
    report["distillation"] = data.get("marketing_structure")
    evidence_boundary = data.get("evidence_boundary")
    if isinstance(evidence_boundary, dict):
        report["evidence_and_risk"]["generated_evidence_boundary"] = evidence_boundary
    report["generation"] = {
        "status": "completed_research_draft",
        "provider": generated.provider,
        "model": generated.model,
        "paid_api_called": True,
        "publishable": False,
        "provider_metadata": generated.provider_metadata,
        "message": "内容生成完成；服务器已强制保留研究稿与发布前人工审核。",
    }
    report["risk_gate"]["publishable"] = False
    report["evidence_and_risk"]["risk_gate"]["publishable"] = False
    response.message = (
        "已根据字幕和商品资料生成唯一推荐研究稿；"
        "商品事实核验与人工审核完成后即可进入发布确认。"
    )
    response.next_action = {
        "type": "review_research_draft",
            "label": "核对商品事实、拍摄表与待核验项",
    }
    return response


def _apply_generated_quick_result(
    response: AnalysisResponse,
    generated: ContentGenerationResult,
) -> AnalysisResponse:
    report = response.report
    if report is None:
        return response
    report["quick_result"] = generated.data
    report["generation"] = {
        "status": "completed_quick",
        "provider": generated.provider,
        "model": generated.model,
        "paid_api_called": True,
        "publishable": False,
        "provider_metadata": generated.provider_metadata,
        "message": "快速解读已完成；完整脚本和发布包可按需继续生成。",
    }
    response.message = "已先生成快速解读；需要完整脚本时可继续补充商品资料。"
    response.next_action = {
        "type": "generate_full_package",
        "label": "补充商品资料并生成完整脚本",
    }
    return response


def _acquisition_analysis_material(
    job_id: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    try:
        status = acquisition_jobs.store.status(job_id)
    except AcquisitionJobNotFoundError as exc:
        raise HTTPException(status_code=404, detail="采集任务不存在。") from exc
    if status.get("status") != "completed":
        raise HTTPException(
            status_code=409,
            detail={
                "message": "采集任务尚未完成，不能进入内容分析。",
                "status": status.get("status"),
                "missing": status.get("missing", []),
            },
        )

    try:
        manifest = acquisition_jobs.store.manifest(job_id)
    except AcquisitionJobNotFoundError as exc:
        raise HTTPException(status_code=409, detail="完成态任务缺少证据清单。") from exc
    if manifest.get("status") != "completed" or not manifest.get("analysis_ready"):
        raise HTTPException(
            status_code=409,
            detail="采集清单尚未达到可分析状态。",
        )

    items = manifest.get("items")
    item = items[0] if isinstance(items, list) and items else None
    if not isinstance(item, dict):
        raise HTTPException(
            status_code=422,
            detail="采集清单没有可供分析的内容条目。",
        )
    if manifest.get("acquisition_mode") == "registered_fixture":
        return status, manifest, item, {}
    content = item.get("content") if isinstance(item, dict) else None
    transcript = content.get("transcript") if isinstance(content, dict) else None
    text = str(transcript.get("text") or "").strip() if isinstance(transcript, dict) else ""
    if not isinstance(item, dict) or not isinstance(transcript, dict) or not text:
        raise HTTPException(
            status_code=422,
            detail="采集清单没有可供分析的非空字幕。",
        )

    artifacts = manifest.get("raw_artifacts")
    transcript_artifact = next(
        (
            artifact
            for artifact in artifacts
            if isinstance(artifact, dict) and artifact.get("name") == "transcript.json"
        ),
        None,
    ) if isinstance(artifacts, list) else None
    transcript_sha256 = (
        str(transcript_artifact.get("sha256") or "").lower()
        if isinstance(transcript_artifact, dict)
        else ""
    )
    if len(transcript_sha256) != 64 or any(
        character not in "0123456789abcdef" for character in transcript_sha256
    ):
        raise HTTPException(
            status_code=422,
            detail="采集清单缺少有效的字幕文件 SHA-256。",
        )
    transcript = {**transcript, "text": text}
    return status, manifest, item, {**transcript_artifact, "sha256": transcript_sha256}


def _attach_acquisition_context(
    response: AnalysisResponse,
    *,
    status: dict[str, Any],
    manifest: dict[str, Any],
    item: dict[str, Any],
    transcript_artifact: dict[str, Any],
) -> AnalysisResponse:
    raw_artifacts = manifest.get("raw_artifacts")
    source_artifact = next(
        (
            artifact
            for artifact in raw_artifacts
            if isinstance(artifact, dict) and artifact.get("name") == "source.json"
        ),
        None,
    ) if isinstance(raw_artifacts, list) else None
    source_artifact_context = (
        {
            key: source_artifact.get(key)
            for key in ("name", "url", "size_bytes", "sha256")
        }
        if isinstance(source_artifact, dict)
        else None
    )
    context_base = {
        "job_id": status["job_id"],
        "status": status["status"],
        "manifest_url": status.get("manifest_url"),
        "manifest_schema_version": manifest.get("schema_version"),
        "acquisition_mode": manifest.get("acquisition_mode"),
        "stable_id": manifest.get("stable_id"),
        "analysis_ready": True,
        "completed_at": manifest.get("completed_at"),
        "evidence_strength": (
            "reviewed_fixture"
            if manifest.get("acquisition_mode") == "registered_fixture"
            else "runtime_public_snapshot"
        ),
        "source_artifact": source_artifact_context,
        "evidence_summary": manifest.get("evidence_summary", {}),
    }
    if not transcript_artifact:
        acquisition_context = {
            **context_base,
            "transcript": None,
        }
        response.source["acquisition"] = acquisition_context
        if response.report is not None:
            response.report["acquisition"] = acquisition_context
            response.report["source"] = response.source
        response.message = "登记样本采集任务已进入内容分析。" + response.message
        return response

    content = item.get("content") if isinstance(item.get("content"), dict) else {}
    transcript = (
        content.get("transcript")
        if isinstance(content.get("transcript"), dict)
        else {}
    )
    transcript_text = str(transcript.get("text") or "").strip()
    manifest_missing = manifest.get("evidence_summary", {}).get("missing", [])
    manifest_missing = manifest_missing if isinstance(manifest_missing, list) else []
    metrics = item.get("metrics") if isinstance(item.get("metrics"), dict) else {}
    analysis_missing = list(response.missing)
    if any(value is not None for value in metrics.values()):
        analysis_missing = [
            missing for missing in analysis_missing if missing != "实时公开指标"
        ]
    combined_missing = list(
        dict.fromkeys([*(str(item) for item in manifest_missing), *analysis_missing])
    )
    transcript_source = str(transcript.get("source") or "worker_transcript")
    acquisition_context = {
        **context_base,
        "transcript": {
            "source": transcript_source,
            "provider": transcript.get("provider"),
            "model": transcript.get("model"),
            "language": transcript.get("language"),
            "character_count": len(transcript_text),
            "segment_count": transcript.get("segment_count"),
            "artifact_name": transcript_artifact.get("name"),
            "artifact_url": transcript_artifact.get("url"),
            "sha256": transcript_artifact["sha256"],
        },
    }

    source = response.source
    source["url"] = manifest.get("canonical_url") or source.get("url")
    source["acquisition_mode"] = manifest.get("acquisition_mode")
    source["retrieval_status"] = "completed"
    source["author"] = item.get("author", {})
    source["content"] = {
        key: value for key, value in content.items() if key != "transcript"
    }
    source["metrics"] = metrics
    source["evidence"] = [
        dict(evidence)
        for evidence in item.get("evidence", [])
        if isinstance(evidence, dict)
    ]
    source["evidence"].append(
        {
            "type": "timed_transcript",
            "label": "隔离 Worker 自动取得的带时间码字幕",
            "value": transcript_source,
            "confidence": "runtime_generated",
            "sha256": transcript_artifact["sha256"],
        }
    )
    source["missing"] = combined_missing
    source["acquisition"] = acquisition_context
    response.missing = combined_missing
    response.message = "采集任务已自动进入内容分析。" + response.message

    report = response.report
    if report is None:
        return response
    report["source"] = source
    report["acquisition"] = acquisition_context
    evidence_and_risk = report.get("evidence_and_risk")
    if isinstance(evidence_and_risk, dict):
        evidence_and_risk["source_evidence"] = source["evidence"]
        evidence_and_risk["transcript_status"] = transcript_source
        boundary = evidence_and_risk.get("evidence_boundary")
        if isinstance(boundary, dict):
            facts = [
                str(fact)
                for fact in boundary.get("facts", [])
                if "用户补充了" not in str(fact)
            ]
            facts.append(
                f"隔离 Worker 自动取得 {len(transcript_text)} 个字符的带时间码字幕。"
            )
            if metrics:
                facts.append("采集清单包含本次公开页面返回的数字指标。")
            boundary["facts"] = facts
            boundary["pending"] = combined_missing
    report["asr"] = {
        "status": "completed",
        "mode": "automatic_acquisition",
        "selected_provider": transcript.get("provider") or transcript_source,
        "model": transcript.get("model"),
        "language": transcript.get("language"),
        "paid_api_called": False,
        "media_required": False,
        "message": "字幕已由隔离采集 Worker 自动取得。",
    }
    material = report.get("material")
    if isinstance(material, dict):
        material["transcript_source"] = transcript_source
        material["transcript_language"] = transcript.get("language")
        material["transcript_segment_count"] = transcript.get("segment_count")
    risk_gate = report.get("risk_gate")
    if isinstance(risk_gate, dict):
        risk_gate["blocking_items"] = combined_missing
    return response


@app.post(
    "/api/acquisition/jobs",
    response_model=AcquisitionJobResponse,
    status_code=202,
)
def create_acquisition_job(payload: AcquisitionJobRequest) -> AcquisitionJobResponse:
    platform = detect_platform(payload.url)
    if PLATFORM_STATUS.get(platform) != "active":
        raise HTTPException(
            status_code=400,
            detail="采集任务当前只接受抖音或 TikTok 公开链接。",
        )
    return AcquisitionJobResponse.model_validate(acquisition_jobs.submit(payload))


@app.get(
    "/api/acquisition/jobs/{job_id}",
    response_model=AcquisitionJobResponse,
)
def acquisition_job_status(job_id: str) -> AcquisitionJobResponse:
    try:
        status = acquisition_jobs.store.status(job_id)
    except AcquisitionJobNotFoundError as exc:
        raise HTTPException(status_code=404, detail="采集任务不存在。") from exc
    return AcquisitionJobResponse.model_validate(status)


@app.get("/api/acquisition/jobs/{job_id}/manifest")
def acquisition_job_manifest(job_id: str) -> dict[str, Any]:
    try:
        return acquisition_jobs.store.manifest(job_id)
    except AcquisitionJobNotFoundError as exc:
        raise HTTPException(status_code=404, detail="采集清单尚不存在。") from exc


@app.get(
    "/api/acquisition/jobs/{job_id}/artifacts/{artifact_name}",
    response_model=None,
)
def acquisition_job_artifact(job_id: str, artifact_name: str) -> FileResponse:
    try:
        path = acquisition_jobs.store.artifact_path(job_id, artifact_name)
        record = acquisition_jobs.store.artifact_record(job_id, artifact_name)
    except AcquisitionJobNotFoundError as exc:
        raise HTTPException(status_code=404, detail="采集证据文件不存在。") from exc
    return FileResponse(
        path,
        media_type=str(record.get("content_type") or "application/octet-stream"),
        filename=artifact_name,
    )


@app.post(
    "/api/transcribe",
    response_model=TranscriptionResponse,
    responses={
        400: {"model": TranscriptionResponse},
        413: {"model": TranscriptionResponse},
        415: {"model": TranscriptionResponse},
        502: {"model": TranscriptionResponse},
        503: {"model": TranscriptionResponse},
    },
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "multipart/form-data": {
                    "schema": {
                        "type": "object",
                        "required": ["file"],
                        "properties": {
                            "file": {"type": "string", "format": "binary"},
                            "provider": {
                                "type": "string",
                                "enum": ["auto", "external", "local", "disabled"],
                                "default": "auto",
                            },
                            "language": {
                                "type": "string",
                                "description": "可选 ISO 语言代码；留空时自动检测。",
                            },
                        },
                    }
                }
            },
        }
    },
)
async def transcribe(
    request: Request,
    provider: ASRMode = Query(default="auto"),
    language: str | None = Query(default=None, max_length=35),
) -> TranscriptionResponse | JSONResponse:
    base_source: dict[str, Any] = {
        "acquisition_mode": "user_upload",
        "retained": False,
        "external_api_call_attempted": False,
    }
    content_type_header = request.headers.get("content-type", "").lower()
    if not content_type_header.startswith("multipart/form-data"):
        return _transcription_json(
            status_code=415,
            status="failed",
            message="/api/transcribe 仅接受 multipart/form-data 文件上传。",
            source=base_source,
        )

    content_length = request.headers.get("content-length")
    if content_length:
        try:
            request_size = int(content_length)
        except ValueError:
            return _transcription_json(
                status_code=400,
                status="failed",
                message="Content-Length 无效。",
                source=base_source,
            )
        if request_size > MAX_UPLOAD_BYTES + MAX_MULTIPART_OVERHEAD_BYTES:
            return _transcription_json(
                status_code=413,
                status="failed",
                message="上传请求超过允许大小。",
                source=base_source,
            )

    if not _multipart_available():
        base_source["missing_dependency"] = "python-multipart"
        return _transcription_json(
            status_code=503,
            status="unavailable",
            message="服务端未安装 python-multipart，暂时无法解析文件上传。",
            source=base_source,
        )

    form = None
    try:
        form = await _parse_bounded_multipart(request)
        upload = form.get("file")
        if not isinstance(upload, StarletteUploadFile):
            raise UploadValidationError(400, "缺少 file 上传字段。")

        form_provider = form.get("provider")
        mode_value = str(form_provider).strip() if isinstance(form_provider, str) else provider
        if mode_value not in {"auto", "external", "local", "disabled"}:
            raise UploadValidationError(400, "provider 必须是 auto、external、local 或 disabled。")
        mode = cast(ASRMode, mode_value)

        form_language = form.get("language")
        requested_language = (
            str(form_language).strip()
            if isinstance(form_language, str) and form_language.strip()
            else language
        )
        if requested_language and len(requested_language) > 35:
            raise UploadValidationError(400, "language 长度不能超过 35 个字符。")

        media, filename, media_type = await _read_uploaded_media(upload)
    except UploadValidationError as exc:
        return _transcription_json(
            status_code=exc.status_code,
            status="failed",
            message=str(exc),
            source=base_source,
        )
    except Exception as exc:
        base_source["parser_error_type"] = type(exc).__name__
        return _transcription_json(
            status_code=400,
            status="failed",
            message="无法解析上传表单。",
            source=base_source,
        )
    finally:
        if form is not None:
            await form.close()

    source = {
        **base_source,
        "filename": filename,
        "content_type": media_type,
        "size_bytes": len(media),
        "sha256": hashlib.sha256(media).hexdigest(),
    }
    plan = asr_router.plan(mode=mode, transcript_supplied=False)
    selected_provider = plan.get("selected_provider")
    selected_model = next(
        (
            item.get("model")
            for item in plan.get("providers", [])
            if item.get("provider") == selected_provider
        ),
        None,
    )
    if not selected_provider:
        source["provider_plan"] = plan
        return _transcription_json(
            status_code=503,
            status="unavailable",
            message=str(plan.get("message") or "没有可用的 ASR provider。"),
            source=source,
            provider=None,
            model=cast(str | None, selected_model),
        )

    source["provider_initially_selected"] = selected_provider
    source["external_api_call_attempted"] = selected_provider == "external_api"
    try:
        result = await asr_router.transcribe(
            mode=mode,
            media=media,
            filename=filename,
            content_type=media_type,
            language=requested_language,
        )
    except ASRProviderError as exc:
        return _transcription_json(
            status_code=502,
            status="failed",
            message=str(exc),
            source=source,
            provider=str(selected_provider),
            model=cast(str | None, selected_model),
        )

    if result is None:
        return _transcription_json(
            status_code=503,
            status="unavailable",
            message="provider 状态在执行前发生变化，本次未取得转写。",
            source=source,
        )

    source["provider_selected"] = result.provider
    source["provider_fallback_used"] = result.provider != selected_provider
    source["provider_metadata"] = result.provider_metadata
    return TranscriptionResponse(
        status="completed",
        message="转写已完成；文字仍需结合原媒体进行人工复核。",
        transcript=result.transcript,
        provider=result.provider,
        model=result.model,
        language=result.language,
        segments=result.segments,
        segments_status=result.segments_status,
        source=source,
        confidence=result.confidence,
        confidence_status=result.confidence_status,
    )


@app.get("/api/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        service=SERVICE_NAME,
        version=__version__,
        paid_content_enabled=bool(content_router.plan().get("configured")),
    )


@app.get("/api/platforms", response_model=PlatformsResponse)
def platforms() -> PlatformsResponse:
    return PlatformsResponse(platforms=PLATFORMS)


@app.get("/api/demo", response_model=DemoResponse)
def demo() -> DemoResponse:
    _, case = douyin_adapter.get_default_case()
    url = str(case["report"]["source"]["url"])
    return DemoResponse(sample_input={"url": url}, result=_completed_response(case))


@app.post("/api/analyze", response_model=AnalysisResponse)
async def analyze(payload: AnalyzeRequest) -> AnalysisResponse:
    platform = detect_platform(payload.url)
    if PLATFORM_STATUS.get(platform) != "active":
        return _unsupported_response(payload.url, platform)

    if platform == "douyin":
        registered = douyin_adapter.get_registered_case(payload.url)
        if registered:
            return _completed_response(registered, payload)
    elif platform == "tiktok":
        registered = tiktok_adapter.get_registered_case(payload.url)
        if registered:
            return _completed_response(registered, payload)

    if not payload.transcript:
        return _needs_input_response(payload, platform)

    response = _partial_response(payload, platform)
    if response.report is None:
        return response

    response.report["generation"] = content_router.plan()
    if payload.analysis_mode == "quick":
        try:
            generated = await content_router.generate_quick(
                platform=platform,
                transcript=payload.transcript or "",
                product_context=payload.product_context,
                product=(
                    payload.product.model_dump(mode="json", exclude_none=True)
                    if payload.product
                    else None
                ),
            )
        except ContentGenerationError as exc:
            response.report["generation"] = {
                **content_router.plan(),
                "status": "failed_quick_fallback",
                "paid_api_called": True,
                "message": str(exc),
            }
            return response
        if generated is None:
            return response
        return _apply_generated_quick_result(response, generated)

    try:
        generated = await content_router.generate(
            platform=platform,
            transcript=payload.transcript or "",
            product_context=payload.product_context,
            product=(
                payload.product.model_dump(mode="json", exclude_none=True)
                if payload.product
                else None
            ),
        )
    except ContentGenerationError as exc:
        response.report["generation"] = {
            **content_router.plan(),
            "status": "failed_research_draft_fallback",
            "paid_api_called": True,
            "message": str(exc),
        }
        return response
    if generated is None:
        return response
    return _apply_generated_research_draft(response, generated)


@app.post(
    "/api/acquisition/jobs/{job_id}/analyze",
    response_model=AnalysisResponse,
)
async def analyze_acquisition_job(
    job_id: str,
    payload: AcquisitionAnalysisRequest,
) -> AnalysisResponse:
    status, manifest, item, transcript_artifact = _acquisition_analysis_material(
        job_id
    )
    transcript = (
        item.get("content", {}).get("transcript")
        if manifest.get("acquisition_mode") != "registered_fixture"
        else None
    )
    analysis_payload = AnalyzeRequest(
        url=str(manifest["canonical_url"]),
        analysis_mode=payload.analysis_mode,
        transcript=str(transcript["text"]) if isinstance(transcript, dict) else None,
        product_context=payload.product_context,
        product=payload.product,
        market=payload.market,
    )
    response = await analyze(analysis_payload)
    return _attach_acquisition_context(
        response,
        status=status,
        manifest=manifest,
        item=item,
        transcript_artifact=transcript_artifact,
    )


@app.get("/", include_in_schema=False, response_model=None)
def index() -> FileResponse | JSONResponse:
    index_path = STATIC_DIR / "index.html"
    if index_path.is_file():
        return FileResponse(index_path)
    return JSONResponse(
        {
            "service": SERVICE_NAME,
            "status": "ok",
            "docs": "/docs",
        }
    )
