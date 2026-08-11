# 自媒体通关搭档 Web 开发版 v0.3.0

这是项目024当前持续完善的 Web 应用开发版。默认路径是“快速看懂”：用户提交抖音或 TikTok 公开内容链接，先得到一句话结论、内容结构和可借鉴方法；需要时再展开完整脚本、拍摄表、发布内容包以及证据与风险边界。

本目录不是正式甲方交付包。现有 `产出` 目录中的旧甲方包未随 v0.3 覆盖；演示请使用本 README 下方的独立入口。

## 当前能力

- 抖音与 TikTok 均可识别为 active 平台。
- 已登记样本从本地 fixture 返回经审阅的报告；返回信息会明确说明不是本次实时抓取。
- 未登记抖音单链接会先尝试可替换的社区公共 Provider；Provider 失效时使用无个人登录态的隔离浏览器回退。合并媒体与分离音视频流均会在进入本地 `faster-whisper` 前完成流校验。
- 未登记 TikTok 单链接会在隔离 Worker 中检查电脑代理，优先获取平台字幕；没有字幕时下载音轨并使用本地 `faster-whisper` 生成带时间码字幕。
- Web 主路径只需提交公开链接：页面会创建采集任务、轮询终态，再调用任务分析入口；字幕框只保留为明确的人工降级入口。
- 用户补充字幕后返回 `partial` 研究稿；商品事实或证据不完整时强制 `publishable=false`。
- 结果按“快速看懂 → 完整分析与交付内容”两层展示；原有三层完整报告仍保留在展开区。
- `analysis_mode=quick` 使用短输出模型请求优先返回快速解读；`analysis_mode=full` 保留原完整研究稿路径。
- 请求模型已预留结构化商品字段、地区/国家/语言字段和 ASR 策略。
- `POST /api/transcribe` 已提供受限媒体上传与转写接口。
- Web 表单可选择媒体并调用转写接口；成功结果写入字幕框，由用户核对后再显式开始分析。
- 已实现 DeepSeek 结构化内容生成适配器；仅在服务端配置凭据后才会尝试调用。
- `POST /api/acquisition/jobs` 会把来源检查放入独立 Python 子进程，任务状态、原始证据和精简清单写入磁盘；主请求只返回任务摘要。
- `POST /api/acquisition/jobs/{job_id}/analyze` 只接受完成态且可分析的清单，自动使用 Worker 字幕和数字证据，并在响应中保留任务 ID、清单地址和字幕 SHA-256。
- 已完成的真实或登记样本任务按规范化链接和请求参数复用缓存；`needs_input` 和 `failed` 不进入完成缓存，原始证据不会默认进入分析上下文。
- YouTube、Facebook 和 X 仍为 `planned`。

## 重要边界

- TikTok 当前已验真实时公开单链接的元数据、媒体和音轨获取；平台页面、签名和第三方公开接口变化仍可能导致采集失败，主页多作品与短链兼容性未完成系统验真。
- 抖音当前已验真公开短链的元数据、媒体、本地 GPU 字幕和自动分析；默认社区 Provider 为 `douyin.wtf`，不是抖音官方稳定接口。Provider 变化或限流时会自动尝试隔离浏览器，两个通道都失败后才返回 `needs_input`。
- 自动采集不再设置视频时长上限。为防止异常链接耗尽本机磁盘，单个媒体文件仍保留 `512 MiB` 下载安全上限；超过时会在页面明确显示该数值。
- `tiktok_cases.json` 是否包含可用登记样本，以文件当前内容为准；空样本库不会返回完整 TikTok 报告。
- Web 表单默认自动触发“采集 → 字幕 → 分析”；25 MB 手动媒体上传和字幕框仅用于自动采集失败后的受控降级。
- 外部 ASR 当前采用通用的 OpenAI-compatible `/audio/transcriptions` 协议。阿里云百炼、AssemblyAI 等厂商专用协议尚未接入和实测。
- 本机演示环境已在项目 G 盘安装 `faster-whisper`、`large-v3-turbo` 模型和 Windows CUDA 12 运行库；真实 Worker 已在 RTX 4060 Ti 上验真。其他机器仍需按 `requirements.txt` 安装依赖并单独验证 GPU。
- DeepSeek 只负责根据已有文字和商品资料生成结构化研究稿，不负责语音转写、平台采集或事实核验。
- 隔离 Worker 已接入抖音与 TikTok 实时单链接媒体和字幕；主页作品列表、画面 OCR、镜头结构和评论采集尚未接入。
- DeepSeek `deepseek-chat` 已在本机服务端配置下完成真实研究稿验收；仓库不包含凭据，其他电脑必须自行配置并重新验真。百炼及其他付费服务仍未接入或验真。
- 应用不读取 Cookie、密码、浏览器本地存储或个人登录态，不自动发布内容。

