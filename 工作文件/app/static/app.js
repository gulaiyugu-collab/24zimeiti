"use strict";

const elements = {
  form: document.querySelector("#analysisForm"),
  urlInput: document.querySelector("#urlInput"),
  transcriptInput: document.querySelector("#transcriptInput"),
  productContextInput: document.querySelector("#productContextInput"),
  productContextField: document.querySelector("#productContextField"),
  mediaFileInput: document.querySelector("#mediaFileInput"),
  mediaFileMeta: document.querySelector("#mediaFileMeta"),
  transcribeButton: document.querySelector("#transcribeButton"),
  transcribeLabel: document.querySelector("#transcribeButton .button__label"),
  transcriptionStatus: document.querySelector("#transcriptionStatus"),
  asrStrategySelect: document.querySelector("#asrStrategySelect"),
  targetMarketSelect: document.querySelector("#targetMarketSelect"),
  supplementDetails: document.querySelector("#supplementDetails"),
  analyzeButton: document.querySelector("#analyzeButton"),
  analyzeLabel: document.querySelector("#analyzeButton .button__label"),
  demoButton: document.querySelector("#demoButton"),
  urlError: document.querySelector("#urlError"),
  formMessage: document.querySelector("#formMessage"),
  platformStatusText: document.querySelector("#platformStatusText"),
  resultArea: document.querySelector("#resultArea"),
  statePanel: document.querySelector("#statePanel"),
  reportLayout: document.querySelector("#reportLayout"),
  productRelevance: document.querySelector("#productRelevance"),
  requirementsSummary: document.querySelector("#requirementsSummary"),
  deliverySummary: document.querySelector("#deliverySummary"),
  recommendedDraft: document.querySelector("#recommendedDraft"),
  shootingPlan: document.querySelector("#shootingPlan"),
  publishingPackage: document.querySelector("#publishingPackage"),
  sourceSummary: document.querySelector("#sourceSummary"),
  qualitySummary: document.querySelector("#qualitySummary"),
  asrSummary: document.querySelector("#asrSummary"),
  distillationReport: document.querySelector("#distillationReport"),
  trafficAssessment: document.querySelector("#trafficAssessment"),
  calibrationPlan: document.querySelector("#calibrationPlan"),
  audienceInsights: document.querySelector("#audienceInsights"),
  contentPackage: document.querySelector("#contentPackage"),
  localizationSummary: document.querySelector("#localizationSummary"),
  evidenceRisks: document.querySelector("#evidenceRisks"),
  riskReview: document.querySelector("#riskReview"),
  quickSourceMeta: document.querySelector("#quickSourceMeta"),
  quickSummary: document.querySelector("#quickSummary"),
  quickWhatHappens: document.querySelector("#quickWhatHappens"),
  quickWhyItWorks: document.querySelector("#quickWhyItWorks"),
  quickTransferable: document.querySelector("#quickTransferable"),
  quickOriginalAngle: document.querySelector("#quickOriginalAngle"),
  scriptNextButton: document.querySelector("#scriptNextButton"),
  pathway: document.querySelector("#pathway"),
  pathwayFill: document.querySelector("#pathwayFill"),
  pathwaySummary: document.querySelector("#pathwaySummary"),
  stageScriptStatus: document.querySelector("#stageScriptStatus"),
  stageShootingStatus: document.querySelector("#stageShootingStatus"),
  stagePublishStatus: document.querySelector("#stagePublishStatus"),
  copyScriptButton: document.querySelector("#copyScriptButton"),
  sourceLink: document.querySelector("#sourceLink"),
  copyFeedback: document.querySelector("#copyFeedback"),
  toast: document.querySelector("#toast")
};

const LABELS = {
  report: "报告", report_id: "报告编号", report_schema_version: "报告格式版本", source: "来源",
  platform: "平台", title: "标题", author: "作者", name: "名称", account: "账号", url: "原始链接",
  canonical_url: "原始链接", video_id: "作品编号", aweme_id: "作品编号", published_at: "发布时间",
  duration: "时长", transcript: "字幕", source_text: "来源文本", fetched_at: "采集时间", label: "样本",
  data_quality: "数据完整度", evidence_boundary: "证据边界", acquisition_mode: "资料获取方式",
  report_type: "报告类型", pending: "待确认项", material: "已接收材料", transcript_excerpt: "字幕摘录",
  transcript_characters: "字幕字符数", product_context_received: "已提供产品背景", level: "等级",
  grade: "等级", score: "评分", status: "状态", confidence: "置信度", evidence: "已有证据", type: "类型",
  value: "内容", facts: "事实", inferences: "推断", limitations: "限制", missing: "缺失资料",
  missing_fields: "缺失资料", warnings: "注意事项", signals: "判断依据", summary: "内容摘要",
  content: "作品内容", core_claim: "核心主张", central_idea: "核心观点", theme: "内容主题",
  content_demonstration: "内容演示",
  topic: "内容主题", audience: "目标受众", audience_pains: "受众痛点", pain_points: "受众痛点",
  hook: "原内容钩子", hook_mechanism: "钩子机制", hook_analysis: "钩子拆解", structure: "内容结构",
  content_structure: "内容结构", key_points: "关键信息", persuasion: "说服路径", emotional_triggers: "情绪触发",
  reusable_patterns: "可迁移模式", transferable_patterns: "可迁移模式", reusable_insights: "可迁移洞察",
  audience_insights: "受众洞察", audience_profile: "受众画像", objections: "受众顾虑", motivations: "行动动机",
  traffic_assessment: "流量判断", traffic_potential: "流量潜力", traffic_drivers: "流量驱动因素",
  traffic_limitations: "流量限制", performance_hypothesis: "表现假设", calibration: "验证计划",
  prediction_status: "验证状态", testable_hypothesis: "可检验假设", disconfirming_signal: "推翻信号",
  tracking_fields: "发布后追踪指标", next_review: "下一次复盘", content_package: "内容包", titles: "标题备选",
  title_options: "标题备选", hooks: "开头钩子", hook_options: "开头钩子", copywriting: "发布文案",
  caption: "发布文案", captions: "发布文案", script: "完整口播脚本", full_script: "完整口播脚本",
  script_text: "完整口播脚本", full_text: "完整口播脚本", spoken_script: "完整口播脚本",
  voiceover_script: "完整口播脚本", final_script: "完整口播脚本", script_draft: "口播脚本草案",
  talking_points: "核心话术", selling_points: "卖点话术", calls_to_action: "行动引导",
  call_to_action: "行动引导", cta: "行动引导", shot_list: "画面建议", storyboard: "分镜建议",
  cover_copy: "封面文案", hashtags: "话题标签", risk_review: "风险审阅", compliance: "合规审阅",
  risk_gate: "发布前审核", publishable: "当前可发布", overall: "总体判断", risk_level: "风险等级",
  health_claims: "健康宣称风险", platform_risks: "平台风险", risky_phrases: "高风险表述",
  rewrite_suggestions: "改写建议", safer_alternatives: "稳妥替代表述", disclaimers: "免责声明建议",
  pending_confirmation: "待确认项", items: "审阅项", message: "说明", analysis_mode: "分析方式",
  snapshot_at: "证据快照时间", followers: "粉丝数", followers_display: "粉丝数", duration_seconds: "时长（秒）",
  chapters: "页面章节", mentioned_brand_terms: "公开产品词", metrics: "公开互动快照", likes: "点赞",
  comments: "评论", favorites: "收藏", shares: "分享", views: "播放量", scope: "数据口径",
  public_comment_summary: "热门评论摘要", sample_size: "样本条数", raw_comments_included: "是否保留原文",
  observed_patterns: "观察到的方向", purchase_demand: "购买需求证据", limitation: "证据限制", mode: "处理模式",
  confirmed_structure: "可确认的内容结构", audience_problem: "受众问题", pattern: "机制模式",
  source_signal: "来源信号", transfer_rule: "迁移规则", suggested_structure: "建议结构", order: "顺序",
  purpose: "目的", description: "说明", basis: "依据", comment_demand: "评论需求", observed_items: "已观察项目",
  hypotheses_to_validate: "待验证假设", do_not_copy: "不可迁移部分", method: "判断方法", conclusion: "结论",
  not_a_prediction: "不是播放预测", count_ratios: "公开计数比值", favorites_to_likes: "收藏/点赞",
  shares_to_likes: "分享/点赞", boundary: "适用边界", evidence_level: "证据强度", observed: "已观察到的现象",
  needs_hypotheses: "需求假设", cannot_determine: "当前不能判断", positioning: "创作定位",
  original_angle: "原创角度", risk: "风险", segments: "分段脚本", time: "时间", voiceover: "口播",
  visual: "画面", screen_text: "屏幕文字", post_copy: "发布文案", body: "正文", tags: "话题标签",
  comment_replies: "评论回复话术", primary: "主要行动引导", question: "观众问题", reply: "建议回复",
  hits: "需人工审核项", blocked_phrases: "禁止表达", review_checklist: "发布前清单", disclaimer: "风险说明",
  text: "命中内容", reason: "原因", action: "处理方式", customer_facts: "客户产品事实", distillation: "内容蒸馏",
  delivery: "交付状态", recommended_draft: "唯一推荐稿", recommended_script: "唯一推荐稿",
  recommended_script_title: "推荐稿标题", shooting_plan: "拍摄执行表", shooting_table: "拍摄执行表",
  publishing_package: "发布内容包", localization: "地区本地化", evidence_and_risks: "证据与风险",
  evidence_and_risk: "证据与风险", asr: "语音转写", product_requirements: "商品资料审核",
  is_primary: "是否为唯一推荐稿", selection_reason: "选择理由", source_basis: "来源依据",
  source_material_excerpt: "来源材料摘录", subtitle: "字幕", product_proof: "商品证明", sound: "声音",
  columns: "执行表字段", rows: "执行步骤", blocking_items: "待核验项", enabled: "是否启用", applied: "是否已应用",
  requested: "用户选择", provider: "实际服务", provider_type: "服务类型", media_required: "是否需要媒体文件",
  fallback: "降级方式", fallback_order: "降级顺序", verification_status: "核验状态", submitted: "已提交资料",
  structured: "结构化商品资料", legacy_context: "补充说明", placeholder_policy: "占位规则",
  transcript_status: "转写状态", product_verification: "商品核验状态", source_evidence: "来源证据",
  selected_provider: "已选择服务", provider_order: "服务选择顺序", external_api_preferred: "是否优先外部 API",
  paid_api_called: "是否调用付费 API", providers: "可用服务检查", configured: "是否已配置",
  product_relevance: "商品属性", has_product: "是否具有商品属性", product_fields_applicable: "是否需要商品字段",
  required_for: "资料使用阶段", follow_up: "后续建议", source: "判断来源",
  rule_based_status: "规则初判", rule_based_confidence: "规则置信度", requirements: "资料盘点",
  blocking_for_interpretation: "当前解读必需", optional_enhancements: "可选增强",
  product_for_rewrite_or_publish: "商品改写或发布资料", interpretation_blocked_by_product: "是否阻塞普通解读",
  conditional_missing_fields: "确认后可能需要", scope: "适用阶段",
  acquisition: "采集记录", job_id: "采集任务", manifest_url: "证据清单", completed_at: "采集完成时间",
  evidence_strength: "证据强度", source_artifact: "来源证据文件", sha256: "文件校验值",
  artifact_name: "证据文件", artifact_url: "证据入口", segment_count: "字幕分段数",
  character_count: "字幕字符数", runtime_public_snapshot: "本次运行时公开采集",
  reviewed_fixture: "预先审阅样本"
};

