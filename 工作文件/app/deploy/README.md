# Project024 云端 MVP 部署准备

这份目录只包含无需付费即可准备的部署材料。当前主应用仍是本机 `8792` 活动版本，真实公网部署必须在完成 JWT、账号隔离和 HTTPS 接入后进行。

## 当前已准备

- `supabase/schema.sql`：Supabase Postgres 的任务、用量和邀请表，以及用户级 RLS。
- `app.services.cloud_worker_runner`：本机出站 Worker 客户端。
- `app.services.cloud_worker_http`：本地 HTTP 合约样机，不能直接暴露公网。
- `.env.example`：只含占位符的环境变量模板，不包含任何真实密钥。

## 你后面只需做的人工动作

1. 注册一个 Supabase 账号并创建 Free 项目。
2. 在 Supabase 的 SQL Editor 粘贴并运行 `supabase/schema.sql`。
3. 保存项目 URL 和公开 anon key；服务端密钥只放后端平台环境变量，不要发到聊天或提交 Git。
4. 选择一个可以运行 FastAPI 的后端平台，再提供部署入口和预算上限。

在第 4 步之前不要把当前本机 `8792` 直接暴露到公网，也不要把测试用 `X-User-Id` / `X-Worker-Id` 当成生产认证。

`.env.example` 只用于对照变量名称。不要把真实值写入 Git 仓库；正式部署时在托管平台的 Environment Variables 页面填写。
