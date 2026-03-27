#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════
#  deploy/setup_vps.sh
#  Setup completo del VPS desde cero (Ubuntu 22.04 LTS)
#
#  Ejecutar UNA SOLA VEZ como root tras crear el VPS:
#    ssh root@TU_IP_VPS
#    curl -fsSL https://raw.githubusercontent.com/TU_USUARIO/smc-bot/main/deploy/setup_vps.sh | bash
#
#  O subir el archivo y ejecutarlo:
#    scp deploy/setup_vps.sh root@TU_IP:/root/
#    ssh root@TU_IP "bash /root/setup_vps.sh"
# ══════════════════════════════════════════════════════════════

set -euo pipefail   # Salir si cualquier comando falla
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
log()  { echo -e "${GREEN}[SETUP]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC}  $1"; }
err()  { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }

# ── Verificar que es Ubuntu ────────────────────────────────
[[ -f /etc/os-release ]] && source /etc/os-release
[[ "${ID:-}" != "ubuntu" ]] && warn "Script probado en Ubuntu 22.04. Continuar bajo tu responsabilidad."

log "=== FASE 1: Sistema base ==="

# Actualizar sistema
apt-get update -qq && apt-get upgrade -y -qq
apt-get install -y -qq \
    curl wget git unzip ufw fail2ban \
    htop tmux vim nano \
    ca-certificates gnupg lsb-release


log "=== FASE 2: Crear usuario no-root ==="

BOT_USER="botuser"
if ! id "$BOT_USER" &>/dev/null; then
    useradd -m -s /bin/bash "$BOT_USER"
    usermod -aG sudo "$BOT_USER"
    log "Usuario '$BOT_USER' creado."
else
    log "Usuario '$BOT_USER' ya existe."
fi

# Configurar sudo sin contraseña solo para docker
echo "$BOT_USER ALL=(ALL) NOPASSWD: /usr/bin/docker, /usr/bin/docker compose" \
    > /etc/sudoers.d/botuser-docker
chmod 440 /etc/sudoers.d/botuser-docker


log "=== FASE 3: Firewall (UFW) ==="

ufw --force reset
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp comment "SSH"
# Si querés exponer algún puerto adicional (ej. dashboard web), descomenta:
# ufw allow 8080/tcp comment "Dashboard"
ufw --force enable
log "Firewall configurado. Solo SSH abierto."


log "=== FASE 4: Fail2ban (protección SSH) ==="

systemctl enable fail2ban
systemctl start fail2ban
log "Fail2ban activo."


log "=== FASE 5: Instalar Docker ==="

if command -v docker &>/dev/null; then
    log "Docker ya instalado: $(docker --version)"
else
    # Instalar Docker oficial (no el del apt por defecto que suele estar desactualizado)
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
        | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    chmod a+r /etc/apt/keyrings/docker.gpg

    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
        https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" \
        > /etc/apt/sources.list.d/docker.list

    apt-get update -qq
    apt-get install -y -qq \
        docker-ce docker-ce-cli containerd.io \
        docker-buildx-plugin docker-compose-plugin

    systemctl enable docker
    systemctl start docker
    log "Docker instalado: $(docker --version)"
fi

# Agregar botuser al grupo docker
usermod -aG docker "$BOT_USER"


log "=== FASE 6: Clonar repositorio ==="

BOT_DIR="/home/$BOT_USER/smc-bot"

if [[ -d "$BOT_DIR" ]]; then
    warn "Directorio $BOT_DIR ya existe. Haciendo git pull..."
    cd "$BOT_DIR" && git pull
else
    # Reemplazar con tu URL de GitHub
    REPO_URL="${REPO_URL:-https://github.com/TU_USUARIO/smc-bot.git}"
    if [[ "$REPO_URL" == *"TU_USUARIO"* ]]; then
        warn "REPO_URL no configurado. Clonando repo de ejemplo."
        warn "Editá la variable REPO_URL en este script con tu repo real."
        mkdir -p "$BOT_DIR"
    else
        git clone "$REPO_URL" "$BOT_DIR"
        log "Repositorio clonado en $BOT_DIR"
    fi
