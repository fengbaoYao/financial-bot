#!/usr/bin/env bash
# Sync local OpenAshare → SV /opt/financial-bot, write VERSION/deploy.json, rebuild frontend.
# Usage (on Mac with key, or box after CopyToBox of tree):
#   FB_DEPLOY_KEY=/tmp/fb_deploy_key ./scripts/deploy-sv.sh
# Env:
#   FB_SV_HOST=ubuntu@43.130.36.53
#   FB_SV_ROOT=/opt/financial-bot
#   FB_DEPLOY_KEY path to SSH private key
#   FB_SKIP_FRONTEND=1 to skip next build
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HOST="${FB_SV_HOST:-ubuntu@43.130.36.53}"
REMOTE_ROOT="${FB_SV_ROOT:-/opt/financial-bot}"
KEY="${FB_DEPLOY_KEY:-/tmp/fb_deploy_key}"
SSH=(ssh -i "$KEY" -o StrictHostKeyChecking=no -o IdentitiesOnly=yes "$HOST")
TZ_LABEL="Asia/Shanghai"

cd "$ROOT"
if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "not a git repo: $ROOT" >&2
  exit 1
fi
if [[ -n "$(git status --porcelain)" ]]; then
  echo "working tree dirty — commit or stash before deploy" >&2
  git status -sb | head -40 >&2
  exit 1
fi

COMMIT="$(git rev-parse HEAD)"
SHORT="$(git rev-parse --short HEAD)"
TAG="$(git describe --tags --exact-match 2>/dev/null || true)"
BRANCH="$(git rev-parse --abbrev-ref HEAD)"
DEPLOYED_AT="$(TZ=$TZ_LABEL date '+%Y-%m-%d %H:%M:%S %Z')"
TS="$(TZ=$TZ_LABEL date '+%Y%m%d%H%M%S')"

echo "deploy $SHORT ($COMMIT) tag=${TAG:-none} → $HOST:$REMOTE_ROOT"

