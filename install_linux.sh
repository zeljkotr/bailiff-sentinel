#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_NAME="paycheck-sentinel"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
ENV_FILE="${APP_DIR}/.env"
VENV_DIR="${APP_DIR}/venv"
DB_NAME="bailiff"
DB_USER="bailiff"
APP_PORT="5000"

INSTALL_COMPLETE=0
PHASE="initialization"

die() {
    printf 'GREŠKA: %s\n' "$*" >&2
    exit 1
}

on_exit() {
    local rc=$?
    trap - EXIT

    if [[ "${INSTALL_COMPLETE}" -ne 1 && "$rc" -ne 0 ]]; then
        printf '\nGREŠKA u fazi: %s (exit %s).\n' "$PHASE" "$rc" >&2
        printf 'Automatsko brisanje resursa je isključeno.\n' >&2
        printf 'Proveri navedenu fazu pre ponovnog pokušaja.\n' >&2
    fi

    exit "$rc"
}
trap on_exit EXIT

if [[ "${EUID}" -ne 0 ]]; then
    [[ -n "${SUDO_USER:-}" && "${SUDO_USER}" != "root" ]] ||
        die "Pokreni sa sudo iz naloga običnog korisnika."
    exec sudo -E bash "$0" "$@"
fi

APP_USER="${SUDO_USER:-}"
[[ -n "$APP_USER" && "$APP_USER" != "root" ]] ||
    die "Nije moguće odrediti vlasnika aplikacije."
id "$APP_USER" >/dev/null 2>&1 ||
    die "Korisnik ${APP_USER} ne postoji."
APP_GROUP="$(id -gn "$APP_USER")"

[[ -f "${APP_DIR}/app.py" ]] || die "Nedostaje app.py."
[[ -f "${APP_DIR}/requirements.txt" ]] || die "Nedostaje requirements.txt."
[[ -f "${APP_DIR}/paycheck_sentinel/db.py" ]] || die "Nedostaje paycheck_sentinel/db.py."
[[ -f "${APP_DIR}/paycheck_sentinel/licensing.py" ]] || die "Nedostaje paycheck_sentinel/licensing.py."
[[ -d "${APP_DIR}/templates" ]] || die "Nedostaje templates direktorijum."

[[ ! -e "$ENV_FILE" ]] || die ".env već postoji; postojeća konfiguracija se ne menja."
[[ ! -e "$VENV_DIR" ]] || die "venv već postoji; potrebna je ručna provera."
[[ ! -e "$SERVICE_FILE" ]] || die "systemd konfiguracija servisa već postoji."
if systemctl is-active --quiet "$SERVICE_NAME"; then
    die "Servis ${SERVICE_NAME} već je aktivan."
fi
if ss -ltnH 2>/dev/null | awk '{print $4}' | grep -Eq '(^|:|\])5000$'; then
    die "Port 5000 je zauzet; ne menjam postojeći servis."
fi

printf 'Provere projekta su završene.\n'
PHASE="system packages"
printf 'Instaliram sistemske pakete...\n'

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y \
    python3 python3-venv python3-pip \
    postgresql postgresql-client \
    redis-server ca-certificates curl

PHASE="PostgreSQL startup"
systemctl start postgresql

PHASE="PostgreSQL preflight"
# SQL greške su fatalne; postojeće uloge i baze se ne preuzimaju.
if ! ROLE_EXISTS="$(sudo -u postgres psql -v ON_ERROR_STOP=1 -Atqc \
    "SELECT 1 FROM pg_roles WHERE rolname='${DB_USER}'")"; then
    die "Ne mogu da proverim PostgreSQL ulogu."
fi
[[ "$ROLE_EXISTS" != "1" ]] ||
    die "PostgreSQL uloga ${DB_USER} već postoji; ne menjam je."

if ! DB_EXISTS="$(sudo -u postgres psql -v ON_ERROR_STOP=1 -Atqc \
    "SELECT 1 FROM pg_database WHERE datname='${DB_NAME}'")"; then
    die "Ne mogu da proverim PostgreSQL bazu."