const VALUE_LABELS = {
  douyin: "抖音", tiktok: "TikTok", active: "已接通", planned: "计划接入", unknown: "未知平台",
  completed: "已完成", needs_input: "需要补充资料", partial: "部分证据", unsupported: "暂不支持",
  ok: "正常", fixture: "预先审阅的演示样本", not_run: "尚未执行", user_supplied_url: "用户提交的公开链接",
  transcript_fallback: "用户补充字幕", submitted: "用户已提交", partial_evidence_record: "部分证据记录",
  reviewed_public_fixture: "预先审阅的公开演示样本", public_url: "公开链接", public_metadata: "公开元数据",
  public_metrics: "公开互动数据", chapter_summary: "章节摘要", comment_observation: "评论观察",
  verified: "已核实", verified_snapshot: "已核实快照", limited_snapshot: "有限快照",
  limited_public_sample: "有限公开评论样本", public_metadata_and_chapter_evidence: "公开元数据与章节快照",
  public_snapshot_without_views: "公开快照（不含播放量）", not_started: "尚未开始", low: "低", medium: "中",
  high: "高", critical: "严重", needs_human_review: "需人工审核", human_review_required: "需人工审核",
  research_draft: "研究稿", publish_ready: "已具备发布条件", blocked_needs_analysis: "等待内容分析",
  blocked_needs_script: "等待完整脚本", future_disabled: "本版本未启用", external: "外部 API", local: "本地处理",
  auto: "自动选择", disabled: "已关闭", unverified_user_submission: "用户提交，尚未核验",
  missing_or_incomplete: "缺失或不完整", user_supplied_unverified: "用户提供，尚未核验",
  submitted_needs_verification: "已提交，仍需核验", external_api: "外部 API", user_supplied_transcript: "用户提供的文字",
  not_needed: "无需转写", needs_media: "等待媒体文件", not_configured: "尚未配置",
  runtime_public_snapshot: "本次运行时公开采集", reviewed_fixture: "预先审阅样本",
  has_product: "具有商品属性", no_product: "无商品属性", needs_confirmation: "商品属性待确认",
  rule_based: "规则初判", model: "模型判断", user_confirmation: "用户确认", client_product_input: "用户商品资料",
  rule_based_with_model_note: "规则优先，保留模型建议",
  product_rewrite_or_publish_only: "仅商品改写或正式发布时",
  not_applicable: "不适用", local_asr: "本地语音转写",
  local_asr_required: "需要本地语音转写", retrieved_public_metadata: "公开页面信息",
  retrieved_ephemeral_browser: "公开页面临时采集", retrieved: "已获取",
  transcript_path: "字幕来源", timed_transcript: "带时间码字幕",
  pending_runtime_step: "等待运行处理", runtime_generated: "本次运行生成",
  automatic_acquisition: "自动采集与转写", registered_fixture: "预先审阅样本",
  worker_transcript: "自动取得的字幕", missing: "缺失"
};

const INTERNAL_REPORT_KEYS = new Set([
  "acquisition", "generation", "provider_metadata", "usage", "request_id", "response_format",
  "job_id", "manifest_url", "manifest_schema_version", "stable_id", "analysis_ready",
  "source_artifact", "evidence_summary", "artifact_name", "artifact_url", "sha256", "size_bytes",
  "prompt_tokens", "completion_tokens", "total_tokens", "prompt_cache_hit_tokens",
  "prompt_cache_miss_tokens"
]);

const SCRIPT_KEYS = new Set([
  "script", "full_script", "script_text", "full_text", "spoken_script", "voiceover_script",
  "final_script", "script_draft", "recommended_draft", "recommended_script",
  "完整脚本", "口播脚本", "完整口播脚本"
]);

let demoSample = null;
let currentScript = "";
let feedbackTimer = null;
let toastTimer = null;
let analysisInFlight = false;
let transcriptionInFlight = false;
let paidContentEnabled = false;
let requestedAnalysisMode = "quick";
let analysisProgressLabel = "";
let previousGateStates = ["locked", "locked", "locked", "locked"];
let productRelevanceOverride = null;
let currentProductRelevance = null;
let currentProductRequirements = null;

const MAX_MEDIA_BYTES = 25 * 1024 * 1024;
const ACQUISITION_POLL_INTERVAL_MS = 1400;
const ACQUISITION_TIMEOUT_MS = 5 * 60 * 1000;

function isPresent(value) {
  if (value === null || value === undefined || value === "") return false;
  if (Array.isArray(value)) return value.length > 0;
  if (typeof value === "object") return Object.keys(value).length > 0;
  return true;
}

function textValue(value) {
  if (typeof value === "boolean") return value ? "是" : "否";
  if (typeof value === "number") return String(value);
  if (typeof value === "string" && VALUE_LABELS[value]) return VALUE_LABELS[value];
  return String(value ?? "");
}

function labelFor(key) {
  if (INTERNAL_REPORT_KEYS.has(key)) return "";
  if (LABELS[key]) return LABELS[key];
  if (/^[\u3400-\u9fff]/u.test(key)) return key;
  return "";
}

function clearNode(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
}

