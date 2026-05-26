#!/bin/bash

set -euo pipefail

R='\033[0;31m' G='\033[0;32m' Y='\033[1;33m' C='\033[0;36m' B='\033[1m' N='\033[0m'
ok()   { echo -e "${G}ok${N}   $*"; }
info() { echo -e "${C}..${N}   $*"; }
warn() { echo -e "${Y}warn${N} $*"; }
die()  { echo -e "${R}err${N}  $*" >&2; exit 1; }
hdr()  { echo -e "\n${B}=== $* ===${N}"; }

INSTALL_DIR="/opt/dga-detector"
SERVICE_NAME="dga-detector"
SERVICE_USER="dga-detector"
SERVICE_GROUP="dga-detector"
PORT=""
WORKERS=""
THRESHOLD="0.60"
USE_TLS=true
MODE=""

PACK_FILES=(
    install.sh
    serve.py
    train.py
    features.py
    requirements.txt
    model.pkl
    certs/
)

while [[ $# -gt 0 ]]; do
    case $1 in
        --pack)      MODE=pack;      shift ;;
        --install)   MODE=install;   shift ;;
        --uninstall) MODE=uninstall; shift ;;
        --dir)       INSTALL_DIR="$2"; shift 2 ;;
        --port)      PORT="$2";      shift 2 ;;
        --workers)   WORKERS="$2";   shift 2 ;;
        --threshold) THRESHOLD="$2"; shift 2 ;;
        --no-tls)    USE_TLS=false;  shift ;;
        *) die "unknown argument: $1" ;;
    esac
done

[[ -z "$MODE" ]] && die "specify --pack, --install, or --uninstall"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

