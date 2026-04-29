#!/bin/bash
cd "$(dirname "$0")"
[ -f .venv/bin/activate ] && source .venv/bin/activate

# ── Dependency sanity check ────────────────────────────────────────
# 确认 streamlit 用的 python 和我们的依赖装在同一个解释器里。
# 否则直接停在这里，不让 streamlit 的 traceback 淹没真正原因。
if ! command -v streamlit >/dev/null 2>&1; then
    echo "✗ 未找到 streamlit。请先执行："
    echo "    python3 -m venv .venv && source .venv/bin/activate"
    echo "    pip install -r requirements.txt"
    exit 1
fi
STREAMLIT_PY="$(head -1 "$(command -v streamlit)" | sed 's|^#!||')"
if ! "$STREAMLIT_PY" -c "import av, streamlit, volcenginesdkarkruntime" 2>/dev/null; then
    echo "✗ streamlit 用的 python ($STREAMLIT_PY) 缺少依赖。"
    echo "  通常是因为当前 shell 的 .venv 跟 streamlit 脚本 shebang 指向的 python"
    echo "  不是同一个。修复："
    echo "    rm -rf .venv"
    echo "    python3 -m venv .venv && source .venv/bin/activate"
    echo "    pip install -r requirements.txt"
    exit 1
fi

export ARK_API_KEY="${ARK_API_KEY:-$(cat APIKEY 2>/dev/null | grep -oE 'ark-[a-z0-9-]+')}"

PORT="${PORT:-8501}"
LAN_IP="$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null)"
echo "───────────────────────────────────────────────"
echo "  本机访问: http://localhost:${PORT}"
[ -n "$LAN_IP" ] && echo "  局域网访问: http://${LAN_IP}:${PORT}"
echo "───────────────────────────────────────────────"

streamlit run app.py \
  --server.address 0.0.0.0 \
  --server.port "$PORT" \
  --server.headless true \
  "$@"