fi

chown -R "$BOT_USER:$BOT_USER" "/home/$BOT_USER"


log "=== FASE 7: Configurar credenciales ==="

ENV_FILE="$BOT_DIR/.env"
if [[ ! -f "$ENV_FILE" ]]; then
    if [[ -f "$BOT_DIR/.env.example" ]]; then
        cp "$BOT_DIR/.env.example" "$ENV_FILE"
        chmod 600 "$ENV_FILE"    # Solo el propietario puede leerlo
        warn "Archivo .env creado desde .env.example"
        warn "IMPORTANTE: Editá $ENV_FILE con tus credenciales antes de iniciar el bot:"
        warn "  nano $ENV_FILE"
    else
        warn "No se encontró .env.example. Creá $ENV_FILE manualmente."
    fi
else
    log ".env ya existe."
fi


log "=== FASE 8: Configurar servicio systemd (para que Docker arranque al boot) ==="

# Docker ya arranca con systemd, pero aseguramos que compose también
cat > /etc/systemd/system/smc-bot.service << SERVICE
[Unit]
Description=SMC Futures Bot (Docker Compose)
Requires=docker.service
After=docker.service network-online.target
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=$BOT_DIR
ExecStart=/usr/bin/docker compose up -d --build
ExecStop=/usr/bin/docker compose down
ExecReload=/usr/bin/docker compose pull && /usr/bin/docker compose up -d
User=$BOT_USER
Group=$BOT_USER
TimeoutStartSec=300

[Install]
WantedBy=multi-user.target
SERVICE

systemctl daemon-reload
systemctl enable smc-bot.service
log "Servicio smc-bot.service habilitado (arranca al boot)."


log "=== FASE 9: Script de actualización ==="

cat > "/home/$BOT_USER/update_bot.sh" << 'UPDATESH'
#!/usr/bin/env bash
# Actualiza el bot desde Git y reinicia el contenedor
set -e
GREEN='\033[0;32m'; NC='\033[0m'
log() { echo -e "${GREEN}[UPDATE]${NC} $1"; }

BOT_DIR="$(dirname "$(readlink -f "$0")")/smc-bot"
cd "$BOT_DIR"

log "Pulling latest code..."
git pull origin main

log "Rebuilding Docker image..."
docker compose build --no-cache

log "Restarting bot..."
docker compose down
docker compose up -d

log "Update complete!"
docker compose logs --tail=20 smc-bot
UPDATESH

chmod +x "/home/$BOT_USER/update_bot.sh"
chown "$BOT_USER:$BOT_USER" "/home/$BOT_USER/update_bot.sh"


log "=== FASE 10: Configurar rotación de logs del sistema ==="

cat > /etc/logrotate.d/smc-bot << LOGROTATE
$BOT_DIR/logs/*.log {
    daily
    rotate 30
    compress
    delaycompress
    missingok
    notifempty
    copytruncate
}
LOGROTATE


log "=== SETUP COMPLETO ==="
echo ""
echo -e "${GREEN}══════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  VPS listo para el SMC Bot${NC}"
echo -e "${GREEN}══════════════════════════════════════════════════════${NC}"
echo ""
echo "  Próximos pasos:"
echo ""
echo "  1. Configurar credenciales:"
echo "     nano $ENV_FILE"
echo "     (Completar BINANCE_API_KEY, BINANCE_API_SECRET, etc.)"
echo ""
echo "  2. Iniciar el bot:"
echo "     cd $BOT_DIR"
echo "     docker compose up -d --build"
echo ""
echo "  3. Ver logs en tiempo real:"
echo "     docker compose logs -f smc-bot"
echo ""
echo "  4. Para actualizar el bot desde Git:"
echo "     ~/update_bot.sh"
echo ""
echo -e "${YELLOW}  IMPORTANTE: Editar .env antes de iniciar.${NC}"
echo ""
