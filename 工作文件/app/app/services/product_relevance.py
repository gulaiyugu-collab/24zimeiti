from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any


PRODUCT_RELEVANCE_STATUSES = {"has_product", "no_product", "needs_confirmation"}
CONFIDENCE_LEVELS = {"high", "medium", "low"}

_STRONG_PRODUCT_TERMS = (
    "商品",
    "产品",
    "品牌",
    "型号",
    "sku",
    "价格",
    "售价",
    "购买",
    "下单",
    "购物车",
    "购买链接",
    "优惠",
    "折扣",
    "套装",
    "配件",
    "规格",
    "参数",
    "材质",
    "尺寸",
    "电池",
    "续航",
    "适用年龄",
    "库存",
    "物流",
    "售后",
    "开箱",
    "卖点",
    "功效",
    "成分",
    "随盒",
    "同款",
    "product",
    "brand",
    "price",
    "buy",
    "purchase",
    "order",
    "shop",
    "sale",
    "discount",
    "bundle",
    "kit",
    "accessory",
    "accessories",
    "specification",
    "specifications",
    "specs",
    "unboxing",
    "review",
)

_WEAK_PRODUCT_TERMS = (
    "这款",
    "这件",
    "这台",
    "功能",
    "演示",
    "展示",
    "测评",
    "体验",
    "好用",
    "推荐",
    "feature",
    "demo",
    "showcase",
    "tested",
)

_NON_PRODUCT_TERMS = (
    "观点",
    "看法",
    "科普",
    "知识",
    "教程",
    "方法",
    "经验",
    "分析",
    "解读",
    "新闻",
    "故事",
    "历史",
    "学习",
    "成长",
    "职场",
    "心理",
    "情绪",
    "读书",
    "思考",
    "生活",
    "opinion",
    "tutorial",
    "education",
    "news",
    "history",
    "career",
    "psychology",
    "story",
)

def _mapping(value: Any) -> dict[str, Any] | None:
    if isinstance(value, Mapping):
        return dict(value)
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump(mode="json", exclude_none=True)
        return dict(dumped) if isinstance(dumped, Mapping) else None
    return None


def _flatten_text(value: Any, *, limit: int = 12_000) -> str:
    chunks: list[str] = []
    used = 0

    def visit(item: Any) -> None:
        nonlocal used
        if used >= limit:
            return
        if isinstance(item, str):
            text = item.strip()
            if text:
                chunk = text[: limit - used]
                chunks.append(chunk)
                used += len(chunk)
            return
        if isinstance(item, Mapping):
            for child in item.values():
                visit(child)
                if used >= limit:
                    return
            return
        if isinstance(item, (list, tuple, set)):
            for child in item:
                visit(child)
                if used >= limit:
                    return

    visit(value)
    return "\n".join(chunks)[:limit]


def _matching_terms(text: str, terms: tuple[str, ...]) -> list[str]:
    lowered = text.casefold()
    matches: list[str] = []
    for term in terms:
        candidate = term.casefold()
        if candidate.isascii() and candidate.isalpha():
            if re.search(rf"(?<![a-z0-9_]){re.escape(candidate)}(?![a-z0-9_])", lowered):
                matches.append(term)
        elif candidate in lowered:
            matches.append(term)
    return matches


def _has_product_submission(product: Any, product_context: Any) -> bool:
    structured = _mapping(product)
    if structured and any(
        value not in (None, "", [], {}) for value in structured.values()
    ):
        return True
    return bool(str(product_context or "").strip())


def _missing_product_fields(product: Any) -> list[str]:
    structured = _mapping(product)
    missing: list[str] = []
    if not structured or not (structured.get("name") or structured.get("sku")):
        missing.append("商品名称或 SKU")
    if not structured or not structured.get("category"):
        missing.append("商品品类")
    if not structured or not structured.get("selling_points"):
        missing.append("商品核心卖点")
    if not structured or not structured.get("specifications"):
        missing.append("商品规格参数")
    if not structured or not structured.get("approved_claims"):
        missing.append("甲方批准使用的宣传表述")
    if not structured or not structured.get("evidence_urls"):
        missing.append("商品证明材料")
    return missing


