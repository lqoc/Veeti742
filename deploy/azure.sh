#!/usr/bin/env bash
# Grok Bot on an Azure VM (Ubuntu or Debian): Docker + the desk + a Cloudflare quick tunnel.
# No inbound ports needed. Survives reboots. Checks GitHub every 15 min and updates itself (keeps your data).
#
#   Install:      curl -fsSL https://raw.githubusercontent.com/lqoc/Veeti742/ccr-8f54846f-6j3ph7/deploy/azure.sh | sudo bash
#   Link again:   sudo bash /opt/grok-bot/deploy/azure.sh link
#   Update now:   sudo bash /opt/grok-bot/deploy/azure.sh update
#   Stop auto-update:  sudo rm /etc/cron.d/grok-bot
set -euo pipefail

main() {
  REPO_URL="${REPO_URL:-https://github.com/lqoc/Veeti742.git}"
  BRANCH="${BRANCH:-ccr-8f54846f-6j3ph7}"
  DIR="${DIR:-/opt/grok-bot}"
  LOG="${LOG:-/var/log/grok-bot-install.log}"
  WAIT_S="${WAIT_S:-180}"
  exec 3>&1 </dev/null            # fd 3 = what you see; nothing below reads the piped script as input

  case "${1:-install}" in
    link) link; return $? ;;
    update) update; return $? ;;
    install) install ;;
    *) say "usage: azure.sh [install|link|update]"; return 1 ;;
  esac
}

say() { printf '%s\n' "$*" >&3; }

link() {
  cd "$DIR"
  local url="" i login
  for i in $(seq 1 $((WAIT_S / 3))); do
    url="$(docker compose logs tunnel 2>/dev/null | grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' | tail -1 || true)"
    [ -n "$url" ] && break
    sleep 3
  done
  for i in $(seq 1 40); do
    docker compose exec -T desk python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3)" \
      >/dev/null 2>&1 && break
    sleep 3
  done
  if [ -z "$url" ]; then
    say "The desk is running, but the tunnel has no URL yet."
    say "Wait a minute, then run:  sudo bash $DIR/deploy/azure.sh link"
    return 1
  fi
  login="$(docker compose exec -T desk python -m desk.main --print-login --url "$url" | tail -1)"
  say ""
  say "Grok Bot is running in PAPER mode."
  say "Site:  $url"
  say "Open this login link once on your phone (Chrome or Samsung Internet). Keep it private:"
  say "$login"
}

update() {
  exec 1>>"$LOG" 2>&1
  cd "$DIR"
  local before after
  before="$(git rev-parse HEAD)"
  git fetch --depth 1 origin "$BRANCH"
  after="$(git rev-parse FETCH_HEAD)"
  if [ "$before" != "$after" ]; then
    echo "== update $(date -u) $before -> $after"
    git checkout -B "$BRANCH" FETCH_HEAD
    docker compose up -d --build desk        # only the desk restarts; the tunnel URL stays the same
    docker image prune -f >/dev/null 2>&1 || true
  fi
}

install() {
  if [ "$(id -u)" != 0 ]; then say "Run it with sudo."; return 1; fi
  exec 1>>"$LOG" 2>&1
  echo "== install $(date -u)"
  say "Installing Grok Bot (3-6 minutes). Details: $LOG"
  export DEBIAN_FRONTEND=noninteractive
  if command -v apt-get >/dev/null; then
    apt-get update -y
    apt-get install -y git curl ca-certificates
  fi
  if ! command -v docker >/dev/null; then
    say "· installing Docker"
    curl -fsSL https://get.docker.com -o /tmp/get-docker.sh
    sh /tmp/get-docker.sh
  fi
  if ! docker compose version >/dev/null 2>&1; then
    apt-get install -y docker-compose-plugin
  fi
  systemctl enable --now docker >/dev/null 2>&1 || true

  say "· getting the code ($BRANCH)"
  if [ -d "$DIR/.git" ]; then
    git -C "$DIR" fetch --depth 1 origin "$BRANCH"
    git -C "$DIR" checkout -B "$BRANCH" FETCH_HEAD     # data/ is untracked and stays
  else
    git clone --depth 1 --branch "$BRANCH" "$REPO_URL" "$DIR"
  fi
  cd "$DIR"
  mkdir -p data
  chmod 700 data

  say "· building and starting the desk + tunnel"
  docker compose up -d --build --remove-orphans

  printf '%s\n' "*/15 * * * * root /bin/bash $DIR/deploy/azure.sh update" > /etc/cron.d/grok-bot
  chmod 644 /etc/cron.d/grok-bot
  say "· auto-update on (every 15 min from GitHub). Turn off: sudo rm /etc/cron.d/grok-bot"

  say "· waiting for the tunnel"
  link
}

main "$@"
