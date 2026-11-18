#!/usr/bin/env bash
# Asks for each key (typing is hidden), writes .env, and starts the services.
# Run on the server:  bash /opt/stream-clipper/deploy/configure.sh
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .env ] || cp .env.example .env
chmod 600 .env

setval() {  # setval KEY VALUE  -> replace KEY=... line in .env (no echo of the value)
  python3 - "$1" "$2" <<'PY'
import sys, re, pathlib
k, v = sys.argv[1], sys.argv[2]
p = pathlib.Path(".env"); s = p.read_text()
line = f"{k}={v}"
s = re.sub(rf"^{re.escape(k)}=.*$", lambda m: line, s, flags=re.M) if re.search(rf"^{re.escape(k)}=", s, re.M) else s + "\n" + line + "\n"
p.write_text(s)
PY
}

ask() {  # ask KEY "Question"  (hidden input; Enter = keep current value)
  local v
  read -r -s -p "$2 (paste, then Enter; typing is hidden; Enter alone = skip): " v; echo
  if [ -n "$v" ]; then setval "$1" "$v"; echo "   ✓ saved"; else echo "   – skipped"; fi
}

echo "=== Clipper setup: paste each key when asked ==="
ask ANTHROPIC_API_KEY  "1/3  Claude API key (sk-ant-...)"
ask TELEGRAM_BOT_TOKEN "2/3  Telegram bot token (from @BotFather)"
ask YOUTUBE_API_KEY    "3/3  YouTube Data API key (optional, for the trend radar)"

systemctl enable --now clipper-bot >/dev/null 2>&1 || true
systemctl restart clipper-bot
echo
echo "Now open your Clip HQ Telegram group and send:  /start"
echo "The bot replies 'Your chat id is -100...'."
read -r -p "Type that number here (it's not secret), then Enter: " chat
if [ -n "$chat" ]; then
  setval TELEGRAM_CHAT_IDS "$chat"
  systemctl restart clipper-bot
  systemctl enable --now clipper-realtime clipper-live clipper-trends >/dev/null 2>&1
  systemctl restart clipper-realtime clipper-live clipper-trends
  echo
  echo "✅ All running. Send /start in the group again: it should say 'Clipper bot online ✅'."
else
  echo "No chat id given. Run this script again when you have it."
fi