def _follow_up(status: str, missing_fields: list[str] | None = None) -> list[str]:
    if status == "no_product":
        return ["继续按内容主题和方法解读；无需补商品名称、核心卖点、规格或证明材料。"]
    if status == "needs_confirmation":
        return [
            "确认内容是否在展示、推荐或销售具体商品。",
            "确认前不把商品资料列为普通解读的缺失项。",
        ]
    missing = missing_fields or []
    if missing:
        return [
            "如果要改写成自己的商品内容或正式发布，再补齐：" + "、".join(missing) + "。",
            "所有卖点和参数都要有甲方可核验的来源，不能从来源视频猜测。",
        ]
    return ["商品资料已提交；正式发布前仍需核对事实、资质和平台规则。"]


def infer_product_relevance(
    *,
    transcript: str | None = None,
    product_context: str | None = None,
    product: Any = None,
    source_material: Any = None,
    override: str | None = None,
) -> dict[str, Any]:
    """Make a conservative, explainable first-pass product classification."""
    if override in PRODUCT_RELEVANCE_STATUSES:
        has_product = (
            True if override == "has_product"
            else False if override == "no_product"
            else None
        )
        if override == "has_product":
            reason = "按用户确认，将商品资料纳入后续改写或发布准备。"
        elif override == "no_product":
            reason = "按用户确认，该内容不按商品内容处理。"
        else:
            reason = "按用户确认，当前证据仍不足以判断商品属性。"
        return {
            "status": override,
            "has_product": has_product,
            "confidence": "high",
            "evidence": ["用户已明确确认该内容的商品属性。"],
            "reason": reason,
            "follow_up": _follow_up(
                override,
                _missing_product_fields(product) if override == "has_product" else None,
            ),
            "source": "user_confirmation",
            "product_fields_applicable": has_product is True,
            "required_for": "product_rewrite_or_publish_only",
        }

    text = _flatten_text([transcript or "", source_material])
    strong = _matching_terms(text, _STRONG_PRODUCT_TERMS)
    weak = _matching_terms(text, _WEAK_PRODUCT_TERMS)
    non_product = _matching_terms(text, _NON_PRODUCT_TERMS)
    submitted = _has_product_submission(product, product_context)

    negative_product = bool(
        re.search(r"(?:不是|没有|无关|不涉及|不卖|非)\s*.{0,5}(?:商品|产品|购买|品牌)", text)
    )
    decision_source = "rule_based"

    if submitted:
        status = "has_product"
        confidence = "high"
        evidence = ["用户已提交商品背景或结构化商品资料。"]
        reason = "已提交的目标资料表明后续可能要改写成商品内容；商品字段只在改写或发布时生效。"
        decision_source = "client_product_input"
    elif negative_product and len(strong) <= 1:
        status = "no_product"
        confidence = "high"
        evidence = ["文字明确否定商品或购买属性。"]
        reason = "当前内容明确不以商品展示、推荐或销售为主。"
    elif len(strong) >= 2 or (len(strong) >= 1 and len(weak) >= 1):
        status = "has_product"
        confidence = "high"
        evidence = [f"发现商品相关信号：{'、'.join(strong[:5] + weak[:3])}。"]
        reason = "内容出现了具体商品、购买、规格或商品演示信号。"
    elif len(strong) == 1:
        status = "has_product"
        confidence = "medium"
        evidence = [f"发现一个明确商品信号：{strong[0]}。"]
        reason = "内容可能涉及具体商品，但证据还不够完整。"
    elif weak:
        status = "needs_confirmation"
        confidence = "low"
        evidence = [f"只发现较弱的展示或推荐信号：{'、'.join(weak[:4])}。"]
        reason = "内容有展示或推荐意味，但还不能确定是否在讲具体商品。"
    elif len(text.strip()) < 20:
        status = "needs_confirmation"
        confidence = "low"
        evidence = ["可供判断的文字太少。"]
        reason = "当前证据不足，不能把商品资料列成必填项。"
    else:
        status = "no_product"
        confidence = "high" if non_product else "medium"
        evidence = (
            [f"内容更像主题、知识或观点表达：{'、'.join(non_product[:4])}。"]
            if non_product
            else ["未发现具体商品、购买、规格或销售信号。"]
        )
        reason = "当前内容按知识、观点或一般内容解读，不需要商品资料。"

    missing_fields = _missing_product_fields(product) if status == "has_product" else []
    return {
        "status": status,
        "has_product": status == "has_product" if status != "needs_confirmation" else None,
        "confidence": confidence,
        "evidence": evidence,
        "reason": reason,
        "follow_up": _follow_up(status, missing_fields),
        "source": decision_source,
        "product_fields_applicable": status == "has_product",
        "required_for": "product_rewrite_or_publish_only",
    }