## 结果与发布前审核

主要报告字段包括：

- `delivery`：交付状态与是否可发布。
- `recommended_script`：唯一推荐稿，不输出多版让用户自行拼接。
- `shooting_table`：时间、画面、口播、字幕、商品证明和声音。
- `publishing_package`：标题、正文、标签、CTA 和评论回复。
- `localization`：记录未来本地化请求；v0.2 不执行内容改写。
- `product_requirements`：商品资料、缺失字段和核验状态。
- `evidence_and_risk`：来源证据、事实边界与发布风险。
- `asr`：转写策略、可用 provider 和本次是否实际调用。

以下情况只能产出研究稿，禁止标记为可发布：

- 商品名称、品类、核心卖点、规格、批准表述或证明材料缺失。
- 用户本次补充了尚未复核的商品资料。
- 只有字幕，没有实时指标、评论原文或完整来源证据。
- DeepSeek 成功生成了内容，但商品事实和人工审核尚未完成。
- 发布前审核明确返回 `publishable=false`。

## 本地运行

要求 Python 3.11 或更高版本。PowerShell 中运行：

```powershell
Set-Location 'G:\Workspace\Projects\项目024_自媒体通关搭档\工作文件\app'
.\run.ps1
```

若系统的 `python.exe` 是 WindowsApps 占位入口，显式指定真实解释器：

```powershell
.\run.ps1 -PythonExe 'G:\path\to\python.exe'
```

默认地址：

- 产品页：<http://127.0.0.1:8787/>
- API 文档：<http://127.0.0.1:8787/docs>
- 健康检查：<http://127.0.0.1:8787/api/health>

甲方电脑演示入口：

- 可直接双击项目根目录的 `项目024_v0.3_甲方演示.cmd`。
- 或在 PowerShell 中运行：

```powershell
Set-Location 'G:\Workspace\Projects\项目024_自媒体通关搭档\工作文件\app'
.\run_v03_demo.ps1
```

服务默认仅监听 `127.0.0.1:8792`，电脑打开 <http://127.0.0.1:8792/>。重复运行入口会复用已通过健康检查的同一服务，不会启动第二个实例。按用户最新决定，电脑演示默认启用付费内容 Provider；页面会直接请求完整内容生成。需要临时关闭付费调用时，显式运行 `.\run_v03_demo.ps1 -DisablePaidContent`。手机端局域网验收暂缓。数字依据默认折叠，发布前审核未通过时不会显示为已通关。

## API

### `GET /api/health`

返回服务状态、当前版本和 `paid_content_enabled` 模式标记。

### `GET /api/platforms`

返回平台能力。`douyin`、`tiktok` 为 `active`；`youtube`、`facebook`、`x` 为 `planned`。

### `GET /api/demo`

返回默认抖音登记样本和对应 v0.3 快速结果及兼容完整报告。

### `POST /api/acquisition/jobs`

创建隔离采集任务。API 主进程只负责入队，实际来源检查由独立 Python 子进程执行，Worker 的标准输出和错误日志不会进入默认分析响应。

```json
{
  "url": "https://www.douyin.com/video/7666774161494183218",
  "item_limit": 1,
  "force_refresh": false
}
```

返回 `job_id` 后依次使用：

- `GET /api/acquisition/jobs/{job_id}`：只读取任务状态、进度、缺失项和精简清单地址。
- `GET /api/acquisition/jobs/{job_id}/manifest`：读取默认交给分析 Agent 的精简证据包。
- `GET /api/acquisition/jobs/{job_id}/artifacts/{artifact_name}`：只有核对具体证据时才定向读取白名单原始文件。
- `POST /api/acquisition/jobs/{job_id}/analyze`：完成态清单自动进入内容分析，请求体不接受 `transcript`。