function createElement(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function createTag(text, tone = "") {
  return createElement("li", `tag${tone ? ` tag--${tone}` : ""}`, textValue(text));
}

function toneForKey(key, value) {
  const combined = `${key} ${textValue(value)}`.toLowerCase();
  if (key === "publishable") return value === true ? "fact" : "risk";
  if (/risk|danger|违规|风险|高风险|不通过|禁止/.test(combined)) return "risk";
  if (/warning|missing|unverified|research|待确认|注意|缺失|有限|中风险|研究稿|未核验/.test(combined)) return "warning";
  if (/fact|evidence|事实|已验证|充分|低风险/.test(combined)) return "fact";
  return "";
}

function isPrimitive(value) {
  return value === null || ["string", "number", "boolean"].includes(typeof value);
}

function renderPrimitive(value, key = "") {
  const content = textValue(value);
  const isScript = SCRIPT_KEYS.has(key) || /完整.*脚本|口播.*脚本/.test(labelFor(key));
  return createElement("p", isScript ? "script-block" : "report-text", content);
}

function renderArray(values, key = "") {
  if (!values.length) return createElement("p", "empty-value", "暂无");
  if (values.every(isPrimitive) && values.every((value) => textValue(value).length < 30)) {
    const list = createElement("ul", "tag-list");
    const tone = toneForKey(key, values.join(" "));
    values.forEach((value) => list.appendChild(createTag(value, tone)));
    return list;
  }
  if (values.every(isPrimitive)) {
    const list = createElement("ol", "report-list");
    values.forEach((value) => list.appendChild(createElement("li", "", textValue(value))));
    return list;
  }
  const wrapper = createElement("div", "nested-list");
  values.forEach((value, index) => {
    const group = createElement("div", "nested-group");
    if (typeof value === "object" && value !== null) {
      group.appendChild(renderObject(value, `${labelFor(key)} ${index + 1}`));
    } else {
      group.appendChild(renderPrimitive(value, key));
    }
    wrapper.appendChild(group);
  });
  return wrapper;
}

function renderObject(object, fallbackTitle = "") {
  const wrapper = createElement("div", "object-content");
  const entries = Object.entries(object || {}).filter(
    ([key, value]) => isPresent(value) && labelFor(key)
  );
  if (!entries.length) {
    wrapper.appendChild(createElement("p", "empty-value", "暂无可展示内容"));
    return wrapper;
  }
  entries.forEach(([key, value]) => {
    if (key === "comment_replies") {
      const group = createElement("section", "data-group data-group--collapsible");
      group.appendChild(renderCollapsible(labelFor(key), value, key));
      wrapper.appendChild(group);
      return;
    }
    const group = createElement("section", "data-group");
    group.appendChild(createElement("h4", "", labelFor(key) || fallbackTitle));
    if (Array.isArray(value)) {
      group.appendChild(renderArray(value, key));
    } else if (typeof value === "object" && value !== null) {
      group.appendChild(renderObject(value));
    } else {
      group.appendChild(renderPrimitive(value, key));
    }
    wrapper.appendChild(group);
  });
  return wrapper;
}

function renderCollapsible(title, value, key = "") {
  const details = createElement("details", "report-collapse");
  details.appendChild(createElement("summary", "", title));
  const content = createElement("div", "report-collapse__content");
  if (Array.isArray(value)) content.appendChild(renderArray(value, key));
  else if (typeof value === "object" && value !== null) content.appendChild(renderObject(value));
  else content.appendChild(renderPrimitive(value, key));
  details.appendChild(content);
  return details;
}

function renderSideSection(container, title, data) {
  clearNode(container);
  const section = createElement("section", "side-section");
  section.appendChild(createElement("h3", "", title));
  if (!isPresent(data)) {
    section.appendChild(createElement("p", "empty-value", "暂无可用信息"));
    container.appendChild(section);
    return;
  }
  if (typeof data !== "object" || Array.isArray(data)) {
    section.appendChild(Array.isArray(data) ? renderArray(data) : renderPrimitive(data));
    container.appendChild(section);
    return;
  }
  const list = createElement("dl", "data-list");
  Object.entries(data)
    .filter(([key, value]) => (
      isPresent(value)
      && labelFor(key)
      && !["url", "canonical_url", "transcript", "source_text"].includes(key)
    ))
    .forEach(([key, value]) => {
      if (key === "public_comment_summary") {
        const row = createElement("div", "data-row data-row--collapsible");
        row.appendChild(renderCollapsible(labelFor(key), value, key));
        list.appendChild(row);
        return;
      }
      const row = createElement("div", "data-row");
      row.appendChild(createElement("dt", "", labelFor(key)));
      const detail = createElement("dd");
      if (Array.isArray(value)) detail.appendChild(renderArray(value, key));
      else if (typeof value === "object" && value !== null) detail.appendChild(renderObject(value));
      else {
        const tone = toneForKey(key, value);
        if (tone) {
          const tags = createElement("ul", "tag-list");
          tags.appendChild(createTag(value, tone));
          detail.appendChild(tags);
        } else {
          detail.textContent = textValue(value);
        }
      }
      row.appendChild(detail);
      list.appendChild(row);
    });
  if (!list.children.length) section.appendChild(createElement("p", "empty-value", "暂无可用信息"));
  else section.appendChild(list);
  container.appendChild(section);
}

function formatEvidenceTime(value) {
  if (!value) return "未记录";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return textValue(value);
  return parsed.toLocaleString("zh-CN", { hour12: false });
}

function appendEvidenceRow(list, label, value, className = "") {
  if (!isPresent(value)) return;
  const row = createElement("div", "metric-evidence__row");
  row.appendChild(createElement("dt", "", label));
  const detail = createElement("dd", className, textValue(value));
  row.appendChild(detail);
  list.appendChild(row);
}

function createMetricEvidence(metricKey, acquisition) {
  const details = createElement("details", "metric-evidence");
  details.dataset.evidenceFor = metricKey;
  details.appendChild(createElement("summary", "", "查看依据"));
  const content = createElement("div", "metric-evidence__content");
  const list = createElement("dl", "metric-evidence__list");
  appendEvidenceRow(list, "资料来源", acquisition?.evidence_strength);
  appendEvidenceRow(list, "采集时间", formatEvidenceTime(acquisition?.completed_at));
  content.appendChild(list);
  details.appendChild(content);
  return details;
}

function renderSourceSummary(container, source, acquisition = {}) {
  const overview = {};
  ["platform", "author", "content", "acquisition_mode", "retrieval_status", "missing"].forEach((key) => {
    if (isPresent(source?.[key])) overview[key] = source[key];
  });
  renderSideSection(container, "来源", overview);

  const metrics = source?.metrics;
  if (!metrics || typeof metrics !== "object") return;
  const entries = Object.entries(metrics).filter(([, value]) => isPresent(value));
  if (!entries.length) return;

  const section = createElement("section", "side-section metric-section");
  section.appendChild(createElement("h3", "", "公开数据"));
  const list = createElement("dl", "data-list metric-list");
  entries.forEach(([key, value]) => {
    const row = createElement("div", "data-row metric-row");
    row.appendChild(createElement("dt", "", labelFor(key)));
    const detail = createElement("dd");
    detail.appendChild(createElement("strong", "metric-row__value", textValue(value)));
    if (isPresent(acquisition)) detail.appendChild(createMetricEvidence(key, acquisition));
    row.appendChild(detail);
    list.appendChild(row);
  });
  section.appendChild(list);
  container.appendChild(section);
}

function renderReportSection(container, title, intro, data) {
  clearNode(container);
  if (!isPresent(data)) return;
  const section = createElement("section", "report-section");
  section.appendChild(createElement("h3", "", title));
  if (intro) section.appendChild(createElement("p", "section-intro", intro));
  if (Array.isArray(data)) section.appendChild(renderArray(data));
  else if (typeof data === "object" && data !== null) section.appendChild(renderObject(data));
  else section.appendChild(renderPrimitive(data));
  container.appendChild(section);
}

function renderCollapsibleReportSection(container, title, intro, data, summaryData = {}) {
  clearNode(container);
  if (!isPresent(data)) return;
  const section = createElement("section", "report-section");
  section.appendChild(createElement("h3", "", title));
  if (intro) section.appendChild(createElement("p", "section-intro", intro));
  if (isPresent(summaryData)) section.appendChild(renderObject(summaryData));
  section.appendChild(renderCollapsible("查看详细资料", data));
  container.appendChild(section);
}

function setProductContextVisibility(status) {
  if (!elements.productContextField) return;
  elements.productContextField.hidden = ["no_product", "needs_confirmation"].includes(status);
}

function confirmedProductRelevance(status) {
  const hasProduct = status === "has_product";
  return {
    status,
    has_product: hasProduct,
    confidence: "high",
    evidence: ["用户已在当前页面确认商品属性。"],
    reason: hasProduct
      ? "按用户确认，将商品资料用于后续改写或发布准备。"
      : "按用户确认，这条内容不按商品内容处理。",
    follow_up: hasProduct
      ? ["如需改写成自己的商品内容或正式发布，再补充可核验的商品事实。"]
      : ["继续按内容主题和方法解读，无需补商品资料。"],
    source: "user_confirmation"
  };
}

function applyProductRelevanceOverride(status) {
  productRelevanceOverride = status;
  currentProductRelevance = confirmedProductRelevance(status);
  const previous = currentProductRequirements || {};
  const conditional = Array.isArray(previous.conditional_missing_fields)
    ? previous.conditional_missing_fields
    : [];
  currentProductRequirements = {
    ...previous,
    status: status === "has_product" ? (conditional.length ? "needs_input" : "submitted_needs_verification") : "not_applicable",
    missing_fields: status === "has_product" ? conditional : [],
    conditional_missing_fields: []
  };
  renderProductRelevance(
    elements.productRelevance,
    currentProductRelevance,
    currentProductRequirements
  );
  setProductContextVisibility(status);
  if (status === "has_product") {
    elements.supplementDetails.open = true;
    elements.productContextInput.focus();
    showFormMessage("已确认是商品内容；商品资料只在改写或正式发布时使用。");
  } else {
    showFormMessage("已确认不是商品内容；普通解读不再要求商品资料。");
  }
  showToast("商品属性已记录", "ok");
}

function renderProductRelevance(container, relevance, requirements) {
  clearNode(container);
  if (!relevance || typeof relevance !== "object") {
    container.hidden = true;
    setProductContextVisibility(null);
    return;
  }
  const status = String(relevance.status || "needs_confirmation");
  const statusMeta = {
    has_product: { label: "具有商品属性", tone: "has" },
    no_product: { label: "无商品属性", tone: "none" },
    needs_confirmation: { label: "商品属性待确认", tone: "pending" }
  }[status] || { label: "商品属性待确认", tone: "pending" };

  container.hidden = false;
  container.className = "product-relevance product-relevance--" + statusMeta.tone;
  const heading = createElement("div", "product-relevance__heading");
  const titleWrap = createElement("div");
  titleWrap.appendChild(createElement("p", "eyebrow", "商品属性"));
  const title = createElement("h2", "", "这条内容是否在讲商品");
  title.id = "productRelevanceTitle";
  titleWrap.appendChild(title);
  heading.appendChild(titleWrap);
  heading.appendChild(createElement("span", "product-relevance__status", statusMeta.label));
  container.appendChild(heading);

  const body = createElement("div", "product-relevance__body");
  body.appendChild(createElement("p", "product-relevance__reason", relevance.reason || "当前证据不足，先保留待确认。"));

  const evidence = Array.isArray(relevance.evidence) ? relevance.evidence.filter(Boolean) : [];
  if (evidence.length) {
    const group = createElement("div", "product-relevance__group");
    group.appendChild(createElement("h3", "", "判断依据"));
    const list = createElement("ul", "report-list");
    evidence.forEach((item) => list.appendChild(createElement("li", "", textValue(item))));
    group.appendChild(list);
    body.appendChild(group);
  }

  const followUp = Array.isArray(relevance.follow_up) ? relevance.follow_up.filter(Boolean) : [];
  if (followUp.length) {
    const group = createElement("div", "product-relevance__group");
    group.appendChild(createElement("h3", "", "后续意见"));
    const list = createElement("ul", "report-list");
    followUp.forEach((item) => list.appendChild(createElement("li", "", textValue(item))));
    group.appendChild(list);
    body.appendChild(group);
  }

  if (status === "has_product") {
    const missing = Array.isArray(requirements?.missing_fields)
      ? requirements.missing_fields.filter(Boolean)
      : [];
    if (missing.length) {
      const group = createElement("div", "product-relevance__group product-relevance__group--requirements");
      group.appendChild(createElement("h3", "", "改写或正式发布前再补"));
      const list = createElement("ul", "report-list");
      missing.forEach((item) => list.appendChild(createElement("li", "", textValue(item))));
      group.appendChild(list);
      body.appendChild(group);
    }
  } else if (status === "no_product") {
    body.appendChild(createElement("p", "product-relevance__not-applicable", "不需要商品名称、核心卖点、规格或证明材料。"));
  } else {
    const actions = createElement("div", "product-relevance__actions");
    const confirmProduct = createElement("button", "button button--secondary", "这是商品内容");
    confirmProduct.type = "button";
    confirmProduct.addEventListener("click", () => applyProductRelevanceOverride("has_product"));
    const confirmNoProduct = createElement("button", "button button--secondary", "不是商品内容");
    confirmNoProduct.type = "button";
    confirmNoProduct.addEventListener("click", () => applyProductRelevanceOverride("no_product"));
    actions.append(confirmProduct, confirmNoProduct);
    body.appendChild(actions);
  }
  container.appendChild(body);
  setProductContextVisibility(status);
}

function appendRequirementGroup(container, title, items, emptyText = "") {
  const group = createElement("section", "requirements-summary__group");
  group.appendChild(createElement("h4", "", title));
  if (Array.isArray(items) && items.length) {
    const list = createElement("ul", "report-list");
    items.forEach((item) => list.appendChild(createElement("li", "", textValue(item))));
    group.appendChild(list);
  } else if (emptyText) {
    group.appendChild(createElement("p", "requirements-summary__empty", emptyText));
  }
  container.appendChild(group);
}

function renderRequirementsSummary(container, requirements, productRelevance) {
  clearNode(container);
  if (!requirements || typeof requirements !== "object") return;
  const section = createElement("section", "requirements-summary");
  section.appendChild(createElement("h3", "", "资料盘点"));
  const grid = createElement("div", "requirements-summary__grid");
  appendRequirementGroup(
    grid,
    "当前解读必需",
    requirements.blocking_for_interpretation,
    "当前解读没有必补资料。"
  );
  appendRequirementGroup(
    grid,
    "可选增强",
    requirements.optional_enhancements,
    "当前没有额外增强项。"
  );
  if (productRelevance?.status === "has_product") {
    appendRequirementGroup(
      grid,
      "商品改写或发布",
      requirements.product_for_rewrite_or_publish,
      "商品资料已提交，发布前仍需人工核验。"
    );
  }
  section.appendChild(grid);
  container.appendChild(section);
}

function objectWithout(value, omittedKeys) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return {};
  const omitted = new Set(omittedKeys);
  return Object.fromEntries(
    Object.entries(value).filter(([key, child]) => !omitted.has(key) && isPresent(child))
  );
}

