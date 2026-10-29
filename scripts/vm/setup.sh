#!/usr/bin/env bash
# Collector VM setup (P-044, D-058): closing-odds captures and the development database.
#
#   ssh -i ~/.ssh/presagio_vm root@<VM> 'bash -s' < scripts/vm/setup.sh
#   ssh -i ~/.ssh/presagio_vm root@<VM> 'PRESAGIO_REF=<branch> bash -s' < scripts/vm/setup.sh
#
# Runs as root on Ubuntu 24.04 (noble) x86_64 with outbound HTTPS. PRESAGIO_REF is the branch
# of https://github.com/oghdy/Presagio checked out in /opt/presagio (default main).
# Tailscale must be logged in before the database part (`tailscale up` once, a human approves the
# printed URL): without a Tailscale address the script installs everything else, then stops with
# exit 3 and says what to do. Run it again afterwards.
#
# Idempotent: every step checks first, and a second run changes nothing (no package upgrades;
# files are copied only when they differ; /opt/presagio/.env is created once and never
# overwritten, only its TAILSCALE_IP line follows `tailscale ip -4`). Nothing secret is printed.
# Run it again outside the capture slots (KST 03:30-12:00 and 15:45-16:30).
set -euo pipefail

REPO_URL="https://github.com/oghdy/Presagio.git"
REF="${PRESAGIO_REF:-main}"
APP="/opt/presagio"
VM="$APP/scripts/vm"
# uv is a pinned standalone release from GitHub, checked against its published sha256 (the
# install.sh one-liner fetches the same binary; this way nothing is piped into a shell).
UV_VERSION="0.12.10"
UV_TARBALL="uv-x86_64-unknown-linux-gnu.tar.gz"
UV_URL="https://github.com/astral-sh/uv/releases/download/$UV_VERSION/$UV_TARBALL"
UV="/usr/local/bin/uv"
SWAPFILE="/swapfile"
SWAP_SIZE="2G"
UNITS=(presagio-odds.service presagio-odds.timer presagio-backup.service presagio-backup.timer)
DOCKER_DROPIN="/etc/systemd/system/docker.service.d/10-after-tailscale.conf"
export DEBIAN_FRONTEND=noninteractive

say() { echo "[setup] $*"; }
die() {
  echo "[setup] FAILED: $*" >&2
  exit 1
}

# install_file SRC DEST MODE: copy only when different, say which.
CHANGED=0
install_file() {
  if [ -f "$2" ] && cmp -s "$1" "$2"; then
    say "unchanged $2"
  else
    install -D -m "$3" "$1" "$2"
    say "updated   $2"
    CHANGED=1
  fi
}

# apt_repo NAME KEY_URL KEYRING LIST_LINE: a signed apt source, written only when it differs.
APT_CHANGED=0
apt_repo() {
  local list="/etc/apt/sources.list.d/$1.list"
  if [ ! -s "$3" ]; then
    install -d -m 0755 "$(dirname "$3")"
    curl -fsSL "$2" -o "$3"
    chmod 644 "$3"
    APT_CHANGED=1
  fi
  if [ "$(cat "$list" 2> /dev/null)" != "$4" ]; then
    echo "$4" > "$list"
    APT_CHANGED=1
  fi
}

# --- preconditions -------------------------------------------------------------------------------
[ "$(id -u)" -eq 0 ] || die "run as root"
# shellcheck disable=SC1091  # the VM's own file
. /etc/os-release
[ "${VERSION_CODENAME:-}" = "noble" ] || die "expected Ubuntu 24.04 (noble), got ${PRETTY_NAME:-?}"
[ "$(uname -m)" = "x86_64" ] || die "expected x86_64, got $(uname -m)"

# --- packages: base, Docker Engine + compose plugin, Tailscale (official apt repositories) ------
# --no-upgrade: install what is missing, never upgrade what is there, so a second run does not
# restart dockerd (and the database) in the middle of a capture. unattended-upgrades covers the
# Ubuntu origins only; Docker and Tailscale are upgraded by hand (README section 8).
say "apt: base packages"
apt-get update -qq
apt-get install -y -qq --no-upgrade ca-certificates curl git openssl ufw gzip > /dev/null
apt_repo docker https://download.docker.com/linux/ubuntu/gpg /etc/apt/keyrings/docker.asc \
  "deb [arch=amd64 signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu noble stable"
apt_repo tailscale https://pkgs.tailscale.com/stable/ubuntu/noble.noarmor.gpg \
  /usr/share/keyrings/tailscale-archive-keyring.gpg \
  "deb [signed-by=/usr/share/keyrings/tailscale-archive-keyring.gpg] https://pkgs.tailscale.com/stable/ubuntu noble main"
[ "$APT_CHANGED" -eq 0 ] || apt-get update -qq
say "apt: docker-ce, docker-compose-plugin, tailscale"
apt-get install -y -qq --no-upgrade docker-ce docker-ce-cli containerd.io docker-buildx-plugin \
  docker-compose-plugin tailscale > /dev/null
systemctl enable --now docker.service tailscaled.service > /dev/null 2>&1

# --- uv -------------------------------------------------------------------------------------------
if [ "$("$UV" --version 2> /dev/null | cut -d' ' -f2)" = "$UV_VERSION" ]; then
  say "uv $UV_VERSION already installed"