fi
[[ "$DB_EXISTS" != "1" ]] ||
    die "Baza ${DB_NAME} već postoji; ne koristim niti menjam postojeću bazu."

PHASE="enabling PostgreSQL and Redis"
systemctl enable --now postgresql
systemctl enable --now redis-server

DB_PASSWORD="$(od -An -N32 -tx1 /dev/urandom | tr -d ' \n')"
FLASK_SECRET_KEY="$(od -An -N32 -tx1 /dev/urandom | tr -d ' \n')"
[[ "${#DB_PASSWORD}" -eq 64 ]] || die "Generisanje DB lozinke nije uspelo."
[[ "${#FLASK_SECRET_KEY}" -eq 64 ]] || die "Generisanje Flask ključa nije uspelo."

PHASE="creating environment configuration"
cat > "$ENV_FILE" <<EOF
FLASK_SECRET_KEY=${FLASK_SECRET_KEY}
DB_PASSWORD=${DB_PASSWORD}
DB_HOST=localhost
DB_PORT=5432
DB_NAME=${DB_NAME}
DB_USER=${DB_USER}
REDIS_HOST=localhost
REDIS_PORT=6379
PAYCHECK_SENTINEL_LICENSE=
EOF
CREATED_ENV=1
chmod 600 "$ENV_FILE"
chown "$APP_USER:$APP_GROUP" "$ENV_FILE"

PHASE="creating PostgreSQL role"
sudo -u postgres psql -v ON_ERROR_STOP=1 \
    -c "CREATE ROLE ${DB_USER} LOGIN PASSWORD '${DB_PASSWORD}'"
CREATED_ROLE=1

PHASE="creating PostgreSQL database"
sudo -u postgres createdb --owner="$DB_USER" "$DB_NAME"
CREATED_DB=1

PHASE="creating Python virtual environment"
sudo -u "$APP_USER" python3 -m venv "$VENV_DIR"
CREATED_VENV=1
sudo -u "$APP_USER" "$VENV_DIR/bin/python" -m pip install --upgrade pip
sudo -u "$APP_USER" "$VENV_DIR/bin/python" -m pip install \
    -r "${APP_DIR}/requirements.txt"

PHASE="writing systemd service"
cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=Bailiff Sentinel
After=network.target postgresql.service redis-server.service
Requires=postgresql.service redis-server.service

[Service]
Type=simple
User=${APP_USER}
Group=${APP_GROUP}
WorkingDirectory=${APP_DIR}
EnvironmentFile=${ENV_FILE}
Environment=PYTHONUNBUFFERED=1
ExecStart=${VENV_DIR}/bin/gunicorn --workers 3 --bind 0.0.0.0:${APP_PORT} --timeout 120 app:app
Restart=on-failure
RestartSec=5
UMask=0077

[Install]
WantedBy=multi-user.target
EOF
CREATED_SERVICE=1
chmod 644 "$SERVICE_FILE"
PHASE="starting application service"
systemctl daemon-reload
systemctl enable --now "$SERVICE_NAME"

PHASE="application health check"
sleep 3
systemctl is-active --quiet "$SERVICE_NAME" ||
    die "Servis nije aktivan; pregledaj systemctl status i journalctl."
curl --fail --silent --show-error --max-time 10 \
    "http://127.0.0.1:${APP_PORT}/" -o /dev/null ||
    die "HTTP provera nije uspela."

INSTALL_COMPLETE=1
trap - ERR

printf '\nInstalacija je završena.\n'
printf 'Direktorijum: %s\n' "$APP_DIR"
printf 'Servis: %s\n' "$SERVICE_NAME"
printf 'Status: sudo systemctl status %s\n' "$SERVICE_NAME"
printf 'Logovi: sudo journalctl -u %s -n 100 --no-pager\n' "$SERVICE_NAME"
printf 'Lokalna adresa: http://127.0.0.1:%s/\n' "$APP_PORT"
printf 'Mrezna adresa: http://%s:%s/\n' "$(ip -4 route get 1.1.1.1 | awk '{for(i=1;i<=NF;i++) if($i=="src"){print $(i+1); exit}}')" "$APP_PORT"