function statusTone(status) {
  const value = String(status || "").toLowerCase();
  if (/blocked|risk|error|reject/.test(value)) return "risk";
  if (/unverified|research|missing|needs_|partial|disabled|pending/.test(value)) return "warning";
  if (/publish_ready|completed|verified|ready/.test(value)) return "fact";
  return "warning";
}

function renderDeliverySummary(container, delivery, publishState) {
  clearNode(container);
  const summary = createElement("div", `delivery-summary delivery-summary--${publishState.publishable ? "ready" : "research"}`);
  const copy = createElement("div", "delivery-summary__copy");
  copy.appendChild(createElement("strong", "", publishState.label));
  copy.appendChild(createElement("p", "", delivery?.message || publishState.note));
  summary.appendChild(copy);
  if (delivery?.label) {
    const tags = createElement("ul", "tag-list delivery-summary__tags");
    tags.appendChild(createTag(delivery.label, publishState.publishable ? "fact" : "warning"));
    summary.appendChild(tags);
  }
  container.appendChild(summary);
}

function renderRecommendedDraft(container, draft, legacyContent, publishState) {
  clearNode(container);
  const fallbackScript = legacyContent?.script || legacyContent?.full_script || legacyContent?.script_draft || null;
  const data = isPresent(draft) ? draft : fallbackScript;
  const block = createElement("section", "deliverable-block");
  if (!isPresent(data)) {
    block.classList.add("deliverable-block--blocked");
    block.appendChild(createElement("h4", "", "完整推荐稿尚未形成"));
    block.appendChild(createElement("p", "availability-note", "当前资料不足，系统没有把来源内容冒充原创成稿。"));
    container.appendChild(block);
    return;
  }
  const object = typeof data === "object" && !Array.isArray(data) ? data : {};
  const title = object.title || object.name || "推荐脚本";
  const fullText = typeof data === "string"
    ? data
    : object.full_text || object.script_text || object.full_script || object.voiceover_script || "";
  const header = createElement("div", "deliverable-block__header");
  const heading = createElement("div");
  heading.appendChild(createElement("p", "deliverable-kicker", "唯一推荐"));
  heading.appendChild(createElement("h4", "", title));
  header.appendChild(heading);
  const meta = createElement("ul", "tag-list deliverable-block__meta");
  const status = object.status || (publishState.publishable ? "publish_ready" : "research_draft");
  meta.appendChild(createTag(status, statusTone(status)));
  if (isPresent(object.duration_seconds)) meta.appendChild(createTag(`${object.duration_seconds} 秒`));
  header.appendChild(meta);
  block.appendChild(header);
  if (fullText) block.appendChild(renderPrimitive(fullText, "full_text"));
  else {
    block.classList.add("deliverable-block--blocked");
    block.appendChild(createElement("p", "availability-note", "尚无完整脚本；请先补齐分析所需资料。"));
  }
  const extras = objectWithout(object, ["status", "is_primary", "title", "name", "duration_seconds", "full_text", "script_text", "full_script", "voiceover_script"]);
  if (isPresent(extras)) {
    const details = createElement("details", "report-collapse deliverable-notes");
    details.appendChild(createElement("summary", "", "查看选择理由与资料边界"));
    const detailBody = createElement("div", "report-collapse__content");
    detailBody.appendChild(renderObject(extras));
    details.appendChild(detailBody);
    block.appendChild(details);
  }
  container.appendChild(block);
}

const SHOOTING_COLUMNS = {
  time: { label: "时间", aliases: ["time", "timecode", "timeline", "duration"] },
  visual: { label: "画面", aliases: ["visual", "shot", "scene"] },
  voiceover: { label: "口播", aliases: ["voiceover", "spoken_text", "narration"] },
  subtitle: { label: "字幕", aliases: ["subtitle", "screen_text", "on_screen_text"] },
  product_proof: { label: "商品证明", aliases: ["product_proof", "proof", "evidence"] },
  sound: { label: "声音", aliases: ["sound", "audio", "music"] },
  purpose: { label: "目的", aliases: ["purpose", "stage"] }
};

function normalizeShootingColumn(key) {
  return Object.entries(SHOOTING_COLUMNS).find(([, config]) => config.aliases.includes(key))?.[0] || key;
}

function flattenText(value) {
  if (!isPresent(value)) return "";
  if (isPrimitive(value)) return textValue(value);
  if (Array.isArray(value)) return value.map(flattenText).filter(Boolean).join("\n\n");
  return Object.entries(value)
    .filter(([, childValue]) => isPresent(childValue))
    .map(([childKey, childValue]) => {
      const text = flattenText(childValue);
      return text ? `${labelFor(childKey)}\n${text}` : "";
    })
    .filter(Boolean)
    .join("\n\n");
}

function shootingCell(row, key) {
  const aliases = SHOOTING_COLUMNS[key]?.aliases || [key];
  const matchedKey = aliases.find((alias) => isPresent(row?.[alias]));
  const value = matchedKey ? row[matchedKey] : "";
  return isPresent(value) ? flattenText(value) : "待补";
}

function renderShootingPlan(container, shootingPlan, legacyContent) {
  clearNode(container);
  const legacyRows = legacyContent?.script?.segments || [];
  const plan = Array.isArray(shootingPlan) ? { rows: shootingPlan } : (shootingPlan || {});
  const rows = Array.isArray(plan.rows)
    ? plan.rows
    : Array.isArray(plan.segments) ? plan.segments : legacyRows;
  const meta = createElement("div", "shooting-meta");
  if (plan.status) {
    const tags = createElement("ul", "tag-list");
    tags.appendChild(createTag(plan.status, statusTone(plan.status)));
    meta.appendChild(tags);
  }
  if (Array.isArray(plan.missing_fields) && plan.missing_fields.length) {
    meta.appendChild(createElement("p", "", `仍需补充：${plan.missing_fields.map(labelFor).join("、")}`));
  }
  if (meta.children.length) container.appendChild(meta);
  if (!rows.length) {
    const empty = createElement("div", "availability-panel");
    empty.appendChild(createElement("h4", "", "拍摄表等待完整脚本"));
    empty.appendChild(createElement("p", "", "当前没有足够内容生成可靠分镜，未使用占位内容冒充执行方案。"));
    container.appendChild(empty);
    return;
  }
  const requestedColumns = Array.isArray(plan.columns) && plan.columns.length
    ? plan.columns.map(normalizeShootingColumn)
    : ["time", "visual", "voiceover", "subtitle", "product_proof", "sound"];
  const columns = [...new Set(requestedColumns)];
  const wrapper = createElement("div", "shooting-table-wrap");
  wrapper.tabIndex = 0;
  wrapper.setAttribute("role", "region");
  wrapper.setAttribute("aria-label", "拍摄执行表，可横向滚动");
  const table = createElement("table", "shooting-table");
  table.appendChild(createElement("caption", "visually-hidden", "按时间排列的拍摄执行表"));
  const head = createElement("thead");
  const headRow = createElement("tr");
  columns.forEach((key) => headRow.appendChild(createElement("th", "", SHOOTING_COLUMNS[key]?.label || labelFor(key))));
  head.appendChild(headRow);
  table.appendChild(head);
  const body = createElement("tbody");
  rows.forEach((row) => {
    const tableRow = createElement("tr");
    columns.forEach((key) => {
      const cell = createElement("td", "", shootingCell(row, key));
      cell.dataset.label = SHOOTING_COLUMNS[key]?.label || labelFor(key);
      tableRow.appendChild(cell);
    });
    body.appendChild(tableRow);
  });
  table.appendChild(body);
  wrapper.appendChild(table);
  container.appendChild(wrapper);
}

