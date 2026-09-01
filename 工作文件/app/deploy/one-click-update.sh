#!/usr/bin/env bash
set -Eeuo pipefail

expected="6caa8a8"
repo=""
for root in /root /opt /srv /var/www; do
  [ -d "$root" ] || continue
  candidate="$(find "$root" -maxdepth 5 -type f -name Dockerfile.control -print -quit 2>/dev/null || true)"
  if [ -n "$candidate" ]; then
    repo="$(dirname "$candidate")"
    break
  fi
done

if [ -z "$repo" ]; then
  echo "UPDATE_BLOCKED: 找不到 Project024 项目目录"
  exit 10
fi

cd "$repo"
git fetch origin
git pull --ff-only origin main
actual="$(git rev-parse --short HEAD)"
if [ "$actual" != "$expected" ]; then
  echo "UPDATE_BLOCKED: 当前版本=$actual，期望版本=$expected"
  exit 11
fi

bash 工作文件/app/deploy/update-control-plane.sh "$expected"
echo "UPDATE_OK repo=$repo commit=$actual"