任务状态为 `queued`、`processing`、`completed`、`needs_input` 或 `failed`。只有 `completed` 会写入缓存；`needs_input` 不会伪装成已采集内容。

Codex 或本地自动化不需要逐个调用接口，可使用单命令入口：

```powershell
Set-Location 'G:\Workspace\Projects\项目024_自媒体通关搭档\工作文件\app'
.\.venv\Scripts\python.exe -m app.acquisition_cli `
  --url 'https://www.douyin.com/video/7666774161494183218' `
  --item-limit 1
```

该命令等待 Worker 到达终态后只打印一条精简 JSON。若等待超时，Worker 继续在后台运行，后续可按 `job_id` 查询；采集过程日志不会打印到当前对话。

完成态任务进入分析的最小请求：

```json
{
  "analysis_mode": "quick",
  "product_context": null
}
```

`queued`、`processing`、`needs_input`、`failed`、空字幕或缺少字幕 SHA-256 的任务会在内容生成前被拒绝。登记样本没有运行时字幕时，入口复用其预先审阅报告，并明确保留 `registered_fixture` 来源。

### `POST /api/analyze`

最小请求：

```json
{
  "url": "https://www.douyin.com/video/7666774161494183218",
  "analysis_mode": "quick",
  "transcript": "可选：用户补充字幕或口播稿",
  "product_context": "可选：已确认的商品资料",
  "market": {
    "region": null,
    "country": null,
    "language": null
  },
  "asr": {
    "mode": "auto"
  }
}
```

`analysis_mode=quick` 只请求一句话结论、内容结构、有效原因、可借鉴方法和原创方向；`analysis_mode=full` 继续生成完整脚本、拍摄表和发布内容包。Web 页面默认使用 `quick`。

`market` 在 v0.2 只保存请求值，响应中保持 `enabled=false`、`applied=false`。Web 页面上的地区/国家选择器也保持禁用。

业务状态：

- `completed`：命中登记样本，返回经审阅样本报告。
- `needs_input`：支持的平台链接缺少字幕或媒体输入。
- `partial`：已有字幕，只返回证据受限的研究稿。
- `unsupported`：当前不支持的平台。

### `POST /api/transcribe`

仅接受 `multipart/form-data`。字段：

- `file`：必填媒体文件，最大 25 MB。
- `provider`：`auto`、`external`、`local` 或 `disabled`，默认 `auto`。
- `language`：可选语言代码；留空时交给 provider 自动检测。

支持的扩展名：`.aac`、`.flac`、`.m4a`、`.mp3`、`.mp4`、`.mpeg`、`.mpga`、`.mov`、`.ogg`、`.wav`、`.webm`。接口会校验扩展名、媒体类型、文件大小和空文件，并返回结构化 `completed`、`unavailable` 或 `failed` 状态。

示例：

```powershell
curl.exe -X POST 'http://127.0.0.1:8787/api/transcribe?provider=auto' `
  -F 'file=@G:\path\to\sample.mp4'
```

上传内容只在本次请求中处理；响应 `source.retained=false`。外部 provider 被选中时媒体会发送到配置的第三方服务，部署前必须补充隐私说明与人工确认。

## 服务端配置

密钥只能放在服务端环境变量或正式密钥管理中，不得写入前端、源码、Markdown、Git 或日志。

### 隔离采集任务

- `PROJECT024_ACQUISITION_ROOT`：任务、缓存和证据根目录；本地默认使用 `工作文件/app/var/acquisition`。
- `PROJECT024_DOUYIN_PUBLIC_API_BASE`：可替换的抖音社区公共 Provider 基础地址；未设置时使用 `https://douyin.wtf`。
- 每个任务包含 `request.json`、`status.json`、`evidence_manifest.json`、`raw/` 和独立 Worker 日志。
- `evidence_manifest.json` 限制默认字段数量和文字长度；完整来源只保存在 `raw/`，通过白名单文件名按需读取。抖音元数据只保留公开字段，作品地址规范化为无查询参数的标准 URL，不保存 Provider 完整响应、媒体签名地址、Cookie 或请求头。
- 当前是单机子进程 Worker MVP。正式多实例部署仍需外部队列、并发限制、超时终止、任务保留期和清理策略。

