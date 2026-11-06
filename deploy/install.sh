#!/usr/bin/env bash
# Grok Bot desk on a plain Ubuntu/Debian VM (no Docker): Python venv + systemd + Cloudflare quick tunnel.
# Run from the repo root as root:   sudo bash deploy/install.sh
# Re-running is safe. Afterwards:   sudo bash deploy/install.sh login   -> prints the iPhone login link
set -euo pipefail
cd "$(dirname "$0")/.."
REPO="$(pwd)"
DATA="${DESK_DATA:-$REPO/data}"

if [ "${1:-}" = "login" ]; then
  URL="$(journalctl -u grok-tunnel --no-pager -o cat | grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' | tail -1 || true)"
  DESK_DATA="$DATA" "$REPO/.venv/bin/python" -m desk.main --print-login --url "${PUBLIC_URL:-$URL}"
  exit 0
fi

apt-get update -y
apt-get install -y python3 python3-venv curl
python3 -m venv "$REPO/.venv"
"$REPO/.venv/bin/pip" install -q -r "$REPO/requirements.txt"

if ! command -v cloudflared >/dev/null; then
  ARCH="$(dpkg --print-architecture)"
  curl -fsSL -o /tmp/cloudflared.deb "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-${ARCH}.deb"
  dpkg -i /tmp/cloudflared.deb
fi

mkdir -p "$DATA"
chmod 700 "$DATA"

cat > /etc/systemd/system/grok-desk.service <<UNIT
[Unit]
Description=Grok Bot desk (5 agents + API)
After=network-online.target
Wants=network-online.target

[Service]
WorkingDirectory=$REPO
Environment=DESK_DATA=$DATA
Environment=PORT=8000
Environment=HOST=127.0.0.1
Environment=TUNNEL_METRICS=http://127.0.0.1:2000
ExecStart=$REPO/.venv/bin/python -m desk.main
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT

# Quick tunnel by default. For a fixed URL: set TUNNEL_TOKEN=<token> before running this script.
if [ -n "${TUNNEL_TOKEN:-}" ]; then
  TUNNEL_EXEC="/usr/bin/cloudflared tunnel --no-autoupdate --metrics 127.0.0.1:2000 run --token $TUNNEL_TOKEN"
else
  TUNNEL_EXEC="/usr/bin/cloudflared tunnel --no-autoupdate --metrics 127.0.0.1:2000 --url http://127.0.0.1:8000"
fi
cat > /etc/systemd/system/grok-tunnel.service <<UNIT
[Unit]
Description=Cloudflare tunnel for the Grok Bot desk
After=grok-desk.service network-online.target
Wants=network-online.target

[Service]
ExecStart=$TUNNEL_EXEC
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT
chmod 600 /etc/systemd/system/grok-tunnel.service

systemctl daemon-reload
systemctl enable --now grok-desk grok-tunnel
sleep 8
echo
echo "Desk:   systemctl status grok-desk     logs: journalctl -u grok-desk -f"
echo "Tunnel: journalctl -u grok-tunnel | grep trycloudflare"
echo "Login:  sudo bash deploy/install.sh login"