function publishingFallback(content) {
  if (!content || typeof content !== "object") return {};
  const postCopy = content.post_copy || {};
  const fallback = {
    titles: postCopy.title_options || content.title_options,
    post_copy: postCopy.body || content.caption,
    tags: postCopy.tags || content.tags || content.hashtags,
    cta: content.cta || content.call_to_action,
    comment_replies: content.comment_replies
  };
  return Object.fromEntries(Object.entries(fallback).filter(([, value]) => isPresent(value)));
}

function assessPublishability(payload, report, partial) {
  const evidenceRisk = report.evidence_and_risk || report.evidence_and_risks || {};
  const candidates = [report.delivery, report.risk_gate, evidenceRisk.risk_gate, report.recommended_script, report.recommended_draft]
    .filter((item) => item && typeof item === "object");
  const explicitValues = candidates.filter((item) => typeof item.publishable === "boolean").map((item) => item.publishable);
  const statuses = candidates.map((item) => String(item.status || "").toLowerCase());
  statuses.push(
    String(report.analysis_mode || "").toLowerCase(),
    String(report.publishing_package?.status || "").toLowerCase(),
    String(report.shooting_table?.status || report.shooting_plan?.status || "").toLowerCase()
  );
  const responseStatus = String(payload.status || "").toLowerCase();
  const blocked = partial
    || !["completed", "complete", "success"].includes(responseStatus)
    || statuses.some((status) => /research|blocked|needs_|human_review|required|partial/.test(status))
    || explicitValues.includes(false);
  const publishable = !blocked && explicitValues.includes(true);
  return publishable
    ? { publishable: true, label: "已具备发布条件", note: "系统已收到明确的可发布状态；仍建议保留最终人工复核。" }
    : { publishable: false, label: "可参考，发布前核对", note: "先核对商品事实、来源证据和风险表述，再决定是否发布。" };
}

function findScript(value, key = "") {
  if (!isPresent(value)) return "";
  const scriptLike = SCRIPT_KEYS.has(key) || /完整.*脚本|口播.*脚本/.test(labelFor(key));
  if (scriptLike) {
    if (typeof value === "object" && value !== null) {
      const explicitScript = value.full_text || value.script_text || value.full_script || value.voiceover_script;
      if (typeof explicitScript === "string") return explicitScript;
      if (["recommended_script", "recommended_draft"].includes(key)) return "";
    }
    return flattenText(value);
  }
  if (typeof value === "string") return "";
  if (Array.isArray(value)) {
    return value.map((item) => findScript(item, key)).sort((a, b) => b.length - a.length)[0] || "";
  }
  if (typeof value === "object") {
    const matches = Object.entries(value).map(([childKey, childValue]) => findScript(childValue, childKey)).filter(Boolean).sort((a, b) => b.length - a.length);
    return matches[0] || "";
  }
  return "";
}

function safeHttpUrl(rawValue) {
  const raw = textValue(rawValue).trim();
  const candidate = raw.match(/https?:\/\/[^\s]+/i)?.[0] || raw;
  try {
    const parsed = new URL(candidate);
    return ["http:", "https:"].includes(parsed.protocol) ? parsed.href : "";
  } catch {
    return "";
  }
}

function sourceUrlFrom(report, fallback) {
  const source = report?.source;
  const candidate = source && typeof source === "object" ? source.url || source.canonical_url || source.source_url : "";
  return safeHttpUrl(candidate || fallback || "");
}

/* ---------- 通关路径状态机 ---------- */
const GATE_STATE_TEXT = {
  locked: "未解锁",
  active: "进行中",
  pending: "待补充",
  cleared: "已通关",
  blocked: "受阻"
};

function computeGateStates(payload, report, publishState) {
  const status = String(payload.status || "").toLowerCase();
  const hasReport = isPresent(report);
  const recommended = report?.recommended_script || report?.recommended_draft || {};
  const scriptText = typeof recommended === "object"
    ? (recommended.full_text || recommended.script_text || recommended.full_script || recommended.voiceover_script)
    : "";
  const shootingRows = report?.shooting_table?.rows
    || report?.shooting_table?.segments
    || report?.shooting_plan?.rows
    || report?.shooting_plan?.segments
    || [];
  const hasPublishing = isPresent(report?.publishing_package) || isPresent(report?.evidence_and_risk) || isPresent(report?.evidence_and_risks);

  const states = ["locked", "locked", "locked", "locked"];

  // 关卡 1：读懂链接
  if (status === "completed" || status === "complete" || status === "success" || status === "partial") {
    states[0] = "cleared";
  } else if (status === "needs_input" || status === "needs-input") {
    states[0] = "pending";
  } else if (status === "unsupported") {
    states[0] = "blocked";
  }

  // 关卡 2/3/4 仅在拿到报告后才有内容
  if (hasReport) {
    states[1] = isPresent(scriptText) ? "cleared" : "locked";
    states[2] = Array.isArray(shootingRows) && shootingRows.length ? "cleared" : "locked";
    states[3] = hasPublishing
      ? (publishState?.publishable ? "cleared" : "pending")
      : "locked";
  }

  // 明确需要人工处理的关卡保留“待补充”，只把尚未开始的下一关标为进行中。
  const nextActive = states.findIndex((s) => s === "locked");
  if (nextActive > 0 && states[0] !== "blocked" && states[0] !== "locked") {
    states[nextActive] = "active";
  }
  return states;
}

function updatePathway(states) {
  const gateButtons = elements.pathway.querySelectorAll(".gate");
  let clearedCount = 0;
  states.forEach((state, index) => {
    const btn = gateButtons[index];
    if (!btn) return;
    btn.classList.remove("gate--locked", "gate--active", "gate--cleared", "gate--blocked");
    btn.classList.add(`gate--${state}`);
    const stateEl = btn.querySelector(".gate__state");
    if (stateEl) stateEl.textContent = GATE_STATE_TEXT[state];
    if (state === "cleared") clearedCount++;
    if (state === "cleared" && previousGateStates[index] !== "cleared") {
      btn.classList.add("is-just-cleared");
      window.setTimeout(() => btn.classList.remove("is-just-cleared"), 640);
    }
    // 同步关卡状态徽标
    const chip = [elements.stageScriptStatus, elements.stageShootingStatus, elements.stagePublishStatus][index - 1];
    if (chip) {
      chip.className = "stage__status";
      if (state === "cleared") { chip.classList.add("stage__status--cleared"); chip.textContent = "已通关"; }
      else if (state === "blocked") { chip.classList.add("stage__status--blocked"); chip.textContent = "受阻"; }
      else if (state === "active" || state === "pending") { chip.classList.add("stage__status--pending"); chip.textContent = state === "active" ? "进行中" : "待补充"; }
      else chip.textContent = "未解锁";
    }
  });
  previousGateStates = states.slice();

  const fillPct = clearedCount > 0 ? ((clearedCount - 1) / (states.length - 1)) * 100 : 0;
  elements.pathwayFill.style.width = `${fillPct}%`;
  elements.pathwaySummary.textContent = `通关进度 ${clearedCount} / ${states.length}`;
  elements.pathwaySummary.classList.toggle("pathway__summary--all", clearedCount === states.length);
}

function resetPathway() {
  previousGateStates = ["locked", "locked", "locked", "locked"];
  updatePathway(previousGateStates);
  document.querySelectorAll(".stage").forEach((s) => s.classList.remove("is-revealed"));
}

function revealStages() {
  const stages = document.querySelectorAll(".stage");
  stages.forEach((stage, index) => {
    window.setTimeout(() => stage.classList.add("is-revealed"), 140 + index * 150);
  });
}

function navigateToGate(button) {
  const targetId = button.dataset.target;
  const target = targetId ? document.getElementById(targetId) : null;
  if (target) {
    target.scrollIntoView({ behavior: "smooth", block: "start" });
    target.animate(
      [{ boxShadow: "0 0 0 3px rgba(8,127,91,0.35)" }, { boxShadow: "0 0 0 3px rgba(8,127,91,0)" }],
      { duration: 900, easing: "ease-out" }
    );
  }
}

/* ---------- 反馈 ---------- */
function showToast(message, tone = "") {
  elements.toast.textContent = message;
  elements.toast.className = "toast";
  if (tone) elements.toast.classList.add(`toast--${tone}`);
  elements.toast.classList.add("is-visible");
  window.clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => elements.toast.classList.remove("is-visible"), 2600);
}