### 外部 ASR

- `PROJECT024_ASR_API_KEY`：外部 ASR 密钥；未设置时会回退检查 `OPENAI_API_KEY`。
- `PROJECT024_ASR_BASE_URL`：OpenAI-compatible API 基础地址，默认 `https://api.openai.com/v1`。
- `PROJECT024_ASR_MODEL`：模型名，默认 `whisper-1`。
- `PROJECT024_ASR_RESPONSE_FORMAT`：响应格式，默认 `verbose_json`。
- `PROJECT024_ASR_TIMEOUT_SECONDS`：请求超时，默认 120 秒。

`auto` 会按“外部 API → 本地”的可用性顺序选择一次 provider。当前实现不是执行失败后自动换 provider 的多次重试链。

### 本地 ASR

- `requirements.txt` 在 Windows 安装 `faster-whisper`、CUDA 12 `cuBLAS/cuDNN` 运行库；依赖只进入项目虚拟环境。
- `PROJECT024_LOCAL_ASR_MODEL`：默认 `large-v3-turbo`。
- `PROJECT024_LOCAL_ASR_DEVICE`：默认 `cuda`；可显式改为其他设备。
- `PROJECT024_LOCAL_ASR_COMPUTE_TYPE`：CUDA 默认 `int8_float16`，CPU 回退使用 `int8`。
- `PROJECT024_LOCAL_ASR_ALLOW_CPU_FALLBACK`：默认允许 CUDA 失败后记录原因并回退 CPU；设为 `0` 可用于严格 GPU 验收。
- `PROJECT024_LOCAL_ASR_DOWNLOAD_ROOT`：模型下载与缓存目录；未设置时使用项目内 `.cache/faster-whisper`。

自动采集 Worker 固定使用本地 ASR，不调用付费 ASR。服务启动时只把项目虚拟环境内的 NVIDIA DLL 目录加入当前进程，不永久修改系统 PATH。

### DeepSeek 内容生成

- `DEEPSEEK_API_KEY` 或 `PROJECT024_CONTENT_API_KEY`：服务端密钥。
- `DEEPSEEK_BASE_URL`：默认 `https://api.deepseek.com`。
- `DEEPSEEK_MODEL`：可选兼容环境变量；默认模型为 `deepseek-chat`。
- `PROJECT024_CONTENT_MODEL`：项目级模型覆盖，优先级高于 `DEEPSEEK_MODEL`。
- `PROJECT024_CONTENT_TIMEOUT_SECONDS`：请求超时，默认 90 秒。
- `PROJECT024_CONTENT_QUICK_MODEL`：快速结果可单独指定模型；未设置时沿用完整内容模型。
- `PROJECT024_CONTENT_QUICK_TIMEOUT_SECONDS`：快速结果超时，默认 30 秒。

未配置密钥时，应用保留本地研究稿结构并显示 provider 未配置；调用失败时降级回证据受限研究稿，仍保持 `publishable=false`。

## 测试

安装项目依赖后运行：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

JavaScript 语法检查：

```powershell
node --check .\static\app.js
```

服务器启动后可运行自动采集浏览器验真：

```powershell
node .\tests\browser_p1_auto_acquisition.cjs http://127.0.0.1:8787
```

`browser_p1_auto_acquisition.cjs` 用于显式 `-DisablePaidContent` 后的零付费回归；第五个参数可传入抖音公开链接。默认付费模式使用 `browser_paid_full_acquisition.cjs`，真实检查页面请求 `analysis_mode=full`、DeepSeek 调用、完整稿、拍摄表、发布内容包与发布前审核。两类脚本都会检查控制台、请求、响应和页面布局。

## Docker

```powershell
docker build -t self-media-growth-partner .
docker run --rm -p 8787:8787 self-media-growth-partner
```

容器监听 `0.0.0.0:8787`。正式部署前还需要反向代理、HTTPS、分布式任务队列、Worker 并发与清理策略、服务端密钥管理、日志脱敏、访问控制、上传隐私策略和 provider 成本控制。
