from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import os
import re
from copy import deepcopy
from pathlib import Path
from typing import Any, cast
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.datastructures import UploadFile as StarletteUploadFile
from starlette.concurrency import run_in_threadpool

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
    DouyinBrowserExportRequest,
    DouyinDownloadImportRequest,
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
    CreatorDataImport,
    DouyinAccountCreate,
    DouyinAccountNotFoundError,
    DouyinAccountStorageError,
    DouyinAccountStore,
    DouyinAccountUpdate,
    DouyinAccountValidationError,
    DouyinCreatorBrowserError,
    export_creator_data,
    latest_creator_download,
    list_browsers,
    DouyinTopicCreate,
    DouyinTopicNotFoundError,
    DouyinTopicStorageError,
    DouyinTopicStore,
    DouyinTopicUpdate,
    FullContentError,
    PublishBackfillInput,
    PublishCalibrationConflictError,
    PublishCalibrationStorageError,
    PublishCalibrationStore,
    PublishCalibrationValidationError,
    PublishExperimentCreate,
    PublishExperimentNotFoundError,
    PublishRecordInput,
    PublishReviewInput,
    LocalOCRProvider,
    LocalOllamaVisionProvider,
    UnavailableVisionProvider,
    VisualAnalysisConfig,
    VisualAnalysisError,
    VisualAnalyzer,
    OperationsAgent,
    OperationsAgentConfirmationError,
    OperationsAgentError,
    OperationsAgentRequest,
    OperationsAgentUnavailableError,
    build_timeline,
    build_product_requirements,
    infer_product_relevance,
    is_optional_enhancement,
    merge_product_relevance,
    ocr_items,
    paginated_response,
    read_verified_transcript,
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
VISUAL_ARTIFACT_NAME_RE = re.compile(
    r"^(?:visual_analysis\.json|visual_frame_[0-9]{2}_[0-9]{9}ms\.jpg)$"
)
MAX_VISUAL_MEDIA_BYTES = 512 * 1024 * 1024
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


@app.exception_handler(PublishExperimentNotFoundError)
async def _publish_not_found_handler(
    request: Request, exc: PublishExperimentNotFoundError
) -> JSONResponse:
    del request
    return JSONResponse(status_code=404, content={"detail": str(exc)})


@app.exception_handler(PublishCalibrationConflictError)
async def _publish_conflict_handler(
    request: Request, exc: PublishCalibrationConflictError
) -> JSONResponse:
    del request
    return JSONResponse(status_code=409, content={"detail": str(exc)})


@app.exception_handler(PublishCalibrationValidationError)
async def _publish_validation_handler(
    request: Request, exc: PublishCalibrationValidationError
) -> JSONResponse:
    del request
    return JSONResponse(status_code=422, content={"detail": str(exc)})


@app.exception_handler(PublishCalibrationStorageError)
async def _publish_storage_handler(
    request: Request, exc: PublishCalibrationStorageError
) -> JSONResponse:
    del request, exc
    return JSONResponse(
        status_code=503,
        content={"detail": "发布实验存储暂时不可用，请稍后重试。"},
    )


@app.exception_handler(DouyinTopicNotFoundError)
async def _douyin_topic_not_found_handler(
    request: Request, exc: DouyinTopicNotFoundError
) -> JSONResponse:
    del request
    return JSONResponse(status_code=404, content={"detail": str(exc)})


@app.exception_handler(DouyinTopicStorageError)
async def _douyin_topic_storage_handler(
    request: Request, exc: DouyinTopicStorageError
) -> JSONResponse:
    del request, exc
    return JSONResponse(
        status_code=503,
        content={"detail": "抖音选题存储暂时不可用，请稍后重试。"},
    )


@app.exception_handler(DouyinAccountNotFoundError)
async def _douyin_account_not_found_handler(
    request: Request, exc: DouyinAccountNotFoundError
) -> JSONResponse:
    del request
    return JSONResponse(status_code=404, content={"detail": str(exc)})


@app.exception_handler(DouyinAccountValidationError)
async def _douyin_account_validation_handler(
    request: Request, exc: DouyinAccountValidationError
) -> JSONResponse:
    del request
    return JSONResponse(status_code=422, content={"detail": str(exc)})


@app.exception_handler(DouyinAccountStorageError)
async def _douyin_account_storage_handler(
    request: Request, exc: DouyinAccountStorageError
) -> JSONResponse:
    del request, exc
    return JSONResponse(
        status_code=503,
        content={"detail": "抖音账号与创作者中心数据暂时不可用，请稍后重试。"},
    )


@app.exception_handler(DouyinCreatorBrowserError)
async def _douyin_creator_browser_handler(
    request: Request, exc: DouyinCreatorBrowserError
) -> JSONResponse:
    del request
    return JSONResponse(status_code=503, content={"detail": str(exc)})


@app.exception_handler(OperationsAgentConfirmationError)
async def _operations_agent_confirmation_handler(
    request: Request, exc: OperationsAgentConfirmationError
) -> JSONResponse:
    del request
    return JSONResponse(status_code=409, content={"detail": str(exc)})


@app.exception_handler(OperationsAgentUnavailableError)
async def _operations_agent_unavailable_handler(
    request: Request, exc: OperationsAgentUnavailableError
) -> JSONResponse:
    del request
    return JSONResponse(status_code=503, content={"detail": str(exc)})


@app.exception_handler(OperationsAgentError)
async def _operations_agent_error_handler(
    request: Request, exc: OperationsAgentError
) -> JSONResponse:
    del request
    return JSONResponse(status_code=502, content={"detail": str(exc)})


douyin_adapter = DouyinAdapter()
tiktok_adapter = TikTokAdapter()
asr_router = ASRRouter()
content_router = ContentGenerationRouter()
operations_agent = OperationsAgent()
acquisition_jobs = AcquisitionJobManager()
publish_calibration = PublishCalibrationStore()
douyin_topics = DouyinTopicStore()
douyin_accounts = DouyinAccountStore()
APP_ROOT = Path(__file__).resolve().parent.parent
configured_ocr_python = os.environ.get("PROJECT024_OCR_PYTHON", "").strip()
ocr_python = (
    Path(configured_ocr_python).expanduser().resolve()
    if configured_ocr_python
    else APP_ROOT / ".venv-ocr" / "Scripts" / "python.exe"
)
vision_enabled = os.environ.get("PROJECT024_VISION_ENABLED", "1").strip().lower() not in {
    "0",
    "false",
    "no",
    "off",
}
vision_provider = (
    LocalOllamaVisionProvider(
        base_url=os.environ.get(
            "PROJECT024_VISION_BASE_URL", "http://127.0.0.1:11435"
        ).strip(),
        model=os.environ.get("PROJECT024_VISION_MODEL", "qwen2.5vl:3b").strip(),
    )
    if vision_enabled
    else UnavailableVisionProvider()
)
visual_analyzer = VisualAnalyzer(
    ocr_provider=LocalOCRProvider(python_executable=ocr_python),
    vision_provider=vision_provider,
    config=VisualAnalysisConfig(total_timeout_seconds=180),
)

STATIC_DIR = APP_ROOT / "static"
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


_PRODUCT_GAP_MARKERS = (
    "商品",
    "产品",
    "客户产品",
    "品牌",
    "型号",
    "sku",
    "规格",
    "参数",
    "卖点",
    "资质",
    "宣传表述",
    "证明材料",
    "附件",
    "遥控",
    "售价",
    "价格",
    "库存",
    "物流",
    "售后",
    "套装",
)


