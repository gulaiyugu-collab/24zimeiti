from __future__ import annotations

import asyncio
import json
import math
import os
import re
from dataclasses import dataclass
from typing import Any, Callable

import httpx

from .product_relevance import normalize_model_product_relevance


_SECRET_PATTERNS = (
    re.compile(r"(?i)\bBearer\s+[^\s,;]+"),
    re.compile(r"(?i)\b(?:sk|rk|pk)-[a-z0-9_-]{8,}\b"),
    re.compile(
        r"(?i)((?:api[_-]?key|access[_-]?token|token|secret)\s*[:=]\s*[\"']?)"
        r"[^\"',;\s}]+"
    ),
    re.compile(
        r"(?i)([?&](?:api[_-]?key|access[_-]?token|token|secret)=)[^&\s]+"
    ),
)


class ContentGenerationError(RuntimeError):
    """The configured content provider could not return a valid research draft."""


@dataclass(frozen=True)
class ContentGenerationResult:
    data: dict[str, Any]
    provider: str
    model: str
    provider_metadata: dict[str, Any]


_FULL_AGENT_ROLES: tuple[tuple[str, str], ...] = (
    (
        "source_analyst",
        "你是来源结构分析 Agent。只提炼内容结构、观众需求、可迁移方法和关键证据，不写最终脚本。",
    ),
    (
        "evidence_auditor",
        "你是证据与风险审查 Agent。重点核对事实、推断、待确认项、商品属性和发布风险，不写最终脚本。",
    ),
    (
        "originality_editor",
        "你是原创编辑 Agent。寻找不复刻来源的原创切入点、叙事顺序和表达取舍，不写最终脚本。",
    ),
)
_QUICK_AGENT_ROLES: tuple[tuple[str, str], ...] = _FULL_AGENT_ROLES[:2]
_SYNTHESIS_INSTRUCTION = (
    "你是总编合成 Agent。下面的 agent_outputs 只是其他 Agent 的候选意见，"
    "不能替代原始字幕、用户资料和 visual_evidence；事实必须回到原始证据，"
    "冲突意见要保留为推断或待确认项。最终只输出当前接口要求的 JSON。"
)


def _bounded_value(value: Any, *, depth: int = 0) -> Any:
    """Keep fan-in context bounded without changing the source evidence."""
    if depth > 4:
        return "[truncated]"
    if isinstance(value, str):
        return value[:800]
    if isinstance(value, list):
        return [_bounded_value(item, depth=depth + 1) for item in value[:8]]
    if isinstance(value, dict):
        return {
            str(key): _bounded_value(item, depth=depth + 1)
            for key, item in list(value.items())[:24]
        }
    return value