else
  say "uv: installing $UV_VERSION to /usr/local/bin"
  tmp="$(mktemp -d)"
  curl -fsSL "$UV_URL" -o "$tmp/$UV_TARBALL"
  curl -fsSL "$UV_URL.sha256" -o "$tmp/$UV_TARBALL.sha256"
  (cd "$tmp" && sha256sum -c --quiet "$UV_TARBALL.sha256") || die "uv checksum mismatch"
  tar -xzf "$tmp/$UV_TARBALL" -C "$tmp"
  install -m 0755 "$tmp/uv-x86_64-unknown-linux-gnu/uv" "$tmp/uv-x86_64-unknown-linux-gnu/uvx" \
    /usr/local/bin/
  rm -rf "$tmp"
fi

# --- swap (1 GB RAM: Postgres + uv sync + the DigitalOcean agent) --------------------------------
if swapon --show=NAME --noheadings | grep -qx "$SWAPFILE"; then
  say "swap $SWAPFILE already on"
else
  if [ ! -f "$SWAPFILE" ]; then
    fallocate -l "$SWAP_SIZE" "$SWAPFILE"
    chmod 600 "$SWAPFILE"
    mkswap "$SWAPFILE" > /dev/null
  fi
  swapon "$SWAPFILE"
  say "swap $SWAPFILE on ($SWAP_SIZE)"
fi
grep -q "^$SWAPFILE " /etc/fstab || echo "$SWAPFILE none swap sw 0 0" >> /etc/fstab

# --- firewall: SSH and Tailscale's UDP port in, nothing else (5433 is bound to Tailscale only) --
ufw allow 22/tcp > /dev/null
ufw allow 41641/udp > /dev/null
ufw default deny incoming > /dev/null
ufw default allow outgoing > /dev/null
ufw --force enable > /dev/null
say "ufw: $(ufw status | head -n 1)"

# --- the repository and its venv ------------------------------------------------------------------
if [ ! -d "$APP/.git" ]; then
  say "git: cloning $REPO_URL into $APP"
  git clone --quiet "$REPO_URL" "$APP"
fi
git -C "$APP" fetch --quiet origin "$REF"
git -C "$APP" checkout --quiet "$REF" 2> /dev/null \
  || git -C "$APP" checkout --quiet -b "$REF" --track "origin/$REF"
git -C "$APP" merge --quiet --ff-only "origin/$REF"
say "git: $REF at $(git -C "$APP" log -1 --format='%h %s')"
(cd "$APP" && "$UV" sync --frozen --no-dev --quiet)
say "uv sync --frozen --no-dev: $("$APP/.venv/bin/python" --version)"

# --- Tailscale address (needed from here on) ------------------------------------------------------
TS_IP="$(tailscale ip -4 2> /dev/null | head -n 1 || true)"
if [ -z "$TS_IP" ]; then
  echo "[setup] STOP: Tailscale is not logged in. On the VM run" >&2
  echo "[setup]   tailscale up --hostname=presagio-collector" >&2
  echo "[setup] have a human open the printed login URL, then run this script again." >&2
  exit 3
fi
say "tailscale: $TS_IP"

# --- /opt/presagio/.env (one generated password for the container and DATABASE_URL) -------------
ENV="$APP/.env"
if [ -f "$ENV" ]; then
  say "kept existing $ENV"
  if ! grep -qx "TAILSCALE_IP=$TS_IP" "$ENV"; then
    sed -i "s/^TAILSCALE_IP=.*/TAILSCALE_IP=$TS_IP/" "$ENV"
    say "updated TAILSCALE_IP in $ENV"
  fi
else
  password="$(openssl rand -hex 24)"
  (umask 077 && sed -e "s/@POSTGRES_PASSWORD@/$password/g" -e "s/@TAILSCALE_IP@/$TS_IP/g" \
    "$VM/env.vm.template" > "$ENV")
  unset password
  say "created $ENV from scripts/vm/env.vm.template (new POSTGRES_PASSWORD, ODDS_API_KEY empty)"
fi
chmod 600 "$ENV"

# --- Docker starts after tailscaled (the 5433 bind needs the Tailscale address) ------------------
install_file "$VM/docker-after-tailscale.conf" "$DOCKER_DROPIN" 644

# --- systemd units --------------------------------------------------------------------------------
for unit in "${UNITS[@]}"; do
  install_file "$VM/$unit" "/etc/systemd/system/$unit" 644
done
[ "$CHANGED" -eq 0 ] || systemctl daemon-reload
install -d -m 0755 /var/log/presagio /var/lib/presagio /var/lib/presagio/slots
install -d -m 0700 /var/backups/presagio

# --- database: compose up (base file + compose.vm.yml via COMPOSE_FILE in .env), migrations ------
cd "$APP"
docker compose up -d --wait --quiet-pull > /dev/null 2>&1 \
  || die "docker compose up failed (cd $APP && docker compose up -d; docker compose logs postgres)"
docker compose ps --format '{{.Name}} {{.Status}}' | sed 's/^/[setup] compose: /'
"$UV" run --no-sync alembic upgrade head 2>&1 | sed 's/^/[setup] alembic: /'
say "alembic current: $("$UV" run --no-sync alembic current 2> /dev/null)"

systemctl enable --now presagio-odds.timer presagio-backup.timer > /dev/null 2>&1
say "timers:"
systemctl list-timers --all --no-pager 'presagio-*' | sed 's/^/[setup]   /'
say "done: docker $(docker version --format '{{.Server.Version}}')," \
  "compose $(docker compose version --short), tailscale $(tailscale version | head -n 1), uv $UV_VERSION"