function showCopyFeedback(message) {
  window.clearTimeout(feedbackTimer);
  elements.copyFeedback.textContent = message;
  elements.copyFeedback.classList.add("is-visible");
  feedbackTimer = window.setTimeout(() => elements.copyFeedback.classList.remove("is-visible"), 2200);
}

/* ---------- 控件状态 ---------- */
function selectedMediaFile() {
  return elements.mediaFileInput.files?.[0] || null;
}

function updateBusyControls() {
  const isBusy = analysisInFlight || transcriptionInFlight;
  const mediaReady = Boolean(selectedMediaFile()) && selectedMediaFile().size <= MAX_MEDIA_BYTES;
  elements.analyzeButton.disabled = isBusy;
  elements.demoButton.disabled = isBusy;
  elements.asrStrategySelect.disabled = isBusy;
  elements.mediaFileInput.disabled = isBusy;
  elements.transcribeButton.disabled = isBusy || !mediaReady;
  elements.analyzeButton.classList.toggle("is-loading", analysisInFlight);
  elements.transcribeButton.classList.toggle("is-loading", transcriptionInFlight);
  elements.analyzeLabel.textContent = analysisInFlight
    ? (analysisProgressLabel || (requestedAnalysisMode === "full" ? "生成中" : "解读中"))
    : (requestedAnalysisMode === "full" ? "生成完整脚本" : "快速看懂");
  elements.transcribeLabel.textContent = transcriptionInFlight ? "转写中" : "转写到字幕框";
  elements.resultArea.setAttribute("aria-busy", String(analysisInFlight));
}

function setLoading(isLoading) {
  analysisInFlight = isLoading;
  if (!isLoading) analysisProgressLabel = "";
  updateBusyControls();
}

function setAnalysisProgress(label) {
  analysisProgressLabel = label;
  updateBusyControls();
}

function setTranscribing(isLoading) {
  transcriptionInFlight = isLoading;
  updateBusyControls();
}