def _bounded_agent_outputs(outputs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [_bounded_value(output) for output in outputs]


def _sum_usage(usages: list[dict[str, int] | None]) -> dict[str, int] | None:
    totals: dict[str, int] = {}
    for usage in usages:
        if not isinstance(usage, dict):
            continue
        for key, value in usage.items():
            if isinstance(value, int) and not isinstance(value, bool):
                totals[key] = totals.get(key, 0) + value
    return totals or None


def _env_float(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError:
        return default
    if not math.isfinite(value):
        return default
    return min(max(value, minimum), maximum)


def _env_text(name: str, default: str) -> str:
    value = os.getenv(name)
    return value.strip() if value and value.strip() else default


def _sanitize_error_text(value: object, *secrets: str | None) -> str:
    message = str(value or "")
    for secret in secrets:
        if secret:
            message = message.replace(secret, "[redacted]")
    for pattern in _SECRET_PATTERNS:
        if pattern.groups:
            message = pattern.sub(r"\1[redacted]", message)
        else:
            message = pattern.sub("[redacted]", message)
    return " ".join(message.split())[:500]


def _deepseek_key() -> str | None:
    value = os.getenv("DEEPSEEK_API_KEY") or os.getenv("PROJECT024_CONTENT_API_KEY")
    value = value.strip() if value else ""
    return value or None


def _deepseek_model() -> str:
    return _env_text(
        "PROJECT024_CONTENT_MODEL", _env_text("DEEPSEEK_MODEL", "deepseek-chat")
    )


def _deepseek_quick_model() -> str:
    return _env_text("PROJECT024_CONTENT_QUICK_MODEL", _deepseek_model())


def _deepseek_endpoint() -> str:
    endpoint = os.getenv("PROJECT024_CONTENT_ENDPOINT")
    if endpoint and endpoint.strip():
        return endpoint.strip().rstrip("/")
    base_url = _env_text(
        "PROJECT024_CONTENT_BASE_URL",
        _env_text("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
    ).rstrip("/")
    if base_url.endswith("/chat/completions"):
        return base_url
    return f"{base_url}/chat/completions"


def _safe_error(response: httpx.Response, secret: str) -> str:
    message = ""
    try:
        payload = response.json()
        if isinstance(payload, dict):
            error = payload.get("error")
            if isinstance(error, dict):
                message = str(error.get("message") or "")
            elif error:
                message = str(error)
    except (TypeError, ValueError):
        message = ""
    message = _sanitize_error_text(message, secret)
    suffix = f"：{message}" if message else ""
    return f"DeepSeek 返回 HTTP {response.status_code}{suffix}"


def _strip_json_fence(value: str) -> str:
    text = value.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


def _required_text(container: dict[str, Any], key: str, scope: str) -> str:
    value = container.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ContentGenerationError(f"DeepSeek 研究稿的 {scope}.{key} 缺失或无效。")
    return value.strip()


def _string_list(value: Any, scope: str, *, allow_empty: bool = True) -> list[str]:
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise ContentGenerationError(f"DeepSeek 研究稿的 {scope} 格式无效。")
    normalized = [item.strip() for item in value]
    if not allow_empty and not normalized:
        raise ContentGenerationError(f"DeepSeek 研究稿的 {scope} 不能为空。")
    return normalized


def _validate_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ContentGenerationError("DeepSeek 没有返回 JSON 对象。")

    summary = _required_text(payload, "summary", "root")
    product_relevance = normalize_model_product_relevance(
        payload.get("product_relevance")
    )

    marketing = payload.get("marketing_structure")
    if not isinstance(marketing, dict):
        raise ContentGenerationError("DeepSeek 研究稿缺少营销结构。")
    normalized_marketing = {
        key: _required_text(marketing, key, "marketing_structure")
        for key in ("hook", "product_demo", "value_proposition", "cta")
    }

    recommended = payload.get("recommended_script")
    if not isinstance(recommended, dict):
        raise ContentGenerationError("DeepSeek 研究稿缺少完整脚本正文。")
    duration = recommended.get("duration_seconds")
    if (
        isinstance(duration, bool)
        or not isinstance(duration, (int, float))
        or not 1 <= duration <= 300
    ):
        raise ContentGenerationError("DeepSeek 研究稿的脚本时长无效。")
    full_text = _required_text(recommended, "full_text", "recommended_script")
    if len(full_text) < 200:
        raise ContentGenerationError(
            "DeepSeek 研究稿的完整脚本少于 200 字，不能视为完整稿。"
        )
    normalized_recommended = {
        "title": _required_text(recommended, "title", "recommended_script"),
        "duration_seconds": duration,
        "full_text": full_text,
        "selection_reason": _required_text(
            recommended, "selection_reason", "recommended_script"
        ),
        "status": "research_draft",
        "publishable": False,
    }

    shooting_rows = payload.get("shooting_table")
    if not isinstance(shooting_rows, list) or not shooting_rows:
        raise ContentGenerationError("DeepSeek 研究稿缺少拍摄执行表。")
    if len(shooting_rows) < 4:
        raise ContentGenerationError("DeepSeek 拍摄执行表少于 4 段，不能视为完整拍摄表。")
    if len(shooting_rows) > 60:
        raise ContentGenerationError("DeepSeek 拍摄执行表超过 60 行限制。")
    normalized_rows: list[dict[str, str]] = []
    for index, item in enumerate(shooting_rows, start=1):
        if not isinstance(item, dict):
            raise ContentGenerationError("DeepSeek 拍摄执行表格式无效。")
        normalized_rows.append(
            {
                key: _required_text(item, key, f"shooting_table[{index}]")
                for key in (
                    "time",
                    "visual",
                    "voiceover",
                    "subtitle",
                    "product_proof",
                    "sound",
                )
            }
        )

    publishing = payload.get("publishing_package")
    if not isinstance(publishing, dict):
        raise ContentGenerationError("DeepSeek 研究稿缺少发布内容包。")
    raw_replies = publishing.get("comment_replies")
    if not isinstance(raw_replies, list):
        raise ContentGenerationError("DeepSeek 研究稿的评论回复格式无效。")
    normalized_replies: list[dict[str, str]] = []
    for index, item in enumerate(raw_replies, start=1):
        if not isinstance(item, dict):
            raise ContentGenerationError("DeepSeek 研究稿的评论回复格式无效。")
        normalized_replies.append(
            {
                "question": _required_text(
                    item, "question", f"comment_replies[{index}]"
                ),
                "reply": _required_text(item, "reply", f"comment_replies[{index}]"),
            }
        )
    normalized_publishing = {
        "titles": _string_list(
            publishing.get("titles"), "publishing_package.titles", allow_empty=False
        ),
        "post_copy": _required_text(
            publishing, "post_copy", "publishing_package"
        ),
        "tags": _string_list(
            publishing.get("tags"), "publishing_package.tags"
        ),
        "cta": _required_text(publishing, "cta", "publishing_package"),
        "comment_replies": normalized_replies,
        "status": "research_draft",
        "publishable": False,
    }

    evidence = payload.get("evidence_boundary")
    if not isinstance(evidence, dict):
        raise ContentGenerationError("DeepSeek 研究稿缺少证据边界。")
    normalized_evidence = {
        key: _string_list(evidence.get(key), f"evidence_boundary.{key}")
        for key in ("facts", "inferences", "pending")
    }

    result = {
        "status": "research_draft",
        "publishable": False,
        "summary": summary,
        "marketing_structure": normalized_marketing,
        "recommended_script": normalized_recommended,
        "shooting_table": normalized_rows,
        "publishing_package": normalized_publishing,
        "evidence_boundary": normalized_evidence,
    }
    if product_relevance is not None:
        result["product_relevance"] = product_relevance
    return result


def _quick_validate_payload(payload: Any) -> dict[str, Any]:
    """Validate the small result shown before the full production package."""
    if not isinstance(payload, dict):
        raise ContentGenerationError("DeepSeek 没有返回快速结果对象。")

    summary = _required_text(payload, "summary", "root")
    product_relevance = normalize_model_product_relevance(
        payload.get("product_relevance")
    )
    what_happens = _string_list(payload.get("what_happens"), "what_happens", allow_empty=False)
    why_it_works = _string_list(payload.get("why_it_works"), "why_it_works", allow_empty=False)
    transferable = _string_list(
        payload.get("transferable"), "transferable", allow_empty=False
    )
    original_angle = _required_text(payload, "original_angle", "root")
    evidence = payload.get("evidence_boundary")
    if not isinstance(evidence, dict):
        raise ContentGenerationError("快速结果缺少证据边界。")
    normalized_evidence = {
        key: _string_list(evidence.get(key), f"evidence_boundary.{key}")
        for key in ("facts", "inferences", "pending")
    }
    result = {
        "summary": summary,
        "what_happens": what_happens[:5],
        "why_it_works": why_it_works[:5],
        "transferable": transferable[:5],
        "original_angle": original_angle,
        "evidence_boundary": normalized_evidence,
    }
    if product_relevance is not None:
        result["product_relevance"] = product_relevance
    return result


def _safe_usage(value: Any) -> dict[str, int] | None:
    if not isinstance(value, dict):
        return None
    allowed = (
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "prompt_cache_hit_tokens",
        "prompt_cache_miss_tokens",
    )
    usage = {
        key: item
        for key in allowed
        if isinstance((item := value.get(key)), int) and not isinstance(item, bool)
    }
    return usage or None


_VISUAL_EVIDENCE_SYSTEM_GUARDRAIL = (
    "视觉数据只能作为字幕和用户资料的补充证据，不能取代字幕或人工核验。"
    "scene_structure 中的镜头切点、分段数和节奏都是机器估算，不是人工确认。"
    "不得根据抽帧数量、镜头数量或视频尺寸推断人物、物体、场景或内容语义。"
    "当 visual_evidence.ocr.status 为 unavailable 时，必须明确没有取得画面文字，"
    "禁止声称看到了画面文字或物体。即使 OCR 可用，也只能引用给出的 text_items，"
    "不得据此补写未提供的画面事实。"
)


def _visual_text(value: Any, *, limit: int = 80) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = " ".join(value.split()).strip()
    return normalized[:limit] or None


def _visual_number(value: Any, *, integer: bool = False) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    numeric = float(value)
    if not math.isfinite(numeric) or numeric < 0:
        return None
    if integer:
        if not numeric.is_integer():
            return None
        return int(numeric)
    return round(numeric, 3)


def _sanitize_visual_evidence(
    visual_evidence: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Return the small, prompt-safe subset of local visual analysis.

    This is intentionally a constructive allow-list.  Frame artifacts, URLs,
    filesystem paths, hashes, job identifiers and analyzer configuration never
    enter the returned object, even if a caller adds them to the source report.
    """
    if visual_evidence is None:
        return None
    if not isinstance(visual_evidence, dict):
        return None

    sanitized: dict[str, Any] = {}

    raw_probe = visual_evidence.get("probe")
    if isinstance(raw_probe, dict):
        probe: dict[str, Any] = {}
        for key in ("coverage_seconds",):
            if (value := _visual_number(raw_probe.get(key))) is not None:
                probe[key] = value
        if isinstance(raw_probe.get("truncated"), bool):
            probe["truncated"] = raw_probe["truncated"]
        for key in ("width", "height"):
            if (value := _visual_number(raw_probe.get(key), integer=True)) is not None:
                probe[key] = value
        if probe:
            sanitized["probe"] = probe

    raw_scene = visual_evidence.get("scene_structure")
    if isinstance(raw_scene, dict):
        scene: dict[str, Any] = {}
        if (value := _visual_text(raw_scene.get("method"))) is not None:
            scene["method"] = value
        for key in ("candidate_cut_count", "estimated_segment_count"):
            if (value := _visual_number(raw_scene.get(key), integer=True)) is not None:
                scene[key] = value
        if (value := _visual_number(raw_scene.get("cuts_per_minute"))) is not None:
            scene["cuts_per_minute"] = value
        if (value := _visual_text(raw_scene.get("pace"))) is not None:
            scene["pace"] = value
        if scene:
            sanitized["scene_structure"] = scene

    raw_ocr = visual_evidence.get("ocr")
    if isinstance(raw_ocr, dict):
        ocr: dict[str, Any] = {}
        for key in ("status", "provider", "reason_code"):
            raw_value = raw_ocr.get(key)
            if key == "provider" and raw_value is None and key in raw_ocr:
                ocr[key] = None
            elif (value := _visual_text(raw_value)) is not None:
                ocr[key] = value
        raw_text_items = raw_ocr.get("text_items")
        ocr_unavailable = str(ocr.get("status") or "").lower() == "unavailable"
        if isinstance(raw_text_items, list) and not ocr_unavailable:
            text_items: list[str] = []
            for item in raw_text_items:
                raw_text = item.get("text") if isinstance(item, dict) else item
                if (value := _visual_text(raw_text, limit=200)) is not None:
                    text_items.append(value)
                if len(text_items) >= 20:
                    break
            ocr["text_items"] = text_items
        if ocr:
            sanitized["ocr"] = ocr

    return sanitized


class DeepSeekContentProvider:
    """Generate a structured research draft from verified text and client facts."""

    name = "deepseek"

    def __init__(
        self,
        client_factory: Callable[..., httpx.AsyncClient] | None = None,
    ) -> None:
        self.client_factory = client_factory or httpx.AsyncClient

    def availability(self) -> dict[str, Any]:
        configured = bool(_deepseek_key())
        return {
            "provider": self.name,
            "configured": configured,
            "model": _deepseek_model(),
            "reason": (
                "DeepSeek 内容生成已通过服务端环境变量配置。"
                if configured
                else "未检测到 DEEPSEEK_API_KEY 或 PROJECT024_CONTENT_API_KEY。"
            ),
        }

    async def generate(
        self,
        *,
        platform: str,
        transcript: str,
        product_context: str | None,
        product: dict[str, Any] | None,
        product_relevance: dict[str, Any] | None = None,
        visual_evidence: dict[str, Any] | None = None,
        collaboration_context: list[dict[str, Any]] | None = None,
        role_instruction: str | None = None,
    ) -> ContentGenerationResult:
        secret = _deepseek_key()
        if not secret:
            raise ContentGenerationError("DeepSeek 内容生成未配置服务端密钥。")

        model = _deepseek_model()
        product_payload = {
            "free_text": product_context,
            "structured": product,
        }
        system_prompt = (
            "你是项目024自媒体通关搭档的内容编排器。"
            "只依据用户提供的字幕、资料和visual_evidence白名单生成中文内容研究稿，"
            "先判断是否具有商品属性。"
            "如果没有商品属性，不要生成商品名称、核心卖点、规格或证明材料清单。"
            "如果待确认，只给出判断依据和确认建议，不要阻塞普通内容解读。"
            "不得把推断写成商品事实；缺失信息必须写成[待确认：字段]。"
            "只给一版唯一推荐脚本，不能复制来源原句形成洗稿。"
            "完整口播稿必须为400-800个中文字符，目标时长60-90秒；"
            "shooting_table必须包含6-10个连续时间段。"
            "地区本地化在v0.2未启用，不得自行添加地区俚语或市场事实。"
            "所有输出都只是研究稿，publishable必须为false，禁止声称可直接发布。"
            + _VISUAL_EVIDENCE_SYSTEM_GUARDRAIL
            + (role_instruction or "")
            + "返回严格JSON对象，不要Markdown。"
        )
        user_payload = {
            "platform": platform,
            "transcript": transcript,
            "product": product_payload,
            "inferred_product_relevance": product_relevance,
            "visual_evidence": _sanitize_visual_evidence(visual_evidence),
            "required_schema": {
                "summary": "甲方可读的一句话结论",
                "product_relevance": {
                    "status": "has_product、no_product 或 needs_confirmation",
                    "confidence": "high、medium 或 low",
                    "evidence": [
                        "引用字幕、用户资料或已提供的OCR text_items；"
                        "没有OCR文本时不得声称依据来自画面"
                    ],
                    "reason": "用普通话说明为什么这样判断",
                    "follow_up": ["后续建议；无商品时明确无需补商品资料"],
                },
                "marketing_structure": {
                    "hook": "来源钩子与证据",
                    "product_demo": "有商品时写商品演示与证据；无商品时写方法、场景或观点演示",
                    "value_proposition": "价值主张与证据边界",
                    "cta": "行动引导与证据边界",
                },
                "recommended_script": {
                    "title": "唯一推荐稿标题",
                    "duration_seconds": "60-90 秒之间的整数",
                    "full_text": "400-800 个中文字符的完整可拍口播脚本，不是提纲",
                    "selection_reason": "选择这一版的原因",
                },
                "shooting_table": [
                    {
                        "time": "0-3秒",
                        "visual": "画面",
                        "voiceover": "口播",
                        "subtitle": "屏幕字幕",
                        "product_proof": "需要展示的商品证明或待确认项",
                        "sound": "声音",
                    }
                ],
                "publishing_package": {
                    "titles": ["标题1", "标题2", "标题3"],
                    "post_copy": "发布正文",
                    "tags": ["话题标签"],
                    "cta": "行动引导",
                    "comment_replies": [
                        {"question": "问题", "reply": "只含已核验事实的回复"}
                    ],
                },
                "evidence_boundary": {
                    "facts": ["来源可确认事实"],
                    "inferences": ["明确标注的推断"],
                    "pending": ["待确认项"],
                },
            },
        }
        if collaboration_context:
            user_payload["agent_outputs"] = _bounded_agent_outputs(collaboration_context)
        request_payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": json.dumps(user_payload, ensure_ascii=False),
                },
            ],
            "response_format": {"type": "json_object"},
            "stream": False,
            "temperature": 0.2,
            "max_tokens": 4000,
        }

        timeout = _env_float(
            "PROJECT024_CONTENT_TIMEOUT_SECONDS", 90.0, 5.0, 300.0
        )
        try:
            async with self.client_factory(timeout=timeout) as client:
                response = await client.post(
                    _deepseek_endpoint(),
                    headers={"Authorization": f"Bearer {secret}"},
                    json=request_payload,
                )
        except httpx.TimeoutException as exc:
            raise ContentGenerationError("DeepSeek 内容生成请求超时。") from exc
        except httpx.HTTPError as exc:
            raise ContentGenerationError(
                f"DeepSeek 内容生成网络失败：{type(exc).__name__}"
            ) from exc
        except Exception as exc:
            raise ContentGenerationError(
                f"DeepSeek 内容生成请求失败：{type(exc).__name__}"
            ) from exc

        if response.status_code >= 400:
            raise ContentGenerationError(_safe_error(response, secret))
        try:
            response_payload = response.json()
            if not isinstance(response_payload, dict):
                raise TypeError("response payload is not an object")
            choices = response_payload.get("choices")
            content = choices[0]["message"]["content"]
            if not isinstance(content, str) or not content.strip():
                raise TypeError("response content is not text")
            generated = json.loads(_strip_json_fence(str(content)))
        except (AttributeError, IndexError, KeyError, TypeError, ValueError) as exc:
            raise ContentGenerationError("DeepSeek 返回内容无法解析为有效研究稿。") from exc

        request_id = _sanitize_error_text(
            response.headers.get("x-request-id", ""), secret
        )

        return ContentGenerationResult(
            data=_validate_payload(generated),
            provider=self.name,
            model=model,
            provider_metadata={
                "request_id": request_id or None,
                "usage": _safe_usage(response_payload.get("usage")),
                "response_format": "json_object",
            },
        )

    async def generate_quick(
        self,
        *,
        platform: str,
        transcript: str,
        product_context: str | None,
        product: dict[str, Any] | None,
        product_relevance: dict[str, Any] | None = None,
        visual_evidence: dict[str, Any] | None = None,
        collaboration_context: list[dict[str, Any]] | None = None,
        role_instruction: str | None = None,
    ) -> ContentGenerationResult:
        """Return a compact, user-facing understanding before the full package."""
        secret = _deepseek_key()
        if not secret:
            raise ContentGenerationError("DeepSeek 内容生成未配置服务端密钥。")

        model = _deepseek_quick_model()
        user_payload = {
            "platform": platform,
            "transcript": transcript,
            "product": {"free_text": product_context, "structured": product},
            "inferred_product_relevance": product_relevance,
            "visual_evidence": _sanitize_visual_evidence(visual_evidence),
            "required_schema": {
                "summary": "用普通人能看懂的一句话说明这条内容在讲什么",
                "product_relevance": {
                    "status": "has_product、no_product 或 needs_confirmation",
                    "confidence": "high、medium 或 low",
                    "evidence": [
                        "字幕、用户资料或已提供的OCR text_items中的依据；"
                        "没有OCR文本时不得声称依据来自画面"
                    ],
                    "reason": "判断原因",
                    "follow_up": ["后续建议"],
                },
                "what_happens": ["内容的关键步骤或结构，最多5条"],
                "why_it_works": ["观众为什么会继续看或采取行动，最多5条"],
                "transferable": ["可以借鉴的方法，不能复制原句，最多5条"],
                "original_angle": "基于方法提出一个不复刻来源的原创方向",
                "evidence_boundary": {
                    "facts": ["字幕中直接出现的事实"],
                    "inferences": ["明确标注的推断"],
                    "pending": ["仍需核对的内容"],
                },
            },
        }
        if collaboration_context:
            user_payload["agent_outputs"] = _bounded_agent_outputs(collaboration_context)
        system_prompt = (
            "你是项目024的快速内容解读器。只依据用户提供的字幕、商品资料和"
            "visual_evidence白名单，"
            "用普通人能看懂的中文解释内容，不生成完整脚本，不补写商品事实，"
            "不把推断写成事实，不复刻来源原句。先判断商品属性；"
            "无商品时不要列商品资料缺失。"
            + _VISUAL_EVIDENCE_SYSTEM_GUARDRAIL
            + (role_instruction or "")
            + "只返回严格JSON对象。"
        )
        request_payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": json.dumps(user_payload, ensure_ascii=False),
                },
            ],
            "response_format": {"type": "json_object"},
            "stream": False,
            "temperature": 0.2,
            "max_tokens": 900,
        }
        timeout = _env_float(
            "PROJECT024_CONTENT_QUICK_TIMEOUT_SECONDS", 30.0, 5.0, 120.0
        )
        try:
            async with self.client_factory(timeout=timeout) as client:
                response = await client.post(
                    _deepseek_endpoint(),
                    headers={"Authorization": f"Bearer {secret}"},
                    json=request_payload,
                )
        except httpx.TimeoutException as exc:
            raise ContentGenerationError("快速内容解读请求超时。") from exc
        except httpx.HTTPError as exc:
            raise ContentGenerationError(
                f"快速内容解读网络失败：{type(exc).__name__}"
            ) from exc
        except Exception as exc:
            raise ContentGenerationError(
                f"快速内容解读请求失败：{type(exc).__name__}"
            ) from exc

        if response.status_code >= 400:
            raise ContentGenerationError(_safe_error(response, secret))
        try:
            response_payload = response.json()
            if not isinstance(response_payload, dict):
                raise TypeError("response payload is not an object")
            choices = response_payload.get("choices")
            content = choices[0]["message"]["content"]
            if not isinstance(content, str) or not content.strip():
                raise TypeError("response content is not text")
            generated = json.loads(_strip_json_fence(content))
        except (AttributeError, IndexError, KeyError, TypeError, ValueError) as exc:
            raise ContentGenerationError("快速内容解读返回内容无法解析。") from exc

        request_id = _sanitize_error_text(
            response.headers.get("x-request-id", ""), secret
        )
        return ContentGenerationResult(
            data=_quick_validate_payload(generated),
            provider=self.name,
            model=model,
            provider_metadata={
                "request_id": request_id or None,
                "usage": _safe_usage(response_payload.get("usage")),
                "response_format": "json_object",
                "mode": "quick",
            },
        )


class ContentGenerationRouter:
    def __init__(self, provider: DeepSeekContentProvider | None = None) -> None:
        self.provider = provider or DeepSeekContentProvider()

    @staticmethod
    def _strategy(value: Any = None) -> str:
        selected = str(
            value or os.getenv("PROJECT024_CONTENT_STRATEGY", "multi_agent")
        ).strip().lower()
        return "single_model" if selected in {"single", "single_model", "single-agent"} else "multi_agent"

    def plan(self, strategy: Any = None) -> dict[str, Any]:
        availability = self.provider.availability()
        orchestration_mode = self._strategy(strategy)
        return {
            "status": "ready" if availability["configured"] else "not_configured",
            "provider": availability["provider"],
            "model": availability["model"],
            "configured": availability["configured"],
            "paid_api_called": False,
            "orchestration_mode": orchestration_mode,
            "requested_roles": [role for role, _ in _FULL_AGENT_ROLES]
            if orchestration_mode == "multi_agent"
            else [],
            "message": availability["reason"],
        }

    @staticmethod
    def _summary(outcome: dict[str, Any]) -> dict[str, Any]:
        return {
            key: outcome[key]
            for key in ("role", "status", "provider", "model", "usage", "error")
            if key in outcome and outcome[key] is not None
        }

    @staticmethod
    def _with_orchestration(
        result: ContentGenerationResult,
        orchestration: dict[str, Any],
    ) -> ContentGenerationResult:
        metadata = dict(result.provider_metadata)
        metadata["orchestration"] = orchestration
        return ContentGenerationResult(
            data=result.data,
            provider=result.provider,
            model=result.model,
            provider_metadata=metadata,
        )

    async def _run_role(
        self,
        method: Callable[..., Any],
        role: str,
        instruction: str,
        kwargs: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            result = await method(**kwargs, role_instruction=instruction)
            return {
                "role": role,
                "status": "completed",
                "provider": result.provider,
                "model": result.model,
                "usage": result.provider_metadata.get("usage"),
                "result": result.data,
            }
        except Exception as exc:
            return {
                "role": role,
                "status": "failed",
                "error": _sanitize_error_text(exc),
            }

    async def _fallback(
        self,
        method: Callable[..., Any],
        kwargs: dict[str, Any],
        orchestration: dict[str, Any],
    ) -> ContentGenerationResult:
        try:
            result = await method(**kwargs)
        except Exception as exc:
            orchestration["fallback_error"] = _sanitize_error_text(exc)
            raise
        orchestration["mode"] = "single_agent_fallback"
        orchestration["fallback_used"] = True
        orchestration["call_count"] = int(orchestration.get("call_count", 0)) + 1
        return self._with_orchestration(result, orchestration)

    async def _multi_agent_generate(
        self,
        *,
        role_method: Callable[..., Any],
        synthesis_method: Callable[..., Any],
        roles: tuple[tuple[str, str], ...],
        kwargs: dict[str, Any],
    ) -> ContentGenerationResult:
        outcomes = await asyncio.gather(
            *(self._run_role(role_method, role, instruction, kwargs) for role, instruction in roles)
        )
        completed = [outcome for outcome in outcomes if outcome["status"] == "completed"]
        orchestration: dict[str, Any] = {
            "mode": "multi_agent",
            "requested_roles": [role for role, _ in roles],
            "completed_roles": [outcome["role"] for outcome in completed],
            "failed_roles": [outcome["role"] for outcome in outcomes if outcome["status"] != "completed"],
            "fanout_count": len(outcomes),
            "fan_in_status": "pending",
            "fallback_used": False,
            "role_runs": [self._summary(outcome) for outcome in outcomes],
            "call_count": len(outcomes),
        }
        if len(completed) < 2:
            orchestration["fan_in_status"] = "skipped_insufficient_roles"
            return await self._fallback(synthesis_method, kwargs, orchestration)

        synthesis_kwargs = dict(kwargs)
        synthesis_kwargs["collaboration_context"] = completed
        synthesis_kwargs["role_instruction"] = _SYNTHESIS_INSTRUCTION
        try:
            result = await synthesis_method(**synthesis_kwargs)
        except Exception as exc:
            orchestration["fan_in_status"] = "failed"
            orchestration["fan_in_error"] = _sanitize_error_text(exc)
            return await self._fallback(synthesis_method, kwargs, orchestration)
        orchestration["fan_in_status"] = "completed"
        orchestration["call_count"] = len(outcomes) + 1
        return self._with_orchestration(result, orchestration)

    async def generate(
        self,
        *,
        strategy: Any = None,
        **kwargs: Any,
    ) -> ContentGenerationResult | None:
        if not self.provider.availability()["configured"]:
            return None
        if self._strategy(strategy) == "single_model":
            result = await self.provider.generate(**kwargs)
            return self._with_orchestration(
                result,
                {
                    "mode": "single_model",
                    "requested_roles": [],
                    "completed_roles": [],
                    "failed_roles": [],
                    "fanout_count": 0,
                    "fan_in_status": "not_requested",
                    "fallback_used": False,
                    "call_count": 1,
                },
            )
        return await self._multi_agent_generate(
            role_method=self.provider.generate_quick,
            synthesis_method=self.provider.generate,
            roles=_FULL_AGENT_ROLES,
            kwargs=kwargs,
        )

    async def generate_quick(
        self,
        *,
        strategy: Any = None,
        **kwargs: Any,
    ) -> ContentGenerationResult | None:
        if not self.provider.availability()["configured"]:
            return None
        if self._strategy(strategy) == "single_model":
            result = await self.provider.generate_quick(**kwargs)
            return self._with_orchestration(
                result,
                {
                    "mode": "single_model",
                    "requested_roles": [],
                    "completed_roles": [],
                    "failed_roles": [],
                    "fanout_count": 0,
                    "fan_in_status": "not_requested",
                    "fallback_used": False,
                    "call_count": 1,
                },
            )
        return await self._multi_agent_generate(
            role_method=self.provider.generate_quick,
            synthesis_method=self.provider.generate_quick,
            roles=_QUICK_AGENT_ROLES,
            kwargs=kwargs,
        )