def _product_relevance(
    payload: AnalyzeRequest | None,
    *,
    source_material: Any = None,
    transcript: str | None = None,
) -> dict[str, Any]:
    if payload is None:
        return infer_product_relevance(
            transcript=transcript,
            source_material=source_material,
        )
    return infer_product_relevance(
        transcript=transcript if transcript is not None else payload.transcript,
        product_context=payload.product_context,
        product=payload.product,
        source_material=source_material,
        override=payload.product_relevance_override,
    )


def _product_requirements(
    payload: AnalyzeRequest | None,
    relevance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if payload is None:
        return build_product_requirements(relevance=relevance)
    return build_product_requirements(
        product=payload.product,
        product_context=payload.product_context,
        relevance=relevance,
    )


def _is_product_gap(value: Any) -> bool:
    text = str(value or "").lower()
    return any(marker.casefold() in text for marker in _PRODUCT_GAP_MARKERS)


def _optional_enhancements(
    source: dict[str, Any] | None = None,
    extra: list[Any] | None = None,
) -> list[str]:
    source = source if isinstance(source, dict) else {}
    optional: list[str] = []
    metrics = source.get("metrics")
    metrics_ready = isinstance(metrics, dict) and any(
        metrics.get(key) is not None and metrics.get(key) != ""
        for key in ("views", "likes", "comments", "favorites", "shares")
    )
    if not metrics_ready:
        optional.append("实时公开指标")

    comment_summary = source.get("public_comment_summary")
    raw_comments = (
        isinstance(comment_summary, dict)
        and comment_summary.get("raw_comments_included") is True
    )
    if not raw_comments:
        optional.append("公开评论原文")

    evidence = source.get("evidence")
    evidence_text = json.dumps(evidence, ensure_ascii=False) if evidence else ""
    if not any(marker in evidence_text.lower() for marker in ("ocr", "镜头结构")):
        optional.append("画面 OCR 与镜头结构分析")

    for item in extra or []:
        value = str(item).strip()
        if value and is_optional_enhancement(value):
            optional.append(
                "公开评论原文" if "评论" in value else
                "画面 OCR 与镜头结构分析" if "ocr" in value.lower() or "镜头结构" in value
                else "实时公开指标"
            )
    return list(dict.fromkeys(optional))


def _requirements_snapshot(
    *,
    product_relevance: dict[str, Any],
    product_requirements: dict[str, Any],
    blocking: list[Any] | None = None,
    optional: list[Any] | None = None,
    distillation_complete: bool = False,
) -> dict[str, Any]:
    blocking_items = [
        str(item).strip()
        for item in (blocking or [])
        if str(item).strip()
    ]
    if distillation_complete:
        blocking_items = [
            item for item in blocking_items if item != "经模型或人工完成的内容蒸馏"
        ]
    status = str(product_relevance.get("status") or "needs_confirmation")
    product_publish_fields = (
        list(product_requirements.get("missing_fields", []))
        if status == "has_product"
        else []
    )
    return {
        "blocking_for_interpretation": list(dict.fromkeys(blocking_items)),
        "optional_enhancements": list(dict.fromkeys(str(item) for item in (optional or []))),
        "product_for_rewrite_or_publish": product_publish_fields,
        "product_status": status,
        "interpretation_blocked_by_product": False,
    }


def _sync_requirement_fields(
    report: dict[str, Any],
    *,
    product_relevance: dict[str, Any],
    product_requirements: dict[str, Any],
    blocking: list[Any] | None = None,
    optional: list[Any] | None = None,
    distillation_complete: bool = False,
) -> None:
    report["product_relevance"] = product_relevance
    report["product_requirements"] = product_requirements
    report["requirements"] = _requirements_snapshot(
        product_relevance=product_relevance,
        product_requirements=product_requirements,
        blocking=blocking,
        optional=optional,
        distillation_complete=distillation_complete,
    )
    evidence_and_risk = report.get("evidence_and_risk")
    if isinstance(evidence_and_risk, dict):
        evidence_and_risk["product_verification"] = product_requirements.get(
            "verification_status"
        )
    material = report.get("material")
    if isinstance(material, dict):
        material["product_relevance_status"] = product_relevance.get("status")


def _asr_state(payload: AnalyzeRequest | None, transcript_supplied: bool) -> dict[str, Any]:
    mode = payload.asr.mode if payload else "auto"
    return asr_router.plan(mode=mode, transcript_supplied=transcript_supplied)


def _shooting_table_from_fixture(
    report: dict[str, Any],
    product_relevance: dict[str, Any],
) -> dict[str, Any]:
    script = report.get("content_package", {}).get("script", {})
    has_product = product_relevance.get("status") == "has_product"
    rows = []
    for segment in script.get("segments", []):
        row = {
            "time": segment.get("time"),
            "visual": segment.get("visual"),
            "voiceover": segment.get("voiceover"),
            "subtitle": segment.get("screen_text"),
            "sound": "[待定：现场声、配乐或音效]",
            "purpose": segment.get("purpose"),
        }
        if has_product:
            row["product_proof"] = "[待补：该镜头对应的商品事实或证明材料]"
        rows.append(row)
    columns = ["time", "visual", "voiceover", "subtitle"]
    if has_product:
        columns.append("product_proof")
    columns.append("sound")
    return {
        "status": "research_draft",
        "columns": columns,
        "rows": rows,
        "missing_fields": ["product_proof", "sound"] if has_product else ["sound"],
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
    legacy_source = legacy.get("source", {})
    legacy_boundary = legacy.get("evidence_boundary")
    boundary_signals = (
        {
            "facts": legacy_boundary.get("facts", []),
            "inferences": legacy_boundary.get("inferences", []),
        }
        if isinstance(legacy_boundary, dict)
        else None
    )
    source_material = {
        "title": legacy.get("title"),
        "content": legacy_source.get("content") if isinstance(legacy_source, dict) else None,
        "public_comment_summary": (
            legacy_source.get("public_comment_summary")
            if isinstance(legacy_source, dict)
            else None
        ),
        "evidence_boundary": boundary_signals,
    }
    product_relevance = _product_relevance(
        payload,
        source_material=source_material,
    )
    product_requirements = _product_requirements(payload, product_relevance)
    content_package = legacy.get("content_package", {})
    script = content_package.get("script", {})
    risk_gate = deepcopy(legacy.get("risk_gate", {}))

    source_missing = (
        list(legacy_source.get("missing", []))
        if isinstance(legacy_source, dict)
        else []
    )
    product_source_gaps = [
        str(item) for item in source_missing if _is_product_gap(item)
    ]
    optional_enhancements = _optional_enhancements(legacy_source, source_missing)
    reviewed_source_gaps = [
        str(item)
        for item in source_missing
        if not _is_product_gap(item) and not is_optional_enhancement(item)
    ]
    optional_enhancements = list(
        dict.fromkeys([*optional_enhancements, *reviewed_source_gaps])
    )
    interpretation_blocking: list[str] = []
    if product_relevance["status"] == "has_product" and product_source_gaps:
        product_requirements["source_evidence_gaps"] = product_source_gaps
        product_requirements["follow_up"] = list(
            dict.fromkeys(
                [
                    *product_requirements.get("follow_up", []),
                    *product_source_gaps,
                ]
            )
        )
    if isinstance(legacy_source, dict):
        legacy_source["missing"] = interpretation_blocking
    if product_relevance["status"] == "no_product":
        boundary = legacy.get("evidence_boundary")
        if isinstance(boundary, dict):
            boundary["pending"] = [
                item
                for item in boundary.get("pending", [])
                if not _is_product_gap(item)
            ]

    fixture_customer_facts = legacy.get("evidence_boundary", {}).get("customer_facts", [])
    fixture_product_missing = any(
        "产品" in str(item) or "商品" in str(item)
        for item in source_missing
    )
    request_changes_product = bool(payload and (payload.product or payload.product_context))
    product_evidence_ready = (
        product_relevance["status"] == "no_product"
        or (bool(fixture_customer_facts) and not fixture_product_missing)
    )
    source_gate_publishable = bool(risk_gate.get("publishable", False))
    publishable = source_gate_publishable and product_evidence_ready
    publishable = publishable and not request_changes_product
    if not publishable:
        risk_gate["publishable"] = False
    if source_gate_publishable and not publishable:
        risk_gate["status"] = "needs_human_review"
        risk_gate["message"] = (
            "内容方案已生成；商品证据和补充资料完成复核后即可进入发布确认。"
            if product_relevance["status"] == "has_product"
            else "内容方案已生成；发布前完成来源与风险复核后即可进入发布确认。"
        )
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
                if product_relevance["status"] == "has_product"
                else "这是唯一推荐研究稿；普通解读无需商品资料，发布前仍需人工复核。"
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
        "shooting_table": _shooting_table_from_fixture(legacy, product_relevance),
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
        "product_relevance": product_relevance,
        "product_requirements": product_requirements,
        "requirements": _requirements_snapshot(
            product_relevance=product_relevance,
            product_requirements=product_requirements,
            blocking=interpretation_blocking,
            optional=optional_enhancements,
            distillation_complete=True,
        ),
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
    _sync_requirement_fields(
        preferred,
        product_relevance=product_relevance,
        product_requirements=product_requirements,
        blocking=interpretation_blocking,
        optional=optional_enhancements,
        distillation_complete=True,
    )
    return preferred


def _completed_response(
    case: dict[str, Any],
    payload: AnalyzeRequest | None = None,
) -> AnalysisResponse:
    report = _fixture_report_v02(case["report"], payload)
    source = report.get("source", {})
    missing = list(
        report.get("requirements", {}).get("blocking_for_interpretation", [])
    )
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

    product_relevance = _product_relevance(payload, transcript=transcript)
    product_requirements = _product_requirements(payload, product_relevance)
    missing = ["经模型或人工完成的内容蒸馏"]
    optional_enhancements = _optional_enhancements()
    product_blocking = (
        list(product_requirements.get("missing_fields", []))
        if product_relevance["status"] == "has_product"
        else []
    )
    risk_gate = {
        "status": "not_run",
        "publishable": False,
        "message": (
            "这是可继续改编的研究稿；商品事实与行业风险仍需在发布前完成复核。"
            if product_relevance["status"] == "has_product"
            else "这是可继续改编的研究稿；普通解读无需商品资料，发布前仍需完成来源与风险复核。"
        ),
        "blocking_items": [*missing, *product_blocking],
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
    pending = [*missing, *product_blocking]
    columns = ["time", "visual", "voiceover", "subtitle"]
    if product_relevance["status"] == "has_product":
        columns.append("product_proof")
    columns.append("sound")
    shooting_missing = ["time", "visual", "voiceover", "subtitle", "sound"]
    if product_relevance["status"] == "has_product":
        shooting_missing.insert(-1, "product_proof")

    report = {
        "report_schema_version": "0.2",
        "quick_result": None,
        "delivery": {
            "status": "research_draft",
            "publishable": False,
            "label": "研究稿",
            "message": (
                "资料已入库，但尚不足以生成可发布成稿。"
                if product_relevance["status"] == "has_product"
                else "资料已入库；当前先完成内容解读，不需要商品资料。"
            ),
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
            "columns": columns,
            "rows": [],
            "missing_fields": shooting_missing,
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
        "product_relevance": product_relevance,
        "product_requirements": product_requirements,
        "requirements": _requirements_snapshot(
            product_relevance=product_relevance,
            product_requirements=product_requirements,
            blocking=missing,
            optional=optional_enhancements,
        ),
        "evidence_and_risk": {
            "source_evidence": source["evidence"],
            "evidence_boundary": {
                "level": "partial",
                "facts": [
                    "用户提交了公开链接。",
                    f"用户补充了 {len(transcript)} 个字符的字幕或口播稿。",
                ],
                "inferences": [],
                "pending": pending,
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
            "product_relevance_status": product_relevance["status"],
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
    product_status = report["product_relevance"]["status"]
    return AnalysisResponse(
        status="partial",
        platform=platform,
        message=(
            "已建立字幕证据记录和 v0.2 研究稿骨架；当前未执行深度模型分析，"
            + (
                "不会伪造蒸馏、脚本、商品事实或合规结论。"
                if product_status == "has_product"
                else "不会伪造蒸馏、脚本或合规结论；普通解读不要求商品资料。"
            )
        ),
        source=report["source"],
        report=report,
        missing=missing,
        next_action={
            "type": "human_or_model_analysis",
            "label": (
                "按需补齐商品资料并进入受控深度分析"
                if product_status == "has_product"
                else "进入受控深度分析"
            ),
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
    rule_relevance = report.get("product_relevance")
    if not isinstance(rule_relevance, dict):
        rule_relevance = infer_product_relevance()
    product_relevance = merge_product_relevance(
        rule_relevance,
        data.get("product_relevance"),
    )
    existing_product_requirements = report.get("product_requirements", {})
    submitted = (
        existing_product_requirements.get("submitted", {})
        if isinstance(existing_product_requirements, dict)
        else {}
    )
    product_requirements = build_product_requirements(
        product=submitted.get("structured") if isinstance(submitted, dict) else None,
        product_context=submitted.get("legacy_context")
        if isinstance(submitted, dict)
        else None,
        relevance=product_relevance,
    )
    recommended = dict(data["recommended_script"])
    recommended.update(
        {
            "status": "research_draft",
            "is_primary": True,
            "publishable": False,
            "source_basis": (
                "verified_transcript_and_client_product_input"
                if product_relevance["status"] == "has_product"
                else "verified_transcript"
            ),
        }
    )
    rows = [dict(row) for row in data["shooting_table"]]
    if product_relevance["status"] != "has_product":
        for row in rows:
            row.pop("product_proof", None)
    publishing = dict(data["publishing_package"])
    publishing.update({"status": "research_draft", "publishable": False})

    report["delivery"] = {
        "status": "research_draft",
        "publishable": False,
        "label": "唯一推荐研究稿",
        "message": (
            "DeepSeek 已生成研究稿；商品事实与人工审核仍需在发布前完成。"
            if product_relevance["status"] == "has_product"
            else "DeepSeek 已生成研究稿；普通解读无需商品资料，发布前仍需人工复核。"
        ),
    }
    report["recommended_script"] = recommended
    columns = ["time", "visual", "voiceover", "subtitle"]
    if product_relevance["status"] == "has_product":
        columns.append("product_proof")
    columns.append("sound")
    report["shooting_table"] = {
        "status": "research_draft",
        "columns": columns,
        "rows": rows,
        "missing_fields": product_requirements.get("missing_fields", []),
    }
    report["publishing_package"] = publishing
    distillation = dict(data.get("marketing_structure") or {})
    if product_relevance["status"] != "has_product" and "product_demo" in distillation:
        distillation["content_demonstration"] = distillation.pop("product_demo")
    report["distillation"] = distillation
    _sync_requirement_fields(
        report,
        product_relevance=product_relevance,
        product_requirements=product_requirements,
        blocking=report.get("requirements", {}).get(
            "blocking_for_interpretation", []
        ),
        optional=report.get("requirements", {}).get("optional_enhancements", []),
        distillation_complete=True,
    )
    evidence_boundary = data.get("evidence_boundary")
    if isinstance(evidence_boundary, dict):
        evidence_boundary = dict(evidence_boundary)
        if product_relevance["status"] != "has_product":
            evidence_boundary["pending"] = [
                item
                for item in evidence_boundary.get("pending", [])
                if not _is_product_gap(item)
            ]
        report["evidence_and_risk"]["generated_evidence_boundary"] = evidence_boundary
    response.diagnostics["generation"] = {
        "status": "completed_research_draft",
        "provider": generated.provider,
        "model": generated.model,
        "paid_api_called": True,
        "publishable": False,
        "provider_metadata": generated.provider_metadata,
        "message": "内容生成完成；服务器已强制保留研究稿与发布前人工审核。",
    }
    report["risk_gate"]["blocking_items"] = product_requirements.get(
        "missing_fields", []
    )
    report["risk_gate"]["publishable"] = False
    report["evidence_and_risk"]["risk_gate"]["publishable"] = False
    response.missing = list(
        report["requirements"]["blocking_for_interpretation"]
    )
    response.message = (
        "已根据字幕和商品资料生成唯一推荐研究稿；商品事实核验与人工审核完成后即可进入发布确认。"
        if product_relevance["status"] == "has_product"
        else "已生成唯一推荐研究稿；当前内容不需要商品资料，发布前仍需完成来源与风险复核。"
    )
    response.next_action = {
        "type": "review_research_draft",
        "label": (
            "核对商品事实、拍摄表与待核验项"
            if product_relevance["status"] == "has_product"
            else "核对拍摄表与发布前审核项"
        ),
    }
    return response


def _apply_generated_quick_result(
    response: AnalysisResponse,
    generated: ContentGenerationResult,
) -> AnalysisResponse:
    report = response.report
    if report is None:
        return response
    quick_data = dict(generated.data)
    rule_relevance = report.get("product_relevance")
    if not isinstance(rule_relevance, dict):
        rule_relevance = infer_product_relevance()
    product_relevance = merge_product_relevance(
        rule_relevance,
        quick_data.pop("product_relevance", None),
    )
    existing_product_requirements = report.get("product_requirements", {})
    submitted = (
        existing_product_requirements.get("submitted", {})
        if isinstance(existing_product_requirements, dict)
        else {}
    )
    product_requirements = build_product_requirements(
        product=submitted.get("structured") if isinstance(submitted, dict) else None,
        product_context=submitted.get("legacy_context")
        if isinstance(submitted, dict)
        else None,
        relevance=product_relevance,
    )
    quick_evidence = quick_data.get("evidence_boundary")
    if (
        product_relevance["status"] != "has_product"
        and isinstance(quick_evidence, dict)
    ):
        quick_evidence = dict(quick_evidence)
        quick_evidence["pending"] = [
            item
            for item in quick_evidence.get("pending", [])
            if not _is_product_gap(item)
        ]
        quick_data["evidence_boundary"] = quick_evidence
    report["quick_result"] = quick_data
    _sync_requirement_fields(
        report,
        product_relevance=product_relevance,
        product_requirements=product_requirements,
        blocking=report.get("requirements", {}).get(
            "blocking_for_interpretation", []
        ),
        optional=report.get("requirements", {}).get("optional_enhancements", []),
        distillation_complete=True,
    )
    response.diagnostics["generation"] = {
        "status": "completed_quick",
        "provider": generated.provider,
        "model": generated.model,
        "paid_api_called": True,
        "publishable": False,
        "provider_metadata": generated.provider_metadata,
        "message": "快速解读已完成；完整脚本和发布包可按需继续生成。",
    }
    response.missing = list(report["requirements"]["blocking_for_interpretation"])
    response.message = (
        "已先生成快速解读；需要完整脚本时可继续补充商品资料。"
        if product_relevance["status"] == "has_product"
        else "已先生成快速解读；当前内容不需要商品资料。"
    )
    response.next_action = {
        "type": "generate_full_package",
        "label": (
            "补充商品资料并生成完整脚本"
            if product_relevance["status"] == "has_product"
            else "生成完整脚本"
        ),
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


def _visual_unavailable(reason_code: str, message: str) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "status": "unavailable",
        "message": message,
        "probe": {},
        "scene_structure": {
            "status": "unavailable",
            "reason_code": reason_code,
        },
        "frames": [],
        "ocr": {
            "status": "unavailable",
            "provider": None,
            "reason_code": "engine_not_installed",
            "message": "本机未安装本地 OCR 引擎，未生成画面文字。",
        },
        "vision": {
            "status": "unavailable",
            "provider": None,
            "reason_code": "visual_analysis_unavailable",
            "message": "当前没有可供本地视觉模型分析的已验证代表帧。",
            "observations": [],
            "possible_inferences": [],
            "limitations": [],
        },
    }


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _public_visual_analysis(result: dict[str, Any]) -> dict[str, Any]:
    probe = result.get("probe") if isinstance(result.get("probe"), dict) else {}
    scene = (
        result.get("scene_structure")
        if isinstance(result.get("scene_structure"), dict)
        else {}
    )
    ocr = result.get("ocr") if isinstance(result.get("ocr"), dict) else {}
    vision = result.get("vision") if isinstance(result.get("vision"), dict) else {}
    frames: list[dict[str, Any]] = []
    raw_frames = result.get("frames")
    for frame in raw_frames if isinstance(raw_frames, list) else []:
        if not isinstance(frame, dict):
            continue
        artifact_url = str(frame.get("artifact_url") or "")
        if not artifact_url.startswith("/api/acquisition/jobs/"):
            continue
        frames.append(
            {
                "frame_id": frame.get("frame_id") or frame.get("artifact_name"),
                "timestamp_seconds": frame.get("timestamp_seconds"),
                "reason": frame.get("reason"),
                "scene_score": frame.get("scene_score"),
                "artifact_url": artifact_url,
            }
        )
    raw_cuts = scene.get("cuts")
    cuts = [
        {
            "timestamp_seconds": item.get("timestamp_seconds"),
            "score": item.get("score"),
        }
        for item in (raw_cuts if isinstance(raw_cuts, list) else [])[:120]
        if isinstance(item, dict)
    ]
    scene_status = str(scene.get("status") or "unavailable")
    ocr_status = str(ocr.get("status") or "unavailable")
    ocr_blocks: list[dict[str, Any]] = []
    raw_blocks = ocr.get("blocks")
    for block in (raw_blocks if isinstance(raw_blocks, list) else [])[:500]:
        if not isinstance(block, dict):
            continue
        frame_refs = [
            {
                key: reference.get(key)
                for key in ("frame_id", "timestamp_seconds", "box", "confidence")
                if reference.get(key) is not None
            }
            for reference in (
                block.get("frame_refs")
                if isinstance(block.get("frame_refs"), list)
                else []
            )[:100]
            if isinstance(reference, dict)
        ]
        ocr_blocks.append(
            {
                key: block.get(key)
                for key in (
                    "frame_id",
                    "last_frame_id",
                    "timestamp_seconds",
                    "first_seen_seconds",
                    "last_seen_seconds",
                    "text",
                    "box",
                    "confidence",
                    "provider",
                    "model_version",
                )
                if block.get(key) is not None
            }
            | {"frame_refs": frame_refs}
        )
    frame_urls = {
        str(frame.get("frame_id") or ""): str(frame.get("artifact_url") or "")
        for frame in frames
    }

    def public_vision_items(key: str, evidence_state: str) -> list[dict[str, Any]]:
        raw_items = vision.get(key)
        public_items: list[dict[str, Any]] = []
        for item in (raw_items if isinstance(raw_items, list) else [])[:100]:
            if not isinstance(item, dict):
                continue
            frame_id = str(item.get("frame_id") or "")
            artifact_url = frame_urls.get(frame_id, "")
            description = str(item.get("description") or "").strip()
            if not artifact_url.startswith("/api/acquisition/jobs/") or not description:
                continue
            public_items.append(
                {
                    key_name: item.get(key_name)
                    for key_name in (
                        "frame_id",
                        "timestamp_seconds",
                        "category",
                        "description",
                        "confidence",
                        "provider",
                        "model_version",
                        "evidence_type",
                    )
                    if item.get(key_name) is not None
                }
                | {"evidence_state": evidence_state, "artifact_url": artifact_url}
            )
        return public_items

    vision_observations = public_vision_items("observations", "observed")
    vision_inferences = public_vision_items("possible_inferences", "inferred")
    vision_status = str(vision.get("status") or "unavailable")
    if (
        scene_status == "completed"
        and ocr_status == "completed"
        and vision_status == "completed"
    ):
        message = "已在本机完成代表帧、候选镜头切点、画面文字与精选帧语义分析。"
    elif scene_status == "completed" and ocr_status == "completed":
        message = "已在本机完成代表帧、候选镜头切点和画面文字识别；画面语义当前不可用。"
    elif scene_status == "completed":
        message = (
            "已在本机完成代表帧提取与候选镜头切点估算；"
            "镜头节奏是机器启发式结果，OCR 当前不可用。"
        )
    else:
        message = str(result.get("message") or "画面结构分析暂不可用。")
    return {
        "schema_version": str(result.get("schema_version") or "1.0"),
        "status": str(result.get("status") or "unavailable"),
        "message": message,
        "probe": {
            key: probe.get(key)
            for key in (
                "duration_seconds",
                "coverage_seconds",
                "truncated",
                "width",
                "height",
                "fps",
            )
            if probe.get(key) is not None
        },
        "scene_structure": {
            key: scene.get(key)
            for key in (
                "status",
                "method",
                "candidate_cut_count",
                "estimated_segment_count",
                "estimated_average_segment_seconds",
                "cuts_per_minute",
                "pace",
                "pace_is_heuristic",
                "cuts_truncated",
                "reason_code",
            )
            if scene.get(key) is not None
        }
        | {"cuts": cuts},
        "frames": frames,
        "frame_count": len(frames),
        "ocr": {
            key: ocr.get(key)
            for key in (
                "status",
                "provider",
                "model_version",
                "reason_code",
                "message",
                "frame_count",
                "block_count",
                "limitations",
            )
            if ocr.get(key) is not None
        }
        | {"blocks": ocr_blocks},
        "vision": {
            key: vision.get(key)
            for key in (
                "status",
                "provider",
                "model_version",
                "reason_code",
                "message",
                "frame_count",
                "successful_frame_count",
                "observation_count",
                "inference_count",
                "limitations",
            )
            if vision.get(key) is not None
        }
        | {
            "observations": vision_observations,
            "possible_inferences": vision_inferences,
        },
        "cache_hit": result.get("cache_hit") is True,
    }


def _visual_analysis_for_job(
    job_id: str,
    manifest: dict[str, Any],
    *,
    public: bool = True,
) -> dict[str, Any]:
    artifacts = manifest.get("raw_artifacts")
    source_media = next(
        (
            item
            for item in artifacts
            if isinstance(item, dict)
            and item.get("role") == "source_media"
            and str(item.get("content_type") or "").startswith("video/")
        ),
        None,
    ) if isinstance(artifacts, list) else None
    if not isinstance(source_media, dict):
        return _visual_unavailable(
            "source_media_missing",
            "当前任务没有已登记的视频媒体，未执行抽帧或镜头结构估算。",
        )
    artifact_name = str(source_media.get("name") or "")
    expected_sha256 = str(source_media.get("sha256") or "").lower()
    try:
        declared_size = int(source_media.get("size_bytes") or 0)
    except (TypeError, ValueError):
        return _visual_unavailable(
            "source_size_invalid",
            "来源视频缺少有效大小记录，未执行画面分析。",
        )
    if declared_size > MAX_VISUAL_MEDIA_BYTES:
        return _visual_unavailable(
            "source_media_too_large",
            "来源视频超过 512 MiB 的本机画面分析上限。",
        )
    if not re.fullmatch(r"[a-f0-9]{64}", expected_sha256):
        return _visual_unavailable(
            "source_hash_missing",
            "来源视频缺少有效校验值，未执行画面分析。",
        )
    try:
        media_path = acquisition_jobs.store.artifact_path(job_id, artifact_name)
        actual_size = media_path.stat().st_size
        if actual_size <= 0:
            raise VisualAnalysisError("来源视频为空，未执行画面分析。")
        if actual_size > MAX_VISUAL_MEDIA_BYTES:
            raise VisualAnalysisError(
                "来源视频超过 512 MiB 的本机画面分析上限。"
            )
        if _sha256_path(media_path) != expected_sha256:
            raise VisualAnalysisError("来源视频校验失败，未执行画面分析。")
        result = visual_analyzer.analyze(
            media_path,
            expected_sha256,
            acquisition_jobs.store.job_dir(job_id) / "visual_analysis",
            artifact_url_builder=lambda name: (
                f"/api/acquisition/jobs/{job_id}/visual-analysis/artifacts/{name}"
            ),
        )
    except (AcquisitionJobNotFoundError, OSError, ValueError, VisualAnalysisError) as exc:
        return _visual_unavailable("visual_analysis_failed", str(exc))
    return _public_visual_analysis(result) if public else result


def _visual_artifact_path(job_id: str, artifact_name: str) -> Path:
    if not VISUAL_ARTIFACT_NAME_RE.fullmatch(artifact_name):
        raise AcquisitionJobNotFoundError(artifact_name)
    output_dir = (
        acquisition_jobs.store.job_dir(job_id) / "visual_analysis"
    ).resolve()
    path = (output_dir / artifact_name).resolve()
    if path.parent != output_dir or not path.is_file() or path.stat().st_size <= 0:
        raise AcquisitionJobNotFoundError(artifact_name)
    return path


def _attach_acquisition_context(
    response: AnalysisResponse,
    *,
    status: dict[str, Any],
    manifest: dict[str, Any],
    item: dict[str, Any],
    transcript_artifact: dict[str, Any],
    visual_analysis: dict[str, Any] | None = None,
) -> AnalysisResponse:
    visual_analysis = visual_analysis if isinstance(visual_analysis, dict) else None
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
        response.diagnostics["acquisition"] = acquisition_context
        if response.report is not None:
            response.report["source"] = response.source
            if visual_analysis is not None:
                response.report["visual_analysis"] = visual_analysis
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
    scene_ready = (
        isinstance(visual_analysis, dict)
        and isinstance(visual_analysis.get("scene_structure"), dict)
        and visual_analysis["scene_structure"].get("status") == "completed"
    )
    ocr_ready = (
        isinstance(visual_analysis, dict)
        and isinstance(visual_analysis.get("ocr"), dict)
        and visual_analysis["ocr"].get("status") == "completed"
    )
    if scene_ready:
        if ocr_ready:
            manifest_missing = [
                missing
                for missing in manifest_missing
                if "ocr" not in str(missing).lower() and "镜头结构" not in str(missing)
            ]
        else:
            manifest_missing = [
                "画面文字 OCR（本机未安装 OCR 引擎）"
                if "ocr" in str(missing).lower() or "镜头结构" in str(missing)
                else missing
                for missing in manifest_missing
            ]
    metrics = item.get("metrics") if isinstance(item.get("metrics"), dict) else {}
    analysis_missing = list(response.missing)
    if any(value is not None for value in metrics.values()):
        analysis_missing = [
            missing for missing in analysis_missing if missing != "实时公开指标"
        ]
    optional_enhancements = _optional_enhancements(
        {
            "metrics": metrics,
            "public_comment_summary": item.get("public_comment_summary"),
            "evidence": item.get("evidence", []),
        },
        manifest_missing,
    )
    if scene_ready:
        optional_enhancements = list(
            dict.fromkeys(
                str(item)
                for item in optional_enhancements
                if not ocr_ready
                or ("ocr" not in str(item).lower() and "镜头结构" not in str(item))
            )
        )
        if not ocr_ready:
            optional_enhancements = [
                "画面文字 OCR（本机未安装 OCR 引擎）"
                if "ocr" in str(item).lower() or "镜头结构" in str(item)
                else str(item)
                for item in optional_enhancements
            ]
    hard_manifest_missing = [
        str(item)
        for item in manifest_missing
        if not is_optional_enhancement(item) and not _is_product_gap(item)
    ]
    combined_missing = list(
        dict.fromkeys([*hard_manifest_missing, *analysis_missing])
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
    response.diagnostics["acquisition"] = acquisition_context

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
    if scene_ready and isinstance(visual_analysis, dict):
        scene = visual_analysis["scene_structure"]
        probe = visual_analysis.get("probe", {})
        source["evidence"].append(
            {
                "type": "frame_and_shot_structure",
                "label": "本机抽帧与镜头结构估算",
                "value": {
                    "frame_count": visual_analysis.get("frame_count"),
                    "candidate_cut_count": scene.get("candidate_cut_count"),
                    "coverage_seconds": probe.get("coverage_seconds"),
                    "truncated": probe.get("truncated"),
                    "pace": scene.get("pace"),
                },
                "confidence": "runtime_generated_heuristic",
            }
        )
        if ocr_ready:
            ocr_evidence = visual_analysis.get("ocr", {})
            source["evidence"].append(
                {
                    "type": "on_screen_text_ocr",
                    "label": "本机画面文字识别",
                    "value": {
                        "frame_count": ocr_evidence.get("frame_count"),
                        "block_count": ocr_evidence.get("block_count"),
                        "provider": ocr_evidence.get("provider"),
                        "model_version": ocr_evidence.get("model_version"),
                    },
                    "confidence": "runtime_generated_with_per_block_scores",
                }
            )
    source["missing"] = combined_missing
    response.missing = combined_missing
    response.message = "采集任务已自动进入内容分析。" + response.message

    report = response.report
    if report is None:
        return response
    report["source"] = source
    if visual_analysis is not None:
        report["visual_analysis"] = visual_analysis
    existing_requirements = report.get("requirements", {})
    existing_relevance = report.get("product_relevance")
    if not isinstance(existing_relevance, dict):
        existing_relevance = infer_product_relevance()
    existing_product_requirements = report.get("product_requirements")
    if not isinstance(existing_product_requirements, dict):
        existing_product_requirements = build_product_requirements(
            relevance=existing_relevance
        )
    existing_optional = (
        list(existing_requirements.get("optional_enhancements", []))
        if isinstance(existing_requirements, dict)
        else []
    )
    if scene_ready:
        if ocr_ready:
            existing_optional = [
                item
                for item in existing_optional
                if "ocr" not in str(item).lower() and "镜头结构" not in str(item)
            ]
        else:
            existing_optional = [
                "画面文字 OCR（本机未安装 OCR 引擎）"
                if "ocr" in str(item).lower() or "镜头结构" in str(item)
                else str(item)
                for item in existing_optional
            ]
    _sync_requirement_fields(
        report,
        product_relevance=existing_relevance,
        product_requirements=existing_product_requirements,
        blocking=combined_missing,
        optional=[
            *existing_optional,
            *optional_enhancements,
        ],
        distillation_complete=bool(report.get("distillation") or report.get("quick_result")),
    )
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
        risk_gate["blocking_items"] = list(
            dict.fromkeys(
                [
                    *combined_missing,
                    *report.get("requirements", {}).get(
                        "product_for_rewrite_or_publish", []
                    ),
                ]
            )
        )
    return response


def _verified_full_transcript(
    job_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    _, manifest, _, transcript_artifact = _acquisition_analysis_material(job_id)
    if not transcript_artifact or transcript_artifact.get("name") != "transcript.json":
        raise HTTPException(
            status_code=422,
            detail="当前任务没有已登记的完整口播文件。",
        )
    try:
        path = acquisition_jobs.store.artifact_path(job_id, "transcript.json")
        record = acquisition_jobs.store.artifact_record(job_id, "transcript.json")
        expected_size = int(
            transcript_artifact.get("size_bytes") or record.get("size_bytes") or 0
        )
        transcript = read_verified_transcript(
            path,
            expected_sha256=str(transcript_artifact["sha256"]),
            expected_size_bytes=expected_size,
        )
    except AcquisitionJobNotFoundError as exc:
        raise HTTPException(status_code=404, detail="完整口播文件不存在。") from exc
    except (TypeError, ValueError, FullContentError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return manifest, transcript


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


@app.post("/api/acquisition/jobs/{job_id}/visual-analysis")
def analyze_acquisition_visuals(job_id: str) -> dict[str, Any]:
    """对已登记来源视频做本机抽帧与候选镜头结构估算。"""
    try:
        status = acquisition_jobs.store.status(job_id)
        manifest = acquisition_jobs.store.manifest(job_id)
    except AcquisitionJobNotFoundError as exc:
        raise HTTPException(status_code=404, detail="采集任务不存在或尚无清单。") from exc
    if status.get("status") != "completed" or manifest.get("status") != "completed":
        raise HTTPException(status_code=409, detail="采集任务尚未完成，不能分析画面。")
    return _visual_analysis_for_job(job_id, manifest)


@app.get(
    "/api/acquisition/jobs/{job_id}/visual-analysis/artifacts/{artifact_name}",
    response_model=None,
)
def acquisition_visual_artifact(job_id: str, artifact_name: str) -> FileResponse:
    try:
        path = _visual_artifact_path(job_id, artifact_name)
    except AcquisitionJobNotFoundError as exc:
        raise HTTPException(status_code=404, detail="画面分析文件不存在。") from exc
    media_type = "application/json" if path.suffix.lower() == ".json" else "image/jpeg"
    return FileResponse(path, media_type=media_type)


@app.get("/api/acquisition/jobs/{job_id}/full-content/transcript")
def acquisition_full_transcript(
    job_id: str,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
) -> dict[str, Any]:
    _, transcript = _verified_full_transcript(job_id)
    return paginated_response(
        "transcript",
        transcript["segments"],
        offset=offset,
        limit=limit,
        metadata={
            key: transcript.get(key)
            for key in (
                "character_count",
                "segment_count",
                "source",
                "provider",
                "model",
                "language",
            )
            if transcript.get(key) is not None
        },
    )


@app.get("/api/acquisition/jobs/{job_id}/full-content/transcript-text")
def acquisition_full_transcript_text(job_id: str) -> dict[str, Any]:
    _, transcript = _verified_full_transcript(job_id)
    return {
        "schema_version": "project024-full-content/v1",
        "section": "transcript_text",
        "status": "completed",
        "character_count": transcript["character_count"],
        "segment_count": transcript["segment_count"],
        "text": transcript["text"],
    }


@app.get("/api/acquisition/jobs/{job_id}/full-content/ocr")
def acquisition_full_ocr(
    job_id: str,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
) -> dict[str, Any]:
    manifest, _ = _verified_full_transcript(job_id)
    visual = _visual_analysis_for_job(job_id, manifest, public=False)
    items = ocr_items(visual)
    ocr = visual.get("ocr") if isinstance(visual.get("ocr"), dict) else {}
    status = str(ocr.get("status") or "unavailable")
    if status != "completed":
        return {
            "schema_version": "project024-full-content/v1",
            "section": "ocr",
            "status": status,
            "message": str(ocr.get("message") or "当前没有可用的画面文字。"),
            "offset": 0,
            "limit": limit,
            "total_items": 0,
            "has_more": False,
            "items": [],
        }
    return paginated_response(
        "ocr",
        items,
        offset=offset,
        limit=limit,
        metadata={
            "frame_count": ocr.get("frame_count"),
            "provider": ocr.get("provider"),
            "model_version": ocr.get("model_version"),
            "limitations": ocr.get("limitations", []),
        },
    )


@app.get("/api/acquisition/jobs/{job_id}/full-content/timeline")
def acquisition_full_timeline(
    job_id: str,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
) -> dict[str, Any]:
    manifest, transcript = _verified_full_transcript(job_id)
    visual = _visual_analysis_for_job(job_id, manifest, public=False)
    items = build_timeline(transcript, visual)
    vision = visual.get("vision") if isinstance(visual.get("vision"), dict) else {}
    return paginated_response(
        "timeline",
        items,
        offset=offset,
        limit=limit,
        metadata={
            "transcript_character_count": transcript["character_count"],
            "transcript_segment_count": transcript["segment_count"],
            "ocr_status": (
                visual.get("ocr", {}).get("status")
                if isinstance(visual.get("ocr"), dict)
                else "unavailable"
            ),
            "vision_status": str(vision.get("status") or "unavailable"),
            "vision_message": str(
                vision.get("message")
                or "多模态画面语义尚未接入；时间线不会用字幕推测画面。"
            ),
        },
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


async def _analyze(
    payload: AnalyzeRequest,
    *,
    visual_evidence: dict[str, Any] | None = None,
) -> AnalysisResponse:
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

    try:
        generation_plan = content_router.plan(payload.analysis_strategy)
    except TypeError as exc:
        # Keep compatibility with the small no-paid test/router adapters.
        if "positional" not in str(exc) and "argument" not in str(exc):
            raise
        generation_plan = content_router.plan()
    response.diagnostics["generation"] = generation_plan
    if payload.analysis_mode == "quick":
        try:
            generated = await content_router.generate_quick(
                strategy=payload.analysis_strategy,
                platform=platform,
                transcript=payload.transcript or "",
                product_context=payload.product_context,
                product=(
                    payload.product.model_dump(mode="json", exclude_none=True)
                    if payload.product
                    else None
                ),
                product_relevance=response.report.get("product_relevance"),
                visual_evidence=visual_evidence,
            )
        except ContentGenerationError as exc:
            response.diagnostics["generation"] = {
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
            strategy=payload.analysis_strategy,
            platform=platform,
            transcript=payload.transcript or "",
            product_context=payload.product_context,
            product=(
                payload.product.model_dump(mode="json", exclude_none=True)
                if payload.product
                else None
            ),
            product_relevance=response.report.get("product_relevance"),
            visual_evidence=visual_evidence,
        )
    except ContentGenerationError as exc:
        response.diagnostics["generation"] = {
            **content_router.plan(),
            "status": "failed_research_draft_fallback",
            "paid_api_called": True,
            "message": str(exc),
        }
        return response
    if generated is None:
        return response
    return _apply_generated_research_draft(response, generated)


@app.post("/api/analyze", response_model=AnalysisResponse)
async def analyze(payload: AnalyzeRequest) -> AnalysisResponse:
    return await _analyze(payload)


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
    visual_analysis = await run_in_threadpool(
        _visual_analysis_for_job,
        job_id,
        manifest,
    )
    transcript = (
        item.get("content", {}).get("transcript")
        if manifest.get("acquisition_mode") != "registered_fixture"
        else None
    )
    analysis_payload = AnalyzeRequest(
        url=str(manifest["canonical_url"]),
        analysis_mode=payload.analysis_mode,
        analysis_strategy=payload.analysis_strategy,
        transcript=str(transcript["text"]) if isinstance(transcript, dict) else None,
        product_context=payload.product_context,
        product=payload.product,
        product_relevance_override=payload.product_relevance_override,
        market=payload.market,
    )
    response = await _analyze(
        analysis_payload,
        visual_evidence=visual_analysis,
    )
    return _attach_acquisition_context(
        response,
        status=status,
        manifest=manifest,
        item=item,
        transcript_artifact=transcript_artifact,
        visual_analysis=visual_analysis,
    )


@app.post("/api/publish/experiments", status_code=201)
def create_publish_experiment(payload: PublishExperimentCreate) -> dict[str, Any]:
    """登记一次即将发布的内容，并写下盲预测（7 维打分 + 指标区间）。"""
    return publish_calibration.create(payload)


@app.get("/api/agent/status")
def operations_agent_status() -> JSONResponse:
    return JSONResponse(operations_agent.plan())


@app.post("/api/agent/chat")
async def operations_agent_chat(payload: OperationsAgentRequest) -> JSONResponse:
    """根据当前页面草稿进行一次可继续迭代的脚本或运营策略对话。"""
    return JSONResponse(await operations_agent.chat(payload))


@app.get("/api/douyin/accounts/connection")
def douyin_account_connection() -> JSONResponse:
    """报告文件导入、本机浏览器导出和官方 OAuth 边界。"""
    return JSONResponse(
        {
            "recommended_path": {
                "key": "creator_export",
                "available": True,
                "label": "创作者中心文件导入",
                "reason": "用户在创作者中心导出 CSV/XLSX 后上传即可；不需要本项目接管登录态。",
            },
            "official_oauth": {
                "status": "requires_platform_setup",
                "available": False,
                "reason": "当前仅作为筹备路径：官方入驻文档将网站/移动应用列在企业身份范围，个人主体仅支持小游戏和小玩法；还需要应用上线、数据权限审核、HTTPS 回调、真实授权和 token 生命周期验收。平台页面若有更新，以当前后台可选主体和审核结果为准。",
                "application_guide": "https://developer.open-douyin.com/docs/resource/zh-CN/dop/develop/app-mgmt/create-mobile-and-web-app",
                "authorization_guide": "https://developer.open-douyin.com/docs/resource/zh-CN/dop/develop/openapi/account-permission/douyin-get-permission-code",
                "openapi_catalog": "https://developer.open-douyin.com/docs/resource/zh-CN/dop/develop/openapi/list",
            },
            "creator_export": {
                "status": "available",
                "available": True,
                "reason": "可导入创作者中心导出的 CSV；不需要账号密码或 Cookie。",
            },
            "local_browser_export": {
                "status": "available",
                "available": True,
                "reason": "用户主动点击后，在本机可见浏览器会话中完成导出；仅把导出文件导入项目，不保存 Cookie 或浏览器 profile。",
            },
            "security": {
                "stores_password": False,
                "stores_cookie": False,
                "stores_browser_session": False,
            },
        }
    )


@app.get("/api/douyin/browser-capabilities")
def douyin_browser_capabilities() -> JSONResponse:
    """返回本机可发现的浏览器；不会读取 Cookie 或返回登录信息。"""
    return JSONResponse({"browsers": list_browsers()})


@app.post("/api/douyin/accounts", status_code=201)
def create_douyin_account(payload: DouyinAccountCreate) -> dict[str, Any]:
    return douyin_accounts.create(payload)


@app.get("/api/douyin/accounts")
def list_douyin_accounts() -> JSONResponse:
    return JSONResponse({"accounts": douyin_accounts.list_all()})


@app.get("/api/douyin/accounts/{account_id}")
def get_douyin_account(account_id: str) -> JSONResponse:
    return JSONResponse(douyin_accounts.get(account_id))


@app.patch("/api/douyin/accounts/{account_id}")
def update_douyin_account(
    account_id: str, payload: DouyinAccountUpdate
) -> JSONResponse:
    return JSONResponse(douyin_accounts.update(account_id, payload))


@app.post("/api/douyin/accounts/{account_id}/imports", status_code=201)
def import_douyin_creator_data(
    account_id: str, payload: CreatorDataImport
) -> dict[str, Any]:
    return douyin_accounts.import_creator_data(account_id, payload)


@app.post("/api/douyin/accounts/{account_id}/browser-import", status_code=201)
async def import_douyin_creator_data_from_browser(
    account_id: str, payload: DouyinBrowserExportRequest
) -> JSONResponse:
    """在本机浏览器会话中导出创作者中心数据，并立即导入标准化指标。"""
    browser_payload, data, filename = await run_in_threadpool(
        export_creator_data,
        browser_id=payload.browser_id,
        profile_mode=payload.profile_mode,
        timeout_seconds=payload.timeout_seconds,
    )
    imported = douyin_accounts.import_creator_data(
        account_id,
        CreatorDataImport(
            filename=filename,
            file_base64=base64.b64encode(data).decode("ascii"),
        ),
    )
    return JSONResponse(
        {
            "browser": browser_payload.get("browser"),
            "browser_label": browser_payload.get("browser_label"),
            "profile_mode": browser_payload.get("profile_mode"),
            "bytes": browser_payload.get("bytes"),
            "temporary_profile_deleted": browser_payload.get("temporary_profile_deleted", False),
            "import": imported,
            "message": "已从本机浏览器导入创作者中心数据；Cookie 未写入项目或数据库。",
        }
    )


@app.post("/api/douyin/accounts/{account_id}/download-import", status_code=201)
def import_latest_douyin_download(
    account_id: str, payload: DouyinDownloadImportRequest
) -> JSONResponse:
    """识别下载文件夹里用户刚下载的 CSV/XLSX 并导入，不读取 Cookie。"""
    result = latest_creator_download(since_epoch_ms=payload.since_epoch_ms)
    if result is None:
        raise HTTPException(status_code=404, detail="尚未发现新的 CSV/XLSX 下载文件。")
    data, filename, modified_ms = result
    imported = douyin_accounts.import_creator_data(
        account_id,
        CreatorDataImport(
            filename=filename,
            file_base64=base64.b64encode(data).decode("ascii"),
        ),
    )
    return JSONResponse(
        {
            "filename": filename,
            "modified_epoch_ms": modified_ms,
            "bytes": len(data),
            "import": imported,
            "message": f"已识别下载文件“{filename}”并导入账号分析。",
        }
    )


@app.get("/api/douyin/accounts/{account_id}/imports")
def list_douyin_creator_imports(account_id: str) -> JSONResponse:
    return JSONResponse({"imports": douyin_accounts.list_imports(account_id)})


@app.get("/api/douyin/accounts/{account_id}/analysis")
def douyin_account_analysis(account_id: str) -> JSONResponse:
    return JSONResponse(douyin_accounts.analysis(account_id))


@app.post("/api/douyin/topics", status_code=201)
def create_douyin_topic(payload: DouyinTopicCreate) -> dict[str, Any]:
    """把抖音分析结果保存为可继续运营的选题。"""
    return douyin_topics.create(payload)


@app.get("/api/douyin/topics")
def list_douyin_topics(
    status: Literal["idea", "draft", "ready"] | None = Query(default=None),
) -> JSONResponse:
    """列出抖音选题；新记录在前，可按状态筛选。"""
    return JSONResponse({"topics": douyin_topics.list_all(status=status)})


@app.get("/api/douyin/topics/{topic_id}")
def get_douyin_topic(topic_id: str) -> JSONResponse:
    return JSONResponse(douyin_topics.get(topic_id))


@app.patch("/api/douyin/topics/{topic_id}")
def update_douyin_topic(
    topic_id: str, payload: DouyinTopicUpdate
) -> JSONResponse:
    """更新抖音选题状态、脚本摘要或实验假设。"""
    return JSONResponse(douyin_topics.update(topic_id, payload))


@app.get("/api/publish/experiments")
def list_publish_experiments() -> JSONResponse:
    """列出全部发布实验，新记录在前，便于复盘与累积。"""
    return JSONResponse({"experiments": publish_calibration.list_all()})


@app.get("/api/publish/calibration-summary")
def publish_calibration_summary() -> JSONResponse:
    """汇总已复盘实验；样本不足时明确标记证据不足。"""
    return JSONResponse(publish_calibration.calibration_summary())


@app.get("/api/publish/experiments/{experiment_id}/events")
def list_publish_experiment_events(experiment_id: str) -> JSONResponse:
    """返回单个实验的不可变事件历史。"""
    publish_calibration.get(experiment_id)
    return JSONResponse(
        {"events": publish_calibration.list_events(experiment_id=experiment_id)}
    )


@app.get("/api/publish/experiments/{experiment_id}")
def get_publish_experiment(experiment_id: str) -> JSONResponse:
    record = publish_calibration.get(experiment_id)
    return JSONResponse(record)


@app.post("/api/publish/experiments/{experiment_id}/publish")
def publish_experiment(
    experiment_id: str, payload: PublishRecordInput
) -> JSONResponse:
    """登记发布动作（平台/日期/链接），进入已发布状态。"""
    record = publish_calibration.publish(experiment_id, payload)
    return JSONResponse(record)


@app.post("/api/publish/experiments/{experiment_id}/backfill")
def backfill_experiment(
    experiment_id: str, payload: PublishBackfillInput
) -> JSONResponse:
    """发布后回填实测指标（曝光/点击/留存/互动/涨粉），进入已测量状态。"""
    record = publish_calibration.backfill(experiment_id, payload)
    return JSONResponse(record)


@app.post("/api/publish/experiments/{experiment_id}/review")
def review_experiment(
    experiment_id: str, payload: PublishReviewInput
) -> JSONResponse:
    """生成偏差与下一轮建议，并标记可沉淀经验（候选017，不自动晋升）。"""
    record = publish_calibration.review(experiment_id, note=payload.note)
    return JSONResponse(record)


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
