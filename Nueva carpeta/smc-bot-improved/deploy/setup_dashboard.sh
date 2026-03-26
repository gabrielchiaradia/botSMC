#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════
#  deploy/setup_dashboard.sh
#  Configura el dashboard web en el VPS:
#    1. Crea el archivo .htpasswd con usuario/contraseña
#    2. Abre el puerto 8080 en el firewall
#    3. Arranca el contenedor nginx
#
#  Ejecutar UNA VEZ después de setup_vps.sh:
#    bash ~/smc-bot/deploy/setup_dashboard.sh
# ══════════════════════════════════════════════════════════════

set -euo pipefail
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
log()  { echo -e "${GREEN}[DASHBOARD]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }

BOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
HTPASSWD_FILE="$BOT_DIR/dashboard/.htpasswd"

log "=== Setup Dashboard Web ==="

# ── 1. Instalar apache2-utils para htpasswd ────────────────
if ! command -v htpasswd &>/dev/null; then
    log "Instalando apache2-utils..."
    sudo apt-get install -y -qq apache2-utils
fi

# ── 2. Crear usuario/contraseña ────────────────────────────
echo ""
echo "  Elegí un usuario y contraseña para proteger el dashboard."
echo "  Vas a necesitarlos cada vez que abras http://TU_IP:8080"
echo ""

read -p "  Usuario [admin]: " DASH_USER
DASH_USER="${DASH_USER:-admin}"

# Pedir contraseña sin que se vea en pantalla
while true; do
    read -s -p "  Contraseña: " DASH_PASS
    echo ""
    read -s -p "  Confirmar contraseña: " DASH_PASS2
    echo ""
    if [[ "$DASH_PASS" == "$DASH_PASS2" ]]; then
        break
    fi
    echo "  Las contraseñas no coinciden. Intentá de nuevo."
done

# Crear/actualizar .htpasswd
htpasswd -cb "$HTPASSWD_FILE" "$DASH_USER" "$DASH_PASS"
chmod 640 "$HTPASSWD_FILE"
log "Archivo .htpasswd creado para usuario '$DASH_USER'"

# ── 3. Abrir puerto 8080 en el firewall ────────────────────
log "Abriendo puerto 8080 en UFW..."
sudo ufw allow 8080/tcp comment "SMC Dashboard"
sudo ufw reload

# ── 4. Arrancar (o reiniciar) el contenedor nginx ──────────
log "Iniciando contenedor del dashboard..."
cd "$BOT_DIR"
docker compose up -d dashboard

# ── 5. Verificar ──────────────────────────────────────────
sleep 3
STATUS=$(docker inspect --format='{{.State.Status}}' smc-dashboard 2>/dev/null || echo "missing")

if [[ "$STATUS" == "running" ]]; then
    # Obtener IP pública del VPS
    VPS_IP=$(curl -s --max-time 5 ifconfig.me 2>/dev/null || echo "TU_IP_VPS")
    log "Dashboard corriendo!"
    echo ""
    echo -e "${GREEN}══════════════════════════════════════════${NC}"
    echo -e "${GREEN}  Dashboard accesible en:${NC}"
    echo -e "${GREEN}  http://${VPS_IP}:8080${NC}"
    echo -e "${GREEN}  Usuario: ${DASH_USER}${NC}"
    echo -e "${GREEN}══════════════════════════════════════════${NC}"
    echo ""
    echo "  Los archivos del bot se actualizan automáticamente."
    echo "  El dashboard hace refresh cada 60 segundos."
    echo ""
else
    warn "El contenedor no arrancó correctamente. Revisá los logs:"
    warn "  docker compose logs dashboard"
fi
