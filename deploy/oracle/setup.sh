#!/usr/bin/env bash
# One-command setup of ClipMaker on a fresh Ubuntu 24.04 server (Oracle Cloud Always Free,
# Ampere A1, D-028). Safe to run again: it also updates a running install.
#
#   git clone https://github.com/Noam414/ClipMaker.git && cd ClipMaker
#   sudo deploy/oracle/setup.sh
#
# What it does: installs Docker, opens ports 80/443 in the server's own firewall (Oracle's
# Ubuntu images block them even when the cloud security list allows them), writes
# deploy/oracle/.env with a free HTTPS name (<ip>.sslip.io) unless you set CLIPMAKER_DOMAIN,
# builds and starts the app + HTTPS proxy + PO-token provider, adds a nightly database backup,
# and waits until https://<name>/api/health answers.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
COMPOSE=(docker compose -f "$HERE/compose.yml")

log() { printf '\n==> %s\n' "$*"; }
die() { printf 'setup: %s\n' "$*" >&2; exit 1; }

# 1.2.3.4 -> 1-2-3-4.sslip.io (a public DNS name that resolves to that IP)
sslip_name() {
  local ip="$1"
  [[ "$ip" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]] || return 1
  printf '%s.sslip.io\n' "${ip//./-}"
}

# Value of KEY in an env file (last one wins), empty if unset
env_get() {
  local key="$1" file="$2"
  [[ -f "$file" ]] || return 0
  grep -E "^${key}=" "$file" | tail -n 1 | cut -d= -f2- | tr -d '"'"'"
}

# Set KEY=VALUE in an env file, replacing an existing line
env_set() {
  local key="$1" value="$2" file="$3"
  if grep -qE "^${key}=" "$file"; then
    sed -i "s|^${key}=.*|${key}=${value}|" "$file"
  else
    printf '%s=%s\n' "$key" "$value" >>"$file"
  fi
}

public_ip() {
  if [[ -n "${PUBLIC_IP:-}" ]]; then echo "$PUBLIC_IP"; return; fi
  curl -fsS --max-time 10 https://api.ipify.org || curl -fsS --max-time 10 https://ifconfig.me
}

install_docker() {
  if docker compose version >/dev/null 2>&1; then return; fi
  log "Installing Docker (Ubuntu packages)"
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y docker.io docker-compose-v2 curl
  systemctl enable --now docker
}

open_firewall() {
  command -v iptables >/dev/null || return 0
  log "Opening ports 80 and 443 in the server firewall"
  local rule changed=0
  for rule in "-p tcp --dport 80" "-p tcp --dport 443" "-p udp --dport 443"; do
    # shellcheck disable=SC2086
    if ! iptables -C INPUT $rule -m state --state NEW -j ACCEPT 2>/dev/null; then
      # before Oracle's final REJECT rule
      # shellcheck disable=SC2086
      iptables -I INPUT 1 $rule -m state --state NEW -j ACCEPT
      changed=1
    fi
  done
  if [[ $changed == 1 ]]; then
    if command -v netfilter-persistent >/dev/null; then
      netfilter-persistent save
    else
      echo "note: netfilter-persistent is missing, so the rules last until reboot" >&2
    fi
  fi
}

write_env() {
  local env="$HERE/.env"
  if [[ ! -f "$env" ]]; then
    cp "$HERE/env.example" "$env"
    chmod 600 "$env"
  fi
  local domain
  domain="$(env_get CLIPMAKER_DOMAIN "$env")"
  if [[ -z "$domain" ]]; then
    local ip
    ip="$(public_ip)" || die "couldn't find the public IP; run again with PUBLIC_IP=<ip>"
    domain="$(sslip_name "$ip")" || die "unexpected public IP: $ip"
    env_set CLIPMAKER_DOMAIN "$domain" "$env"
  fi
  DOMAIN="$domain"
  DATA_PATH="$(env_get CLIPMAKER_DATA_PATH "$env")"
  DATA_PATH="${DATA_PATH:-/srv/clipmaker/data}"
  mkdir -p "$DATA_PATH/backups" "$DATA_PATH/secrets"
  chmod 700 "$DATA_PATH/secrets"
}

install_backup_cron() {
  log "Nightly database backup at 03:30 (kept 14 days in $DATA_PATH/backups)"
  cat >/etc/cron.d/clipmaker-backup <<EOF
# ClipMaker: gzipped SQLite copy, safe while the app runs (clipmaker backup)
30 3 * * * root ${COMPOSE[*]} exec -T clipmaker clipmaker backup --keep 14 >>/var/log/clipmaker-backup.log 2>&1
EOF
  chmod 644 /etc/cron.d/clipmaker-backup
}

start() {
  log "Building and starting ClipMaker (the first build takes several minutes)"
  "${COMPOSE[@]}" up -d --build --remove-orphans
}

wait_healthy() {
  log "Waiting for https://$DOMAIN/api/health (the certificate can take a minute)"
  local _
  for _ in $(seq 1 60); do
    if curl -fsS --max-time 10 "https://$DOMAIN/api/health" >/dev/null 2>&1; then
      printf '\nClipMaker is live: https://%s\n' "$DOMAIN"
      return 0
    fi
    sleep 5
  done
  echo "The site didn't answer over HTTPS yet. Check:" >&2
  echo "  - the Oracle security list allows TCP 80 and 443 from 0.0.0.0/0 (H-002 step 3)" >&2
  echo "  - ${COMPOSE[*]} logs caddy" >&2
  return 1
}

main() {
  [[ $EUID -eq 0 ]] || die "run with sudo"
  [[ -f "$REPO/pyproject.toml" ]] || die "run from a ClipMaker checkout"
  install_docker
  open_firewall
  write_env
  start
  install_backup_cron
  wait_healthy
}

# Tests source this file for the helpers only
if [[ "${CLIPMAKER_SETUP_SOURCE_ONLY:-0}" != "1" ]]; then
  main "$@"
fi
