# 国内云控制面部署说明

这份入口只运行 Project024 的云端控制面：Supabase 任务状态、用户 JWT 验证和手机查询。视频采集、ASR、OCR 和 Ollama 仍由用户电脑上的本机 Worker 执行，不需要云端 GPU。

## 腾讯云轻量服务器

服务器系统建议使用 Ubuntu 22.04 LTS。首次内测可用 2 核 2GB；如果同时运行监控或构建较多，选择 2 核 4GB。不要安装 OpenClaw 应用镜像。

在服务器上执行：

```bash
git clone https://github.com/gulaiyugu-collab/24zimeiti.git
cd 24zimeiti
docker build -f Dockerfile.control -t project024-control .
```

手机入口打包在同一个控制面里，部署后地址为 `https://你的域名/cloud`。

首次部署前，请在 Supabase Dashboard 的 SQL Editor 中完整运行仓库里的
`工作文件/app/deploy/supabase/schema.sql`。旧版本已经运行过时，也要再次运行，脚本包含 `create or replace function`。

创建仅服务器保存的环境文件 `/root/project024-control.env`：

```text
SUPABASE_URL=https://你的项目编号.supabase.co
SUPABASE_SECRET_KEY=只填写在服务器，不要提交 Git
SUPABASE_PUBLISHABLE_KEY=Supabase 项目设置中的 publishable/anon 公钥
# 只有旧版 HS256 项目需要；新项目通常使用 JWKS，可留空
SUPABASE_JWT_SECRET=旧版项目的 JWT Secret（如无则留空）
PROJECT024_CLOUD_WORKER_TOKEN=你自己生成的长随机 Worker 凭据
```

运行控制面：

```bash
docker run -d --name project024-control --restart unless-stopped \
  --env-file /root/project024-control.env \
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

当前 Worker 合约已经支持领取、租约心跳、完成和失败回传；正式手机业务还需要将前端提交路径接到 `/api/cloud/tasks`，并在 Supabase SQL Editor 运行新增的 RPC 函数。

现在手机端已接入：登录/注册、提交公开链接、轮询任务状态、显示本机 Worker 返回的证据清单。完整结果出现前，必须先在你的电脑上启动 Worker；云服务器不会使用你的 RTX 显卡。
