from __future__ import annotations

import json
import re
from typing import Any, Callable, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .content_generation import (
    _bounded_value,
    _deepseek_endpoint,
    _deepseek_key,
    _deepseek_model,
    _env_float,
    _safe_error,
    _safe_usage,
    _sanitize_error_text,
    _strip_json_fence,
)


AgentMode = Literal["script", "strategy"]
AgentPage = Literal["analysis", "douyin", "publish"]

_EDIT_REQUEST_PATTERN = re.compile(
    r"改|修改|优化|重写|调整|改成|换成|增加|删掉|删除|压缩|扩展|润色|口语化|正式化|搞笑|简化"
)


def _request_requires_edit(message: str) -> bool:
    """Hint the model about edit intent without forcing a change for questions."""
    return bool(_EDIT_REQUEST_PATTERN.search(message or ""))


class AgentHistoryItem(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4000)


class OperationsAgentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    message: str = Field(min_length=1, max_length=4000)
    mode: AgentMode
    page: AgentPage
    draft: str = Field(default="", max_length=20_000)
    context: dict[str, Any] = Field(default_factory=dict)
    history: list[AgentHistoryItem] = Field(default_factory=list, max_length=12)
    confirm_paid: bool = False

    @field_validator("context")
    @classmethod
    def _bounded_context_size(cls, value: dict[str, Any]) -> dict[str, Any]:
        encoded = json.dumps(value, ensure_ascii=False, default=str)
        if len(encoded) > 50_000:
            raise ValueError("context 超过 50000 字符限制")
        return value


class OperationsAgentConfirmationError(Exception):
    """A paid model call needs an explicit user action."""


class OperationsAgentUnavailableError(Exception):
    """The configured model provider is unavailable."""


class OperationsAgentError(Exception):
    """The model call failed or returned an invalid response."""


class OperationsAgent:
    """One-call iterative editor for scripts and evidence-backed account strategy."""

    def __init__(
        self,
        client_factory: Callable[..., httpx.AsyncClient] | None = None,
    ) -> None:
        self.client_factory = client_factory or httpx.AsyncClient

    def plan(self) -> dict[str, Any]:
        configured = bool(_deepseek_key())
        return {
            "status": "ready" if configured else "not_configured",
            "provider": "deepseek",
            "model": _deepseek_model(),
            "configured": configured,
            "paid_api_called": False,
            "call_count": 0,
            "message": (
                "运营 Agent 已配置，可在用户发送后调用一次模型。"
                if configured
                else "运营 Agent 未配置服务端内容模型密钥。"
            ),
        }

    async def chat(self, payload: OperationsAgentRequest) -> dict[str, Any]:
        if not payload.confirm_paid:
            raise OperationsAgentConfirmationError("请在用户主动发送后确认本次模型调用。")
        secret = _deepseek_key()
        if not secret:
            raise OperationsAgentUnavailableError("运营 Agent 未配置服务端内容模型。")

        mode_instruction = (
            "你的工作是迭代当前完整口播稿。updated_text 必须返回修改后的完整稿，"
            "不是提纲、差异说明或零散片段。"
            if payload.mode == "script"
            else "你的工作是迭代当前运营策略。只依据给出的账号自身数据、选题和实验记录，"
            "updated_text 必须返回可继续编辑的完整策略，不引用虚构行业基准。"
        )
        system_prompt = (
            "你是项目024页面内的迭代 Agent。使用简洁中文与使用者直接对话。"
            "只依据 current_draft 和 context 中的证据工作，不补写账号数据、商品事实、"
            "平台权限或流量承诺；推断与待确认项必须明确标注。"
            "先判断 request 的意图：如果 request_requires_edit=true 且没有安全或证据阻塞，"
            "必须实际改写，不能只给建议；如果只是提问或资料不足，updated_text 原样返回并说明原因。"
            "reply 用两三句话说明本轮判断，updated_text 放完整可编辑文本，"
            "next_actions 最多三个具体下一步。"
            + mode_instruction
            + "只返回严格 JSON 对象，不要 Markdown。"
        )
        history = [
            {"role": item.role, "content": item.content[:1200]}
            for item in payload.history[-8:]
        ]
        user_payload = {
            "mode": payload.mode,
            "page": payload.page,
            "request": payload.message,
            "request_requires_edit": _request_requires_edit(payload.message),
            "current_draft": payload.draft[:20_000],
            "context": _bounded_value(payload.context),
            "recent_conversation": history,
            "required_schema": {
                "reply": "本轮判断与修改说明",
                "updated_text": "修改后的完整脚本或完整运营策略；不修改时原样返回",
                "next_actions": ["最多三个可执行下一步"],
            },
        }
        request_payload = {
            "model": _deepseek_model(),
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
            "max_tokens": 1800,
        }
        timeout = _env_float("PROJECT024_AGENT_TIMEOUT_SECONDS", 45.0, 5.0, 120.0)
        try:
            async with self.client_factory(timeout=timeout) as client:
                response = await client.post(
                    _deepseek_endpoint(),
                    headers={"Authorization": f"Bearer {secret}"},
                    json=request_payload,
                )
        except httpx.TimeoutException as exc:
            raise OperationsAgentError("运营 Agent 请求超时。") from exc
        except httpx.HTTPError as exc:
            raise OperationsAgentError(
                f"运营 Agent 网络失败：{type(exc).__name__}"
            ) from exc
        except Exception as exc:
            raise OperationsAgentError(
                f"运营 Agent 请求失败：{type(exc).__name__}"
            ) from exc

        if response.status_code >= 400:
            raise OperationsAgentError(_safe_error(response, secret))
        try:
            response_payload = response.json()
            choices = response_payload.get("choices")
            content = choices[0]["message"]["content"]
            generated = json.loads(_strip_json_fence(str(content)))
            raw_reply = generated.get("reply")
            raw_updated_text = generated.get("updated_text", payload.draft)
            if not isinstance(raw_reply, str) or not isinstance(raw_updated_text, str):
                raise TypeError("agent text fields are invalid")
            reply = raw_reply.strip()
            updated_text = raw_updated_text.strip()
            if payload.draft.strip() and not updated_text:
                raise ValueError("updated_text cannot be empty when current draft is not empty")
            raw_actions = generated.get("next_actions", [])
            if not reply or not isinstance(raw_actions, list):
                raise ValueError("invalid agent payload")
            next_actions = [
                item.strip()[:500]
                for item in raw_actions[:3]
                if isinstance(item, str) and item.strip()
            ]
            if len(reply) > 4000 or len(updated_text) > 20_000:
                raise ValueError("agent payload too large")
        except (AttributeError, IndexError, KeyError, TypeError, ValueError) as exc:
            raise OperationsAgentError("运营 Agent 返回内容无法解析。") from exc

        changed = updated_text != payload.draft.strip()

        return {
            "reply": reply,
            "updated_text": updated_text,
            "next_actions": next_actions,
            "decision": {
                "action": "apply" if changed else "keep",
                "changed": changed,
                "request_requires_edit": _request_requires_edit(payload.message),
                "before_chars": len(payload.draft.strip()),
                "after_chars": len(updated_text),
            },
            "provider": "deepseek",
            "model": _deepseek_model(),
            "provider_metadata": {
                "request_id": _sanitize_error_text(
                    response.headers.get("x-request-id", ""), secret
                )
                or None,
                "usage": _safe_usage(response_payload.get("usage")),
                "paid_api_called": True,
                "call_count": 1,
            },
        }