"${SSH[@]}" "set -e
  ROOT=$REMOTE_ROOT
  SHORT=$SHORT
  TS=$TS
  mkdir -p \"\$ROOT/logs\"
  # Lightweight backups named with commit short hash (avoid copying venv)
  mkdir -p \"\$ROOT/logs\"
  if [ -d \"\$ROOT/backend/api\" ]; then
    mkdir -p \"\$ROOT/backend.bak-\${SHORT}-\${TS}\"
    cp -a \"\$ROOT/backend/api\" \"\$ROOT/backend.bak-\${SHORT}-\${TS}/api\"
  fi
  if [ -d \"\$ROOT/frontend-src\" ]; then
    mkdir -p \"\$ROOT/frontend-src.bak-\${SHORT}-\${TS}\"
    rsync -a --exclude node_modules --exclude .next \"\$ROOT/frontend-src/\" \"\$ROOT/frontend-src.bak-\${SHORT}-\${TS}/\" || true
  fi
  if [ -d \"\$ROOT/frontend\" ]; then
    mkdir -p \"\$ROOT/frontend.bak-\${SHORT}-\${TS}\"
    rsync -a --exclude node_modules \"\$ROOT/frontend/\" \"\$ROOT/frontend.bak-\${SHORT}-\${TS}/\" || true
  fi
  echo backup_ok
"

upload() {
  local src="$1" dest="$2"
  "${SSH[@]}" "mkdir -p \"\$(dirname '$dest')\" && cat > '$dest'" < "$src"
}

# Stream trees via tar (fast); exclude caches and junk
tar_sync() {
  local src_rel="$1" dest_rel="$2"
  echo "sync $src_rel → $dest_rel"
  tar -C "$ROOT" -czf - \
    --exclude='__pycache__' --exclude='*.pyc' --exclude='.DS_Store' \
    --exclude='*.bak*' --exclude='node_modules' --exclude='.next' \
    "$src_rel" | "${SSH[@]}" "mkdir -p '$REMOTE_ROOT/$dest_rel' && tar -C '$REMOTE_ROOT/$dest_rel' --strip-components=1 -xzf -"
}

# api → backend/api (tar contains top folder name api/)
echo "sync api → backend/"
tar -C "$ROOT" -czf - \
  --exclude='__pycache__' --exclude='*.pyc' --exclude='.DS_Store' \
  api | "${SSH[@]}" "tar -C '$REMOTE_ROOT/backend' -xzf -"

echo "sync app/components/lib/public → frontend-src/"
tar -C "$ROOT" -czf - \
  --exclude='__pycache__' --exclude='.DS_Store' --exclude='*.bak*' \
  --exclude='node_modules' --exclude='.next' \
  app components lib public 2>/dev/null \
  | "${SSH[@]}" "tar -C '$REMOTE_ROOT/frontend-src' -xzf -"

for f in package.json package-lock.json next.config.ts tsconfig.json next-env.d.ts middleware.ts; do
  if [ -f "$ROOT/$f" ]; then
    upload "$ROOT/$f" "$REMOTE_ROOT/frontend-src/$f"
  fi
done
if [ -f "$ROOT/requirements_api.txt" ]; then
  upload "$ROOT/requirements_api.txt" "$REMOTE_ROOT/backend/requirements_api.txt"
fi
if [ -f "$ROOT/scripts/run_api_prod.sh" ]; then
  upload "$ROOT/scripts/run_api_prod.sh" "$REMOTE_ROOT/backend/scripts/run_api_prod.sh" || true
fi

# Keep monorepo mirror in sync: frontend-src/api must match backend/api
echo "sync backend/api → frontend-src/api (prevent stale mirror)"
"${SSH[@]}" "rm -rf '$REMOTE_ROOT/frontend-src/api' && mkdir -p '$REMOTE_ROOT/frontend-src/api' && cp -a '$REMOTE_ROOT/backend/api/.' '$REMOTE_ROOT/frontend-src/api/'"

# Also sync tests/scripts/docs used for parity checks
for dir in tests scripts docs; do
  if [ -d "$ROOT/$dir" ]; then
    echo "sync $dir → frontend-src/$dir"
    tar -C "$ROOT" -czf - --exclude='__pycache__' --exclude='.DS_Store' --exclude='superpowers' "$dir" \
      | "${SSH[@]}" "tar -C '$REMOTE_ROOT/frontend-src' -xzf -"
  fi
done

# Write VERSION + deploy.json locally then upload
cat > /tmp/fb-VERSION <<VER
git_commit=$COMMIT
git_short=$SHORT
git_tag=${TAG:-}
git_branch=$BRANCH
deployed_at=$DEPLOYED_AT
backend=$REMOTE_ROOT/backend
frontend=$REMOTE_ROOT/frontend
frontend_src=$REMOTE_ROOT/frontend-src
VER
python3 - <<PY
import json
print(json.dumps({
  "git_commit": "$COMMIT",
  "git_short": "$SHORT",
  "git_tag": "$TAG",
  "git_branch": "$BRANCH",
  "deployed_at": "$DEPLOYED_AT",
  "timezone": "$TZ_LABEL",
  "paths": {
    "backend": "$REMOTE_ROOT/backend",
    "frontend": "$REMOTE_ROOT/frontend",
    "frontend_src": "$REMOTE_ROOT/frontend-src",
  },
}, ensure_ascii=False, indent=2))
PY > /tmp/fb-deploy.json
upload /tmp/fb-VERSION "$REMOTE_ROOT/VERSION"
upload /tmp/fb-deploy.json "$REMOTE_ROOT/deploy.json"
cp /tmp/fb-VERSION "$ROOT/VERSION.local"
cp /tmp/fb-deploy.json "$ROOT/deploy.json.local"

"${SSH[@]}" "set -e
  ROOT=$REMOTE_ROOT
  # backend deps + import smoke + restart
  cd \"\$ROOT/backend\"
  if [ -x .venv/bin/python ]; then PY=.venv/bin/python; PIP=.venv/bin/pip; else PY=python3; PIP=pip3; fi
  if [ -f requirements_api.txt ]; then
    \$PIP install -r requirements_api.txt
  fi
  \$PY -c 'from api.financial_bot.gap_synthesis import MODEL_FILL_TOOL; import tonghuashun_ifind_skill; from api.datasources.ifind_provider import IfindProvider; from api.agent_pydantic import should_prefetch_ifind, prefetch_ifind_skill; print(MODEL_FILL_TOOL, tonghuashun_ifind_skill.__name__, IfindProvider().is_configured, should_prefetch_ifind("茅台股价"))'
  sudo systemctl restart financial-bot-backend.service
"

if [ "${FB_SKIP_FRONTEND:-0}" != "1" ]; then
  "${SSH[@]}" "set -e
    cd $REMOTE_ROOT/frontend-src
    export FB_STANDALONE=1 NODE_ENV=production FINANCIAL_BOT_PUBLIC_BETA=false
    npm run build
    rsync -a --delete --exclude .env.production --exclude node_modules \
      .next/standalone/ $REMOTE_ROOT/frontend/
    mkdir -p $REMOTE_ROOT/frontend/.next
    rsync -a .next/static/ $REMOTE_ROOT/frontend/.next/static/
    if [ -d public ]; then rsync -a public/ $REMOTE_ROOT/frontend/public/; fi
    sudo systemctl restart financial-bot-web.service
  "
fi

"${SSH[@]}" "set -e
  sleep 2
  systemctl is-active financial-bot-backend financial-bot-web financial-bot-tunnel financial-bot-web-tunnel
  echo '--- VERSION ---'
  cat $REMOTE_ROOT/VERSION
  curl -sS -m 15 http://127.0.0.1:8000/api/bot/status || curl -sS -m 15 http://127.0.0.1:8000/api/health || true
  echo
  WEB=\$(cat $REMOTE_ROOT/web-public-url.txt 2>/dev/null || true)
  API=\$(cat $REMOTE_ROOT/api-public-url.txt 2>/dev/null || true)
  [ -n \"\$WEB\" ] && curl -sS -m 15 \"\$WEB/bot\" -o /dev/null -w \"bot:%{http_code}\\n\"
  [ -n \"\$WEB\" ] && curl -sS -m 15 \"\$WEB/work\" -o /dev/null -w \"work:%{http_code}\\n\"
  [ -n \"\$WEB\" ] && curl -sS -m 15 \"\$WEB/landing\" -o /dev/null -w \"landing:%{http_code}\\n\"
  [ -n \"\$API\" ] && curl -sS -m 15 \"\$API/api/bot/status\" -o /tmp/bot-status.json -w \"api_status:%{http_code}\\n\" && head -c 400 /tmp/bot-status.json; echo
"
echo "deploy done: $SHORT ${TAG}"