function formatFileSize(bytes) {
  if (!Number.isFinite(bytes) || bytes < 0) return "大小未知";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function showTranscriptionStatus(tone = "info", message = "") {
  elements.transcriptionStatus.hidden = !message;
  elements.transcriptionStatus.className = `transcription-status transcription-status--${tone}`;
  elements.transcriptionStatus.textContent = message;
}

function updateMediaSelection() {
  const file = selectedMediaFile();
  showTranscriptionStatus();
  if (!file) {
    elements.mediaFileMeta.textContent = "尚未选择文件";
    updateBusyControls();
    return;
  }
  elements.mediaFileMeta.textContent = `${file.name} · ${formatFileSize(file.size)}`;
  if (file.size === 0) showTranscriptionStatus("error", "文件为空，请重新选择。");
  else if (file.size > MAX_MEDIA_BYTES) showTranscriptionStatus("error", "文件超过 25 MB，请压缩或截取后重试。");
  updateBusyControls();
}

function resetReportActions() {
  currentScript = "";
  currentProductRelevance = null;
  currentProductRequirements = null;
  clearNode(elements.productRelevance);
  elements.productRelevance.hidden = true;
  clearNode(elements.requirementsSummary);
  elements.copyScriptButton.hidden = true;
  elements.copyScriptButton.textContent = "复制研究稿";
  elements.sourceLink.hidden = true;
  elements.sourceLink.removeAttribute("href");
}

function showFormMessage(message = "", tone = "warning") {
  elements.formMessage.hidden = !message;
  elements.formMessage.textContent = message;
  elements.formMessage.style.borderLeftColor = tone === "error" ? "var(--risk)" : "var(--warning)";
}

function validateUrl() {
  const raw = elements.urlInput.value.trim();
  elements.urlInput.removeAttribute("aria-invalid");
  elements.urlError.textContent = "";
  if (!raw) {
    elements.urlInput.setAttribute("aria-invalid", "true");
    elements.urlError.textContent = "请先粘贴一个公开内容链接。";
    elements.urlInput.focus();
    return false;
  }
  try {
    const matchedUrl = raw.match(/https?:\/\/[^\s]+/i)?.[0] || raw;
    const parsed = new URL(matchedUrl);
    if (!["http:", "https:"].includes(parsed.protocol)) throw new Error("invalid protocol");
  } catch {
    elements.urlInput.setAttribute("aria-invalid", "true");
    elements.urlError.textContent = "链接格式不正确，请检查后重试。";
    elements.urlInput.focus();
    return false;
  }
  return true;
}

/* ---------- 渲染：状态与报告 ---------- */
function showLoading() {
  resetReportActions();
  resetPathway();
  elements.resultArea.hidden = false;
  elements.reportLayout.hidden = true;
  clearNode(elements.statePanel);
  const panel = createElement("div", "loading-panel");
  panel.setAttribute("aria-label", "正在分析内容");
  const side = createElement("div", "skeleton-column");
  side.appendChild(createElement("div", "skeleton-line skeleton-line--short"));
  side.appendChild(createElement("div", "skeleton-line skeleton-line--medium"));
  side.appendChild(createElement("div", "skeleton-block"));
  const main = createElement("div", "skeleton-column");
  main.appendChild(createElement("div", "skeleton-line skeleton-line--short"));
  main.appendChild(createElement("div", "skeleton-line skeleton-line--medium"));
  main.appendChild(createElement("div", "skeleton-block"));
  main.appendChild(createElement("div", "skeleton-line"));
  main.appendChild(createElement("div", "skeleton-line skeleton-line--medium"));
  panel.append(side, main);
  elements.statePanel.appendChild(panel);
}

function showStateMessage(type, title, message, missing = []) {
  resetReportActions();
  elements.resultArea.hidden = false;
  elements.reportLayout.hidden = true;
  clearNode(elements.statePanel);
  const panel = createElement("div", `state-message state-message--${type}`);
  panel.appendChild(createElement("h2", "", title));
  panel.appendChild(createElement("p", "", message));
  if (Array.isArray(missing) && missing.length) {
    const list = createElement("ul", "missing-list");
    missing.forEach((item) => {
      const text = typeof item === "object" && item !== null
        ? item.label || item.message || item.field || JSON.stringify(item)
        : item;
      list.appendChild(createElement("li", "", textValue(text)));
    });
    panel.appendChild(list);
  }
  elements.statePanel.appendChild(panel);
}

function quickList(value, fallback = []) {
  const source = Array.isArray(value) ? value : fallback;
  return source.map((item) => textValue(item).trim()).filter(Boolean).slice(0, 3);
}

function renderQuickList(node, values) {
  clearNode(node);
  values.forEach((value) => node.appendChild(createElement("li", "", value)));
  if (!values.length) node.appendChild(createElement("li", "gate-view__empty", "当前证据不足，先补充字幕或视频。"));
}

function renderQuickView(payload, report, source, partial = false) {
  const quick = report.quick_result || payload.quick_result || {};
  const distillation = report.distillation || payload.distillation || {};
  const quickSummary = typeof quick.summary === "string" ? quick.summary.trim() : quick.summary;
  const topic = typeof distillation.topic === "string" ? distillation.topic.trim() : "";
  const summary = quickSummary || topic || "当前证据有限，先查看已确认的信息和缺口。";
  const whatHappens = quickList(quick.what_happens, quickList(distillation.confirmed_structure, [summary]));
  const whyItWorks = quickList(quick.why_it_works, quickList(distillation.transferable_patterns, ["先制造一个具体问题，再给出解释和行动建议。"]));
  const transferable = quickList(quick.transferable, quickList(distillation.transferable_patterns, ["借鉴结构和方法，不复用原句、原案例或未经核验的功效。"]));

  elements.quickSummary.textContent = summary;
  elements.quickSourceMeta.textContent = source.author?.name
    ? `来源：${source.author.name}`
    : (partial ? "证据有限 · 需要补充资料" : "来源信息已读取");
  renderQuickList(elements.quickWhatHappens, whatHappens);
  renderQuickList(elements.quickWhyItWorks, whyItWorks);
  renderQuickList(elements.quickTransferable, transferable);
  elements.quickOriginalAngle.textContent = quick.original_angle
    ? `原创方向：${quick.original_angle}`
    : "原创方向：先讲清方法，再结合自己的真实资料。";
  elements.scriptNextButton.textContent = currentScript ? "查看我的原创稿" : "补充资料生成原创稿";
}

function showCompleted(payload, partial = false) {
  const report = payload.report || payload;
  const source = report.source || payload.source || {};
  const diagnostics = payload.diagnostics || {};
  const acquisition = diagnostics.acquisition || source.acquisition || {};
  const delivery = report.delivery || payload.delivery || {};
  const recommended = report.recommended_script || report.recommended_draft || payload.recommended_script || payload.recommended_draft || {};
  const shooting = report.shooting_table || report.shooting_plan || payload.shooting_table || payload.shooting_plan || {};
  const publishing = report.publishing_package || payload.publishing_package || {};
  const localization = report.localization || payload.localization || {};
  const evidenceRisk = report.evidence_and_risk || report.evidence_and_risks || payload.evidence_and_risk || payload.evidence_and_risks || {};
  const asr = report.asr || payload.asr || source.asr || {};
  const distillation = report.distillation || payload.distillation || {};
  const traffic = report.traffic_assessment || payload.traffic_assessment || {};
  const calibration = report.calibration || payload.calibration || {};
  const audience = report.audience_insights || payload.audience_insights || {};
  const content = report.content_package || payload.content_package || {};
  const risks = report.compliance || report.risk_review || report.risk_gate || payload.compliance || payload.risk_review || {};
  const productRelevance = report.product_relevance || payload.product_relevance || null;
  const productRequirements = report.product_requirements || payload.product_requirements || null;
  const requirements = report.requirements || payload.requirements || null;
  const publishState = assessPublishability(payload, report, partial);

  elements.resultArea.hidden = false;
  elements.reportLayout.hidden = false;
  clearNode(elements.statePanel);
  currentProductRelevance = productRelevance;
  currentProductRequirements = productRequirements;
  renderProductRelevance(elements.productRelevance, productRelevance, productRequirements);
  renderRequirementsSummary(elements.requirementsSummary, requirements, productRelevance);

  if (payload.message && !isPresent(report.quick_result)) {
    const tone = partial ? "warning" : "info";
    const title = partial ? "研究稿已生成，仍需补充资料" : "结果说明";
    const note = createElement("div", `state-message state-message--${tone}`);
    note.appendChild(createElement("h2", "", title));
    note.appendChild(createElement("p", "", payload.message));
    if (Array.isArray(payload.missing) && payload.missing.length) {
      const list = createElement("ul", "missing-list");
      payload.missing.forEach((item) => list.appendChild(createElement("li", "", textValue(item))));
      note.appendChild(list);
    }
    elements.statePanel.appendChild(note);
  }

  renderDeliverySummary(elements.deliverySummary, delivery, publishState);
  renderRecommendedDraft(elements.recommendedDraft, recommended, content, publishState);
  renderShootingPlan(elements.shootingPlan, shooting, content);

  const contentExtras = objectWithout(content, ["script", "full_script", "script_draft", "post_copy", "caption", "cta", "call_to_action", "comment_replies"]);
  renderReportSection(elements.contentPackage, "创作说明", "推荐稿之外的定位与原创边界。", contentExtras);
  renderReportSection(elements.publishingPackage, "发布配套", "标题、发布文案、行动引导和评论回复均继承当前交付状态。", isPresent(publishing) ? publishing : publishingFallback(content));

  renderSourceSummary(elements.sourceSummary, source, acquisition);
  renderSideSection(elements.qualitySummary, "证据边界", report.evidence_boundary || report.data_quality || payload.evidence_boundary || payload.data_quality || {});
  const asrDetails = objectWithout(asr, ["status", "mode", "selected_provider", "provider", "model", "language", "message", "paid_api_called", "media_required"]);
  const asrOverview = objectWithout(asr, Object.keys(asrDetails));
  renderSideSection(elements.asrSummary, "语音转写", asrOverview);
  if (isPresent(asrDetails)) {
    elements.asrSummary.querySelector(".side-section")?.appendChild(
      renderCollapsible("查看转写详情", asrDetails)
    );
  }

  if (isPresent(distillation)) {
    renderReportSection(elements.distillationReport, "内容蒸馏", "区分原始信息、推断和可迁移的方法。", distillation);
  } else {
    renderReportSection(elements.distillationReport, "已接收材料", "当前仅展示用户提交且可核对的资料。", report.material || {});
  }
  renderReportSection(elements.trafficAssessment, "流量判断", "基于当前证据解释潜力与限制，不承诺实际播放结果。", traffic);
  renderReportSection(elements.calibrationPlan, "验证计划", "把发布前判断变成可追踪、可推翻、可复盘的实验。", calibration);
  renderReportSection(elements.audienceInsights, "受众洞察", "用于校准表达角度、顾虑回应和行动动机。", audience);
  renderReportSection(
    elements.localizationSummary,
    "地区本地化",
    "v0.2 仅展示预留状态，不据此改写内容。",
    objectWithout(localization, ["requested"])
  );

  const evidenceDetail = evidenceRisk;
  const riskGateSummary = evidenceRisk?.risk_gate && typeof evidenceRisk.risk_gate === "object"
    ? objectWithout(evidenceRisk.risk_gate, ["hits", "review_checklist", "blocked_phrases"])
    : {};
  const evidenceSummary = {
    transcript_status: evidenceRisk?.transcript_status,
    product_verification: evidenceRisk?.product_verification,
    ...(isPresent(riskGateSummary) ? { risk_gate: riskGateSummary } : {})
  };
  renderCollapsibleReportSection(
    elements.evidenceRisks,
    "证据与发布前审核",
    productRelevance?.status === "has_product"
      ? "商品事实、来源证据与人工审核共同决定是否可发布。"
      : "来源证据与人工审核共同决定是否可发布。",
    evidenceDetail,
    evidenceSummary
  );
  renderReportSection(elements.riskReview, "风险审阅", "健康与产品相关表达需结合实际资质和平台规则复核。", isPresent(evidenceRisk) ? {} : risks);

  currentScript = isPresent(recommended) ? findScript(recommended, "recommended_script") : findScript(content);
  elements.copyScriptButton.hidden = !currentScript;
  elements.copyScriptButton.textContent = publishState.publishable ? "复制完整脚本" : "复制研究稿";

  const sourceUrl = sourceUrlFrom({ source }, elements.urlInput.value.trim());
  if (sourceUrl) {
    elements.sourceLink.href = sourceUrl;
    elements.sourceLink.hidden = false;
  } else {
    elements.sourceLink.hidden = true;
    elements.sourceLink.removeAttribute("href");
  }

  renderQuickView(payload, report, source, partial);

  // 通关路径：先重置再揭示
  document.querySelectorAll(".stage").forEach((s) => s.classList.remove("is-revealed"));
  const states = computeGateStates(payload, report, publishState);
  updatePathway(states);
  revealStages();

  const clearedCount = states.filter((s) => s === "cleared").length;
  if (payload.status === "completed") {
    showToast(clearedCount === states.length ? "内容已通关，四关全部点亮" : `已解析内容 · 通关 ${clearedCount} / ${states.length}`, "ok");
  }
}

function focusMissingField(missing) {
  const terms = (Array.isArray(missing) ? missing : [])
    .map((item) => typeof item === "object" && item !== null ? `${item.field || ""} ${item.label || ""} ${item.message || ""}` : textValue(item))
    .join(" ")
    .toLowerCase();
  elements.supplementDetails.open = true;
  if (/transcript|subtitle|caption|字幕|口播|文案/.test(terms)) {
    elements.transcriptInput.setAttribute("aria-invalid", "true");
    elements.transcriptInput.focus();
  } else if (/product|context|产品|业务|背景/.test(terms)) {
    elements.productContextInput.setAttribute("aria-invalid", "true");
    elements.productContextInput.focus();
  }
}

function resetMissingFieldState() {
  elements.transcriptInput.removeAttribute("aria-invalid");
  elements.productContextInput.removeAttribute("aria-invalid");
}

async function parseResponse(response) {
  const contentType = response.headers.get("content-type") || "";
  if (contentType.includes("application/json")) return response.json();
  const text = await response.text();
  return { message: text || `请求失败（${response.status}）` };
}

function responseMessage(payload, fallback) {
  if (typeof payload?.message === "string" && payload.message.trim()) return payload.message;
  if (typeof payload?.detail === "string" && payload.detail.trim()) return payload.detail;
  if (typeof payload?.detail?.message === "string" && payload.detail.message.trim()) return payload.detail.message;
  return fallback;
}

async function requestJson(url, options, fallback) {
  const response = await fetch(url, options);
  const payload = await parseResponse(response);
  if (!response.ok) {
    const error = new Error(responseMessage(payload, fallback || `请求失败（${response.status}）`));
    error.status = response.status;
    error.payload = payload;
    throw error;
  }
  return payload;
}

function delay(milliseconds) {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
}

async function waitForAcquisition(initialStatus) {
  let status = initialStatus;
  let pollDelay = ACQUISITION_POLL_INTERVAL_MS;
  const deadline = Date.now() + ACQUISITION_TIMEOUT_MS;
  while (["queued", "processing"].includes(String(status.status || "").toLowerCase())) {
    setAnalysisProgress(status.status === "queued" ? "排队中" : "采集中");
    showFormMessage(status.message || "电脑正在获取公开视频和字幕。", "warning");
    if (Date.now() >= deadline) {
      const error = new Error(`采集任务 ${status.job_id} 仍在后台处理，请稍后重试。`);
      error.status = 408;
      throw error;
    }
    await delay(pollDelay);
    pollDelay = Math.min(3000, pollDelay + 250);
    status = await requestJson(
      `/api/acquisition/jobs/${encodeURIComponent(status.job_id)}`,
      { headers: { "Accept": "application/json" } },
      "读取采集任务状态失败。"
    );
  }
  return status;
}

async function acquireAndAnalyze(body) {
  setAnalysisProgress("提交中");
  const submitted = await requestJson(
    "/api/acquisition/jobs",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url: body.url })
    },
    "公开链接采集任务提交失败。"
  );
  const status = await waitForAcquisition(submitted);
  const lifecycle = String(status.status || "").toLowerCase();
  if (lifecycle === "needs_input") return { acquisitionStatus: status };
  if (lifecycle !== "completed") {
    throw new Error(status.message || "公开链接采集未完成。原始错误已保存在 Worker 日志中。");
  }

  setAnalysisProgress("解读中");
  showFormMessage(status.cache_hit ? "已找到相同链接的完成结果，正在解读。" : "视频和字幕已取得，正在解读。", "warning");
  const analysis = await requestJson(
    `/api/acquisition/jobs/${encodeURIComponent(status.job_id)}/analyze`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        analysis_mode: body.analysis_mode,
        product_context: body.product_context || null,
        product_relevance_override: body.product_relevance_override || null
      })
    },
    "采集结果进入内容分析失败。"
  );
  return { analysis };
}