def normalize_model_product_relevance(value: Any) -> dict[str, Any] | None:
    """Accept only a small, explainable model correction; otherwise use the rule result."""
    data = _mapping(value)
    if not data:
        return None
    status = str(data.get("status") or "").strip().lower()
    if status not in PRODUCT_RELEVANCE_STATUSES:
        raw_has_product = data.get("has_product")
        if raw_has_product is True:
            status = "has_product"
        elif raw_has_product is False:
            status = "no_product"
        else:
            return None
    confidence = str(data.get("confidence") or "medium").strip().lower()
    if confidence not in CONFIDENCE_LEVELS:
        if isinstance(data.get("confidence"), (int, float)):
            number = float(data["confidence"])
            confidence = "high" if number >= 0.75 else "medium" if number >= 0.45 else "low"
        else:
            confidence = "medium"
    evidence = data.get("evidence") or data.get("signals") or []
    if isinstance(evidence, str):
        evidence = [evidence]
    evidence = [str(item).strip() for item in evidence if str(item).strip()][:6]
    reason = str(data.get("reason") or "").strip()
    if not evidence or not reason:
        return None
    follow_up = data.get("follow_up") or []
    if isinstance(follow_up, str):
        follow_up = [follow_up]
    follow_up = [str(item).strip() for item in follow_up if str(item).strip()][:6]
    if not follow_up:
        follow_up = _follow_up(status)
    return {
        "status": status,
        "has_product": status == "has_product" if status != "needs_confirmation" else None,
        "confidence": confidence,
        "evidence": evidence,
        "reason": reason,
        "follow_up": follow_up,
        "source": "model",
        "product_fields_applicable": status == "has_product",
        "required_for": "product_rewrite_or_publish_only",
    }


def merge_product_relevance(
    rule_based: dict[str, Any], model_value: Any = None
) -> dict[str, Any]:
    model = normalize_model_product_relevance(model_value)
    if model is None:
        return rule_based
    # Explicit client product data is stronger than a model's guess that the source is generic.
    if rule_based.get("source") in {"user_confirmation", "client_product_input"}:
        return rule_based
    return {
        **model,
        "rule_based_status": rule_based.get("status"),
        "rule_based_confidence": rule_based.get("confidence"),
    }


def build_product_requirements(
    *,
    product: Any = None,
    product_context: str | None = None,
    relevance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    relevance = relevance or infer_product_relevance(
        product=product, product_context=product_context
    )
    structured = _mapping(product)
    missing = _missing_product_fields(structured)

    status = str(relevance.get("status") or "needs_confirmation")
    if status == "no_product":
        applicable_missing: list[str] = []
        requirement_status = "not_applicable"
        follow_up: list[str] = []
    elif status == "needs_confirmation":
        applicable_missing = []
        requirement_status = "needs_confirmation"
        follow_up = list(relevance.get("follow_up") or _follow_up(status))
    else:
        applicable_missing = missing
        requirement_status = "needs_input" if missing else "submitted_needs_verification"
        follow_up = _follow_up(status, missing)

    return {
        "status": requirement_status,
        "scope": "product_rewrite_or_publish_only",
        "submitted": {
            "legacy_context": product_context,
            "structured": structured,
        },
        "verification_status": (
            "not_applicable"
            if status == "no_product"
            else "unverified_user_submission" if structured or product_context else "missing"
        ),
        "missing_fields": applicable_missing,
        "conditional_missing_fields": missing if status == "needs_confirmation" else [],
        "follow_up": follow_up,
        "placeholder_policy": "缺失项保留占位符，不得推断为商品事实。",
    }


def is_optional_enhancement(value: Any) -> bool:
    text = str(value or "").lower()
    return any(
        marker in text
        for marker in (
            "ocr",
            "镜头结构",
            "公开评论",
            "评论原文",
            "评论分页",
            "实时公开指标",
            "播放量",
            "流量来源",
            "增长时间序列",
        )
    )
