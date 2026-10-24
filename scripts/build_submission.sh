#!/bin/sh
# 构建并导出交付镜像，然后做完整自验。一条命令，避免手工漏步导致上传过期归档。
#
# 用法：
#   sh scripts/build_submission.sh
#
# 它会：
#   1. 以 linux/amd64 构建 research-assistant:p1（需先 docker load 基础镜像）
#   2. docker save 到 ../research-assistant-p1-linux-amd64.tar 并 chmod 644
#      （docker save 默认 600，macOS 文件选择器会把 600 的文件置灰成不可选）
#   3. 同步短名硬链接 ../agent-upload.tar（同一 inode，排除长文件名变量）
#   4. 删掉旧标签后从归档重新 docker load（往返校验）
#   5. 起容器跑 healthcheck、一次真实 A 类 /solve、落盘 MD5 与响应 MD5 比对、
#      非 root 属主、凭据泄漏 grep
#   6. 打印新的 SHA-256 与字节数——**把它抄进 docs/开发进度与接力记录.md 的归档表**
#
# 任何一步失败都会以非零退出码结束，不会留下「构建成功但没验证」的假象。
set -eu

# docker CLI 可能不在 PATH 上；可用 DOCKER=/absolute/path/docker 覆盖。
DOCKER="${DOCKER:-docker}"
if ! command -v "$DOCKER" >/dev/null 2>&1; then
  DOCKER=/Applications/Docker.app/Contents/Resources/bin/docker
fi

cd "$(dirname "$0")/.."
REPO="$(pwd)"
PARENT="$(dirname "$REPO")"
TAR="$PARENT/research-assistant-p1-linux-amd64.tar"
LINK="$PARENT/agent-upload.tar"
IMAGE="research-assistant:p1"
PORT="${SUBMISSION_PORT:-18099}"

echo "== 1/6 构建 $IMAGE (linux/amd64) =="
"$DOCKER" build --platform linux/amd64 -t "$IMAGE" . > /tmp/build_submission.log 2>&1 \
  || { echo "构建失败，日志在 /tmp/build_submission.log"; tail -20 /tmp/build_submission.log; exit 1; }
echo "构建完成"

echo "== 2/6 导出归档并修权限 =="
"$DOCKER" save -o "$TAR" "$IMAGE"
chmod 644 "$TAR"
ln -f "$TAR" "$LINK"
ls -l "$TAR" | awk '{print "  ", $1, $5, "bytes", $NF}'

echo "== 3/6 往返校验：删标签后从归档重新 load =="
"$DOCKER" rmi "$IMAGE" > /dev/null 2>&1 || true
"$DOCKER" load -i "$TAR" | tail -1
"$DOCKER" inspect "$IMAGE" --format '   arch={{.Architecture}} user={{.Config.User}} workdir={{.Config.WorkingDir}} ports={{.Config.ExposedPorts}} cmd={{.Config.Cmd}}'

echo "== 4/6 容器内验证 =="
if [ ! -f .env.local ]; then
  echo "   缺少 .env.local，跳过真实解题（healthcheck 仍会跑）"
  ENV_ARGS=""
else
  ENV_ARGS="--env-file .env.local"
fi
OUT="$(mktemp -d)"
chmod 777 "$OUT"
CONTAINER="submission-check-$$"
# shellcheck disable=SC2086
"$DOCKER" run -d --rm --platform linux/amd64 --name "$CONTAINER" -p "$PORT:8080" \
  $ENV_ARGS -e RESEARCH_WORKSPACE_TEAM=smoke -v "$OUT:/output" "$IMAGE" > /dev/null
trap '"$DOCKER" stop "$CONTAINER" > /dev/null 2>&1 || true; rm -rf "$OUT"' EXIT

i=0
until "$DOCKER" exec "$CONTAINER" sh -c 'curl -fsS http://localhost:8080/health > /dev/null' 2>/dev/null; do
  i=$((i+1))
  [ "$i" -ge 40 ] && { echo "   healthcheck 40 次轮询未通过"; exit 1; }
  sleep 0.5
done
echo "   容器内 healthcheck: PASS（$((i/2))s 内就绪）"

if [ -n "$ENV_ARGS" ]; then
  BODY='{"case_id":"A-90","function":"Chat","title":"t","input":"请解释光照强度与室内植物生长速度的可能关系，并问我一个最关键的研究范围限定问题。","materials":[],"constraints":[],"expected_output":"e","output_schema_ref":"Chat"}'
  RESP="$(curl -fsS -X POST "http://localhost:$PORT/solve" -H 'Content-Type: application/json' -d "$BODY")"
  # printf 而非 echo：echo 会追加换行，导致与容器内文件（无尾换行）MD5 不符
  printf '%s' "$RESP" > /tmp/submission_check.json
  CODE=$(python3 -c "import json,sys; d=json.load(open('/tmp/submission_check.json')); print(sorted(d)); assert d['case_id']=='A-90'")
  echo "   真实 A 类 /solve: HTTP 200，顶层字段 $CODE"
  FILE_MD5="$($DOCKER exec "$CONTAINER" md5sum /output/data/smoke/A-90.json | cut -d' ' -f1)"
  RESP_MD5="$(md5 -q /tmp/submission_check.json)"
  [ "$FILE_MD5" = "$RESP_MD5" ] || { echo "   落盘与响应 MD5 不一致: $FILE_MD5 vs $RESP_MD5"; exit 1; }
  echo "   落盘与响应逐字节一致: $FILE_MD5"
  OWNER="$($DOCKER exec "$CONTAINER" sh -c 'stat -c %U /output/data/smoke/A-90.json 2>/dev/null || ls -l /output/data/smoke/A-90.json | awk "{print \$3}"')"
  echo "   结果文件属主: $OWNER"
  if "$DOCKER" exec "$CONTAINER" sh -c 'grep -rl "sk-7T7U" /output /app 2>/dev/null | grep -q .'; then
    echo "   凭据泄漏检查: 发现命中，中止"; exit 1
  fi
  echo "   凭据泄漏检查: 无命中"
  rm -f /tmp/submission_check.json
fi

echo "== 5/6 默认测试套件 =="
uv run pytest -q > /tmp/submission_suite.log 2>&1 || { tail -20 /tmp/submission_suite.log; exit 1; }
tail -1 /tmp/submission_suite.log | sed 's/^/   /'

echo "== 6/6 新归档摘要（请抄进接力记录的归档表）=="
shasum -a 256 "$TAR"
echo "完成。上传 $LINK 或 $TAR 均可（同一内容）。"