async function transcribeSelectedMedia() {
  const file = selectedMediaFile();
  if (!file) {
    showTranscriptionStatus("error", "请先选择一个音频或视频文件。");
    elements.mediaFileInput.focus();
    return;
  }
  if (file.size === 0 || file.size > MAX_MEDIA_BYTES) {
    updateMediaSelection();
    elements.mediaFileInput.focus();
    return;
  }
  const form = new FormData();
  form.append("file", file, file.name);
  form.append("provider", elements.asrStrategySelect.value || "auto");
  setTranscribing(true);
  showTranscriptionStatus("info", "正在转写，处理时间取决于文件长度和当前服务能力。请勿关闭页面。");
  try {
    const response = await fetch("/api/transcribe", { method: "POST", body: form });
    const payload = await parseResponse(response);
    if (!response.ok) {
      const error = new Error(responseMessage(payload, `转写请求失败（${response.status}）`));
      error.status = response.status;
      throw error;
    }
    const transcript = textValue(payload.transcript).trim();
    if (!transcript) throw new Error("转写服务没有返回可用文字。");
    const current = elements.transcriptInput.value.trim();
    if (current && current !== transcript) {
      const replace = window.confirm("字幕框已有内容，是否用本次转写结果替换？");
      if (!replace) {
        showTranscriptionStatus("warning", "转写已完成，但没有覆盖字幕框中的现有内容。");
        return;
      }
    }
    elements.transcriptInput.value = transcript;
    elements.transcriptInput.dispatchEvent(new Event("input", { bubbles: true }));
    elements.supplementDetails.open = true;
    const provider = [payload.provider, payload.model].filter(Boolean).join(" · ");
    showTranscriptionStatus("success", `${provider ? `${provider}：` : ""}转写完成，已写入字幕框。核对文字后点击“开始分析”。`);
  } catch (error) {
    const message = error instanceof Error ? error.message : "转写请求失败，请稍后重试。";
    const tone = error?.status === 503 ? "warning" : "error";
    showTranscriptionStatus(tone, message);
  } finally {
    setTranscribing(false);
  }
}

async function submitAnalysis(event) {
  event.preventDefault();
  showFormMessage();
  resetMissingFieldState();
  if (!validateUrl()) return;

  setLoading(true);
  showLoading();

  const body = {
    url: elements.urlInput.value.trim(),
    analysis_mode: requestedAnalysisMode,
    transcript: elements.transcriptInput.value.trim(),
    product_context: elements.productContextInput.value.trim(),
    product_relevance_override: productRelevanceOverride,
    asr: { mode: elements.asrStrategySelect.value || "auto" }
  };

  try {
    let payload;
    if (body.transcript) {
      setAnalysisProgress(requestedAnalysisMode === "full" ? "生成中" : "解读中");
      payload = await requestJson(
        "/api/analyze",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body)
        },
        "字幕降级分析失败。"
      );
    } else {
      const result = await acquireAndAnalyze(body);
      if (result.acquisitionStatus) {
        const status = result.acquisitionStatus;
        showStateMessage(
          "warning",
          "公开链接暂时无法读取",
          status.message || "请检查电脑网络后重试。",
          status.missing
        );
        showFormMessage(status.message || "请检查电脑网络后重试。", "warning");
        return;
      }
      payload = result.analysis;
    }
    showFormMessage();

    const status = String(payload.status || (payload.report ? "completed" : "partial")).toLowerCase();
    if (["completed", "complete", "success"].includes(status)) {
      showCompleted(payload);
    } else if (status === "partial") {
      if (payload.report || payload.distillation || payload.content_package || payload.recommended_script) showCompleted(payload, true);
      else {
        showStateMessage("warning", "仅获得部分信息", payload.message || "当前资料不足以生成可靠报告。", payload.missing);
        focusMissingField(payload.missing);
      }
    } else if (status === "needs_input" || status === "needs-input") {
      if (payload.report || payload.recommended_script || payload.recommended_draft) showCompleted(payload, true);
      else {
        showStateMessage("warning", "需要补充资料", payload.message || "补充以下资料后可继续分析。", payload.missing);
      }
      focusMissingField(payload.missing);
    } else if (status === "unsupported") {
      showStateMessage("info", "暂不支持该链接", payload.message || "当前版本暂时无法处理这个来源。", payload.missing);
    } else {
      showStateMessage("error", "分析未完成", payload.message || "返回了无法识别的状态，请稍后重试。", payload.missing);
    }
  } catch (error) {
    const message = error instanceof Error ? error.message : "网络请求失败，请稍后重试。";
    showStateMessage("error", "分析失败", message);
    showFormMessage(message, "error");
  } finally {
    requestedAnalysisMode = paidContentEnabled ? "full" : "quick";
    setLoading(false);
  }
}

async function loadDemoSample(fill = false) {
  if (demoSample) {
    if (fill) fillDemoSample();
    return;
  }
  try {
    const response = await fetch("/api/demo", { headers: { "Accept": "application/json" } });
    if (!response.ok) return;
    const data = await response.json();
    const url = data?.url || data?.sample_input?.url || data?.result?.source?.url || data?.result?.report?.source?.url;
    if (!url) return;
    demoSample = { url, label: data?.label || data?.result?.report?.title || "演示样本" };
    elements.demoButton.textContent = "填入演示样本";
    if (fill) fillDemoSample();
  } catch {
    if (fill) showFormMessage("演示样本暂时不可用，请手动粘贴链接。", "error");
  }
}

function fillDemoSample() {
  if (!demoSample?.url) return;
  productRelevanceOverride = null;
  currentProductRelevance = null;
  currentProductRequirements = null;
  setProductContextVisibility(null);
  elements.urlInput.value = demoSample.url;
  elements.urlInput.removeAttribute("aria-invalid");
  elements.urlError.textContent = "";
  showFormMessage(demoSample.label ? `已填入：${demoSample.label}` : "已填入演示样本。");
  elements.urlInput.focus();
}

async function loadPlatforms() {
  try {
    const response = await fetch("/api/platforms", { headers: { "Accept": "application/json" } });
    if (!response.ok) return;
    const payload = await response.json();
    const platforms = Array.isArray(payload) ? payload : payload.platforms || payload.supported || [];
    const names = platforms
      .filter((item) => typeof item === "string" || (item?.enabled !== false && (!item?.status || item.status === "active")))
      .map((item) => typeof item === "string" ? item : item.label || item.name || item.id)
      .filter(Boolean);
    if (names.length) elements.platformStatusText.textContent = `当前支持：${names.join("、")}`;
  } catch { /* 静态回退已足够准确 */ }
}

async function loadRuntimeMode() {
  try {
    const response = await fetch("/api/health", { headers: { "Accept": "application/json" } });
    if (!response.ok) return;
    const payload = await response.json();
    paidContentEnabled = payload?.paid_content_enabled === true;
    requestedAnalysisMode = paidContentEnabled ? "full" : "quick";
    updateBusyControls();
  } catch {
    paidContentEnabled = false;
    requestedAnalysisMode = "quick";
  }
}

async function copyScript() {
  if (!currentScript) return;
  try {
    await navigator.clipboard.writeText(currentScript);
    showCopyFeedback(elements.copyScriptButton.textContent.includes("研究稿") ? "研究稿已复制" : "完整脚本已复制");
  } catch {
    const area = document.createElement("textarea");
    area.value = currentScript;
    area.setAttribute("readonly", "");
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    const copied = document.execCommand("copy");
    area.remove();
    const copiedMessage = elements.copyScriptButton.textContent.includes("研究稿") ? "研究稿已复制" : "完整脚本已复制";
    showCopyFeedback(copied ? copiedMessage : "复制失败，请手动选择脚本文本");
  }
}

function openScriptFlow() {
  if (currentScript) {
    const target = document.getElementById("stageScript");
    if (target) target.scrollIntoView({ behavior: "smooth", block: "start" });
    return;
  }
  requestedAnalysisMode = "full";
  elements.supplementDetails.open = true;
  elements.analyzeLabel.textContent = "生成完整脚本";
  const status = currentProductRelevance?.status;
  if (status === "no_product") {
    showFormMessage("这条内容无需商品资料；核对字幕后即可生成完整脚本。");
    if (!elements.transcriptInput.value.trim()) elements.transcriptInput.focus();
    else elements.analyzeButton.focus();
  } else if (status === "needs_confirmation") {
    showFormMessage("先确认上方商品属性；普通解读不会被商品资料阻塞。");
    elements.productRelevance.scrollIntoView({ behavior: "smooth", block: "center" });
    return;
  } else {
    showFormMessage("商品资料只在改写或正式发布时使用；补充后点击“生成完整脚本”。");
    elements.productContextInput.focus();
  }
  elements.supplementDetails.scrollIntoView({ behavior: "smooth", block: "start" });
}

/* ---------- 事件绑定 ---------- */
elements.form.addEventListener("submit", submitAnalysis);
elements.demoButton.addEventListener("click", () => loadDemoSample(true));
elements.mediaFileInput.addEventListener("change", updateMediaSelection);
elements.transcribeButton.addEventListener("click", transcribeSelectedMedia);
elements.copyScriptButton.addEventListener("click", copyScript);
elements.scriptNextButton.addEventListener("click", openScriptFlow);
elements.pathway.querySelectorAll(".gate").forEach((gate) => {
  gate.addEventListener("click", () => navigateToGate(gate));
});
elements.urlInput.addEventListener("input", () => {
  productRelevanceOverride = null;
  currentProductRelevance = null;
  currentProductRequirements = null;
  setProductContextVisibility(null);
  elements.urlInput.removeAttribute("aria-invalid");
  elements.urlError.textContent = "";
});
elements.transcriptInput.addEventListener("input", resetMissingFieldState);
elements.productContextInput.addEventListener("input", resetMissingFieldState);

resetPathway();
loadDemoSample();
loadPlatforms();
loadRuntimeMode();
updateBusyControls();
