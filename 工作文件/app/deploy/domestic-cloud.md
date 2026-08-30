# 国内云控制面部署说明

这份入口只运行 Project024 的国内云端控制面：腾讯云本地 SQLite 任务状态、用户 JWT 验证和手机查询。视频采集、ASR、OCR 和 Ollama 仍由用户电脑上的本机 Worker 执行，不需要云端 GPU。正式国内模式不依赖 Supabase，也不需要运行 Supabase SQL。

## 腾讯云轻量服务器

服务器系统建议使用 Ubuntu 22.04 LTS。首次内测可用 2 核 2GB；如果同时运行监控或构建较多，选择 2 核 4GB。不要安装 OpenClaw 应用镜像。

在服务器上执行：

```bash
git clone https://github.com/gulaiyugu-collab/24zimeiti.git
cd 24zimeiti
docker build -f Dockerfile.control -t project024-control .
```

手机入口打包在同一个控制面里，部署后地址为 `https://你的域名/cloud`。

国内模式不需要执行仓库里的 Supabase SQL。`supabase/schema.sql` 仅作为旧兼容方案留档，不参与本次部署。

创建仅服务器保存的环境文件 `/root/project024-control.env`：

```text
PROJECT024_AUTH_SECRET=服务器保存的 32 位以上随机登录签名密钥
PROJECT024_CLOUD_TASK_DB=/var/lib/project024/cloud-control.sqlite3
PROJECT024_CLOUD_WORKER_TOKEN=你自己生成的长随机 Worker 凭据
```

运行控制面：

```bash
docker run -d --name project024-control --restart unless-stopped \
  --env-file /root/project024-control.env \
  -v /var/lib/project024:/var/lib/project024 \
  -p 127.0.0.1:8787:8787 project024-control
```

公网入口应由 Nginx/Caddy 终止 HTTPS，再反代到 `127.0.0.1:8787`。不要把 `8787` 直接暴露到公网。防火墙只开放 `80`、`443` 和必要的 SSH 管理端口。

## 本机 Worker

本机安装依赖后，只设置以下本机环境变量，不把值写进仓库：

```powershell
$env:PROJECT024_CLOUD_CONTROL_BASE_URL = 'https://你的域名'
$env:PROJECT024_CLOUD_WORKER_ID = '你的电脑名称'
$env:PROJECT024_CLOUD_WORKER_TOKEN = '与服务器相同的长随机凭据'
$env:PROJECT024_ACQUISITION_ROOT = 'G:\Project024Data\acquisition'
& .\.venv\Scripts\python.exe -m app.services.cloud_worker_runner
```

当前 Worker 合约已经支持领取、租约心跳、完成和失败回传；手机端已接入国内登录、提交路径 `/api/cloud/tasks` 和状态查询。

现在手机端已接入：登录/注册、提交公开链接、轮询任务状态、显示本机 Worker 返回的证据清单和分析报告。完整结果出现前，必须先在你的电脑上启动 Worker；云服务器不会使用你的 RTX 显卡。