cmd_pack() {
    hdr "pack"

    local ts; ts=$(date +%Y%m%d_%H%M%S)
    local out="dga-detector.tar.gz"

    local found=()
    for f in "${PACK_FILES[@]}"; do
        local p="$SCRIPT_DIR/$f"

        local pt="${p%/}"
        if [[ -e "$pt" ]]; then
            found+=("$f")
            ok "$f"
        else
            warn "$f  (not found, skipping)"
        fi
    done

    [[ ${#found[@]} -eq 0 ]] && die "nothing to pack"

    tar -czf "$SCRIPT_DIR/dga-detector.tar.gz" \
        --transform "s|^|dga-detector/|" \
        -C "$SCRIPT_DIR" "${found[@]}"
    ok "created: $SCRIPT_DIR/$out  ($(du -sh "$SCRIPT_DIR/$out" | cut -f1))"
}

gen_cert() {
    local cert_dir="$1"
    local days="${2:-3650}"
    local cn; cn="${3:-$(hostname -f 2>/dev/null || hostname)}"
    local key="$cert_dir/server.key"
    local crt="$cert_dir/server.crt"
    local san_cfg; san_cfg=$(mktemp)

    info "generating RSA-2048 cert  CN=$cn  days=$days"

    cat > "$san_cfg" <<EOF
[req]
distinguished_name = dn
x509_extensions    = v3_req
prompt             = no

[dn]
CN = $cn
O  = DGA Detector
OU = Security

[v3_req]
subjectAltName = @alt
basicConstraints = critical, CA:FALSE

[alt]
DNS.1 = localhost
DNS.2 = $cn
IP.1  = 127.0.0.1
EOF

    openssl req -x509 -newkey rsa:2048 -nodes \
        -keyout "$key" -out "$crt" \
        -days "$days" \
        -config "$san_cfg" \
        -extensions v3_req \
        2>/dev/null

    rm -f "$san_cfg"
    chmod 600 "$key"
    chmod 644 "$crt"
    ok "cert: $crt"
    ok "key:  $key"
}


cmd_install() {
    [[ $EUID -ne 0 ]] && die "root required: sudo bash install.sh --install"

    # port defaults
    if [[ -z "$PORT" ]]; then
        [[ "$USE_TLS" == true ]] && PORT=8443 || PORT=8000
    fi

    if [[ -z "$WORKERS" ]]; then
        WORKERS=$(nproc)
    fi

    hdr "preflight"

    command -v openssl   &>/dev/null || die "openssl not found"
    command -v systemctl &>/dev/null || die "systemd not found"

    local PYTHON; PYTHON=$(command -v python3 || true)
    [[ -z "$PYTHON" ]] && die "python3 not found"

    local pyver; pyver=$($PYTHON -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
    local pymaj pymin
    pymaj=$(echo "$pyver" | cut -d. -f1)
    pymin=$(echo "$pyver" | cut -d. -f2)
    [[ $pymaj -lt 3 || ($pymaj -eq 3 && $pymin -lt 10) ]] && \
        die "python 3.10+ required, found $pyver"
    ok "python $pyver"

    [[ ! -f "$SCRIPT_DIR/serve.py" ]]    && die "serve.py not found in $SCRIPT_DIR"
    [[ ! -f "$SCRIPT_DIR/features.py" ]] && die "features.py not found in $SCRIPT_DIR"
    [[ ! -f "$SCRIPT_DIR/model.pkl" ]]   && die "model.pkl not found in $SCRIPT_DIR"
    ok "source files present"

    info "install dir:  $INSTALL_DIR"
    info "user/group:   $SERVICE_USER:$SERVICE_GROUP"
    info "port:         $PORT  tls=$USE_TLS"
    info "workers:      $WORKERS"
    info "threshold:    $THRESHOLD"

    hdr "user / group"

    if ! getent group "$SERVICE_GROUP" &>/dev/null; then
        groupadd --system "$SERVICE_GROUP"
        ok "group created: $SERVICE_GROUP"
    else
        ok "group exists:  $SERVICE_GROUP"
    fi

    if ! id "$SERVICE_USER" &>/dev/null; then
        useradd --system --no-create-home --shell /usr/sbin/nologin \
            --gid "$SERVICE_GROUP" "$SERVICE_USER"
        ok "user created:  $SERVICE_USER"
    else
        ok "user exists:   $SERVICE_USER"
    fi

    hdr "files  →  $INSTALL_DIR"

    mkdir -p "$INSTALL_DIR/certs" "$INSTALL_DIR/data"

    local COPY_FILES=(serve.py features.py model.pkl requirements.txt train.py)
    for f in "${COPY_FILES[@]}"; do
        if [[ -f "$SCRIPT_DIR/$f" ]]; then
            cp "$SCRIPT_DIR/$f" "$INSTALL_DIR/$f"
            ok "$f"
        else
            warn "$f  (not found, skipped)"
        fi
    done

    chown -R "$SERVICE_USER:$SERVICE_GROUP" "$INSTALL_DIR"
    chmod 750 "$INSTALL_DIR"
    ok "ownership set"

    hdr "venv"

    local VENV="$INSTALL_DIR/.venv"
    if [[ ! -d "$VENV" ]]; then
        $PYTHON -m venv "$VENV"
        ok "created: $VENV"
    else
        ok "exists:  $VENV"
    fi

    info "installing dependencies..."
    "$VENV/bin/pip" install --quiet --upgrade pip
    "$VENV/bin/pip" install --quiet -r "$INSTALL_DIR/requirements.txt"
    ok "dependencies installed"

    "$VENV/bin/python3" -c "
import lightgbm, fastapi, joblib, numpy
from features import extract
f = extract('d80c31e97211.com')
assert f.is_hex_pattern == 1
" 2>/dev/null \
        && ok "import sanity check passed" \
        || warn "import sanity check failed — verify manually"

    hdr "tls"

    local TLS_ARGS="" PROTO="http"
    if [[ "$USE_TLS" == true ]]; then
        PROTO="https"
        if [[ -f "$INSTALL_DIR/certs/server.crt" ]]; then
            local expiry; expiry=$(openssl x509 -enddate -noout \
                -in "$INSTALL_DIR/certs/server.crt" 2>/dev/null | cut -d= -f2 || echo "unknown")
            ok "cert exists  (expires: $expiry)"
        else
            gen_cert "$INSTALL_DIR/certs" 3650
            chown "$SERVICE_USER:$SERVICE_GROUP" \
                "$INSTALL_DIR/certs/server.key" \
                "$INSTALL_DIR/certs/server.crt"
        fi
        TLS_ARGS="--keyfile $INSTALL_DIR/certs/server.key --certfile $INSTALL_DIR/certs/server.crt"
    else
        info "tls disabled"
    fi

    hdr "systemd"

    local SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

    cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=DGA Detector
After=network.target
Wants=network-online.target

[Service]
Type=notify
User=$SERVICE_USER
Group=$SERVICE_GROUP
WorkingDirectory=$INSTALL_DIR

Environment="MODEL_PATH=$INSTALL_DIR/model.pkl"
Environment="DGA_THRESHOLD=$THRESHOLD"
Environment="LOG_LEVEL=INFO"

ExecStart=$VENV/bin/gunicorn serve:app \
    --worker-class uvicorn.workers.UvicornWorker \
    --workers $WORKERS \
    --bind 0.0.0.0:$PORT \
    $TLS_ARGS \
    --timeout 30 \
    --graceful-timeout 10 \
    --keep-alive 5 \
    --log-level warning \
    --access-logfile - \
    --error-logfile -

Restart=on-failure
RestartSec=5s
StartLimitIntervalSec=60s
StartLimitBurst=3

LimitNOFILE=65536
MemoryMax=4G
CPUQuota=90%

NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ReadWritePaths=$INSTALL_DIR
ProtectHome=true

[Install]
WantedBy=multi-user.target
EOF

    ok "unit: $SERVICE_FILE"
    systemctl daemon-reload

    if systemctl is-active --quiet "$SERVICE_NAME"; then
        info "restarting existing service..."
        systemctl restart "$SERVICE_NAME"
    else
        systemctl enable --quiet "$SERVICE_NAME"
        systemctl start  "$SERVICE_NAME"
    fi
    ok "service started"

    hdr "health check"

    local HEALTH_URL="${PROTO}://127.0.0.1:${PORT}/health"
    local MAX_WAIT=60 WAITED=0 STATUS=""

    while [[ $WAITED -lt $MAX_WAIT ]]; do
        STATUS=$(curl -sk --max-time 3 "$HEALTH_URL" 2>/dev/null \
            | python3 -c "import sys,json; print(json.load(sys.stdin).get('status',''))" \
            2>/dev/null || true)
        [[ "$STATUS" == "ok" ]] && break
        sleep 2; WAITED=$((WAITED + 2))
        printf "  waiting %ds...\r" "$WAITED"
    done
    echo

    if [[ "$STATUS" == "ok" ]]; then
        ok "/health → ok"
    else
        warn "/health did not respond within ${MAX_WAIT}s"
        info "check logs: journalctl -u $SERVICE_NAME -n 40"
    fi

    local RESP
    RESP=$(curl -sk --max-time 5 -X POST \
        "${PROTO}://127.0.0.1:${PORT}/predict" \
        -H "Content-Type: application/json" \
        -d '[{"object":"d80c31e97211.com"},{"object":"google.com"}]' \
        2>/dev/null || true)

    if echo "$RESP" | grep -q '"detected"' 2>/dev/null; then
        ok "predict endpoint: d80c31e97211.com → detected"
    else
        warn "predict endpoint test failed: ${RESP:0:120}"
    fi

    echo
    echo -e "${B}done.${N}"
    echo
    printf "  dir        %s\n"  "$INSTALL_DIR"
    printf "  user:group %s:%s\n" "$SERVICE_USER" "$SERVICE_GROUP"
    printf "  port       %s (%s)\n" "$PORT" "$PROTO"
    printf "  workers    %s\n"  "$WORKERS"
    printf "  threshold  %s\n"  "$THRESHOLD"
    echo
    printf "  systemctl {status,restart,stop} %s\n" "$SERVICE_NAME"
    printf "  journalctl -u %s -f\n" "$SERVICE_NAME"
    echo
    printf "  curl -sk -X POST %s://localhost:%s/predict \\\\\n" "$PROTO" "$PORT"
    printf "    -H 'Content-Type: application/json' \\\\\n"
    printf "    -d '[{\"object\":\"test.com\"}]' | jq\n"
    echo
}

cmd_uninstall() {
    [[ $EUID -ne 0 ]] && die "root required: sudo bash install.sh --uninstall"

    hdr "uninstall"

    if systemctl is-active --quiet "$SERVICE_NAME" 2>/dev/null; then
        systemctl stop "$SERVICE_NAME"
        ok "service stopped"
    fi

    if systemctl is-enabled --quiet "$SERVICE_NAME" 2>/dev/null; then
        systemctl disable "$SERVICE_NAME"
        ok "service disabled"
    fi

    local unit="/etc/systemd/system/${SERVICE_NAME}.service"
    if [[ -f "$unit" ]]; then
        rm -f "$unit"
        systemctl daemon-reload
        ok "unit file removed"
    fi

    if [[ -d "$INSTALL_DIR" ]]; then
        rm -rf "$INSTALL_DIR"
        ok "removed: $INSTALL_DIR"
    else
        warn "$INSTALL_DIR not found"
    fi

    if id "$SERVICE_USER" &>/dev/null; then
        userdel "$SERVICE_USER"
        ok "user removed: $SERVICE_USER"
    fi

    if getent group "$SERVICE_GROUP" &>/dev/null; then
        groupdel "$SERVICE_GROUP" 2>/dev/null || true
        ok "group removed: $SERVICE_GROUP"
    fi

    ok "uninstall complete"
}

case "$MODE" in
    pack)      cmd_pack      ;;
    install)   cmd_install   ;;
    uninstall) cmd_uninstall ;;
esac