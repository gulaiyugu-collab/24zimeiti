from __future__ import annotations

import json
import math
import os
import re
from dataclasses import dataclass
from typing import Any, Callable

import httpx


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

    return {
        "status": "research_draft",
        "publishable": False,
        "summary": summary,
        "marketing_structure": normalized_marketing,
        "recommended_script": normalized_recommended,
        "shooting_table": normalized_rows,
        "publishing_package": normalized_publishing,
        "evidence_boundary": normalized_evidence,
    }


def _quick_validate_payload(payload: Any) -> dict[str, Any]:
    """Validate the small result shown before the full production package."""
    if not isinstance(payload, dict):
        raise ContentGenerationError("DeepSeek 没有返回快速结果对象。")

    summary = _required_text(payload, "summary", "root")
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
    return {
        "summary": summary,
        "what_happens": what_happens[:5],
        "why_it_works": why_it_works[:5],
        "transferable": transferable[:5],
        "original_angle": original_angle,
        "evidence_boundary": normalized_evidence,
    }


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
            "只依据用户提供的字幕和商品资料生成中文商品短视频研究稿。"
            "不得把推断写成商品事实；缺失信息必须写成[待确认：字段]。"
            "只给一版唯一推荐脚本，不能复制来源原句形成洗稿。"
            "完整口播稿必须为400-800个中文字符，目标时长60-90秒；"
            "shooting_table必须包含6-10个连续时间段。"
            "地区本地化在v0.2未启用，不得自行添加地区俚语或市场事实。"
            "所有输出都只是研究稿，publishable必须为false，禁止声称可直接发布。"
            "返回严格JSON对象，不要Markdown。"
        )
        user_payload = {
            "platform": platform,
            "transcript": transcript,
            "product": product_payload,
            "required_schema": {
                "summary": "甲方可读的一句话结论",
                "marketing_structure": {
                    "hook": "来源钩子与证据",
                    "product_demo": "商品演示与证据",
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
            "required_schema": {
                "summary": "用普通人能看懂的一句话说明这条内容在讲什么",
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
        system_prompt = (
            "你是项目024的快速内容解读器。只依据用户提供的字幕和商品资料，"
            "用普通人能看懂的中文解释内容，不生成完整脚本，不补写商品事实，"
            "不把推断写成事实，不复刻来源原句。只返回严格JSON对象。"
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

    def plan(self) -> dict[str, Any]:
        availability = self.provider.availability()
        return {
            "status": "ready" if availability["configured"] else "not_configured",
            "provider": availability["provider"],
            "model": availability["model"],
            "configured": availability["configured"],
            "paid_api_called": False,
            "message": availability["reason"],
        }

    async def generate(self, **kwargs: Any) -> ContentGenerationResult | None:
        if not self.provider.availability()["configured"]:
            return None
        return await self.provider.generate(**kwargs)

    async def generate_quick(self, **kwargs: Any) -> ContentGenerationResult | None:
        if not self.provider.availability()["configured"]:
            return None
        return await self.provider.generate_quick(**kwargs)
