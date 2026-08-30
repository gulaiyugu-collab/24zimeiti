#!/usr/bin/env bash
set -Eeuo pipefail

container="project024-control"
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_dir="$(cd -- "$script_dir/../../.." && pwd)"
cd "$repo_dir"

commit="$(git rev-parse --short HEAD)"
if [ "${1:-$commit}" != "$commit" ]; then
  echo "ERROR: 当前代码不是要求的提交，当前=$commit 要求=${1:-未知}"
  exit 20
fi

if ! sudo docker inspect "$container" >/dev/null 2>&1; then
  echo "ERROR: 未找到运行中的 $container，未修改服务"
  exit 21
fi

raw_env="$(sudo docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$container")"
env_value() {
  printf '%s\n' "$raw_env" | sed -n "s/^$1=//p" | head -n 1
}

auth_secret="$(env_value PROJECT024_AUTH_SECRET)"
worker_token="$(env_value PROJECT024_CLOUD_WORKER_TOKEN)"
task_db="$(env_value PROJECT024_CLOUD_TASK_DB)"
domestic_mode="$(env_value PROJECT024_DOMESTIC_MODE)"
mount_src="$(sudo docker inspect -f '{{range .Mounts}}{{if eq .Destination "/var/lib/project024"}}{{.Source}}{{end}}{{end}}' "$container")"

if [ -z "$auth_secret" ] || [ -z "$worker_token" ] || [ -z "$task_db" ] || [ -z "$mount_src" ]; then
  echo "ERROR: 现有容器缺少必要配置或数据库挂载，未中断服务"
  exit 22
fi

sudo docker build -f Dockerfile.control -t "project024-control:$commit" .

candidate_env=(
  --env "PROJECT024_AUTH_SECRET=$auth_secret"
  --env "PROJECT024_CLOUD_WORKER_TOKEN=$worker_token"
  --env "PROJECT024_CLOUD_TASK_DB=/tmp/cloud-control-candidate.sqlite3"
)
if [ -n "$domestic_mode" ]; then
  candidate_env+=(--env "PROJECT024_DOMESTIC_MODE=$domestic_mode")
fi

sudo docker rm -f project024-control-candidate >/dev/null 2>&1 || true
sudo docker run -d \
  --name project024-control-candidate \
  "${candidate_env[@]}" \
  -p 127.0.0.1:8788:8787 \
  "project024-control:$commit" >/dev/null

cleanup_candidate() {
  sudo docker rm -f project024-control-candidate >/dev/null 2>&1 || true
}
trap cleanup_candidate EXIT

candidate_ok=0
for _ in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:8788/api/health >/dev/null && \
     curl -fsS http://127.0.0.1:8788/static/cloud.js >/dev/null; then
    candidate_ok=1
    break
  fi
  sleep 1
done

if [ "$candidate_ok" != "1" ]; then
  sudo docker logs --tail 80 project024-control-candidate || true
  echo "ERROR: 候选镜像验收失败，正式服务未切换"
  exit 23
fi

rollback_name="project024-control-rollback-$(date +%Y%m%d%H%M%S)"
sudo docker stop "$container" >/dev/null
sudo docker rename "$container" "$rollback_name"

rollback() {
  sudo docker rm -f "$container" >/dev/null 2>&1 || true
  sudo docker rename "$rollback_name" "$container" >/dev/null 2>&1 || true
  sudo docker start "$container" >/dev/null 2>&1 || true
}

live_env=(
  --env "PROJECT024_AUTH_SECRET=$auth_secret"
  --env "PROJECT024_CLOUD_WORKER_TOKEN=$worker_token"
  --env "PROJECT024_CLOUD_TASK_DB=$task_db"
)
if [ -n "$domestic_mode" ]; then
  live_env+=(--env "PROJECT024_DOMESTIC_MODE=$domestic_mode")
fi

if ! sudo docker run -d \
  --name "$container" \
  --restart unless-stopped \
  "${live_env[@]}" \
  -v "$mount_src:/var/lib/project024" \
  -p 0.0.0.0:8787:8787 \
  "project024-control:$commit" >/dev/null; then
  rollback
  echo "ERROR: 正式容器启动失败，已回滚旧容器"
  exit 24
fi

live_ok=0
for _ in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:8787/api/health >/dev/null && \
     curl -fsS http://127.0.0.1:8787/static/cloud.js >/dev/null; then
    live_ok=1
    break
  fi
  sleep 1
done

if [ "$live_ok" != "1" ]; then
  sudo docker logs --tail 80 "$container" || true
  rollback
  echo "ERROR: 正式入口验收失败，已回滚旧容器"
  exit 25
fi

echo "DEPLOY_OK commit=$commit"
curl -fsS http://127.0.0.1:8787/api/health
echo
echo "STATIC_SCRIPT_OK"
echo "ROLLBACK_CONTAINER=$rollback_name"
