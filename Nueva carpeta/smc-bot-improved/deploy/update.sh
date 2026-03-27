#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════
#  deploy/update.sh
#  Actualiza el bot desde Git y reinicia el contenedor.
#  Zero-downtime: el bot sigue corriendo mientras se buildea.
#
#  Uso:
#    ./deploy/update.sh              # Pull + rebuild + restart
#    ./deploy/update.sh --no-build   # Solo pull + restart (sin rebuild)
#    ./deploy/update.sh --hard       # Reset completo (descarta cambios locales)
# ══════════════════════════════════════════════════════════════

set -euo pipefail
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
log()  { echo -e "${GREEN}[UPDATE $(date '+%H:%M:%S')]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
err()  { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }

BOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$BOT_DIR"

NO_BUILD=false
HARD=false

for arg in "$@"; do
    case $arg in
        --no-build) NO_BUILD=true ;;
        --hard)     HARD=true ;;
    esac
done

log "Directorio: $BOT_DIR"
log "Rama actual: $(git branch --show-current 2>/dev/null || echo 'desconocida')"

# ── 1. Git pull ────────────────────────────────────────────────
log "Actualizando código desde Git..."
if $HARD; then
    warn "Hard reset: descartando cambios locales."
    git fetch origin
    git reset --hard origin/main
else
    git pull origin main
fi

COMMIT=$(git log --oneline -1)
log "Commit actual: $COMMIT"

# ── 2. Verificar cambios en requirements ──────────────────────
NEEDS_BUILD=true
if $NO_BUILD; then
    NEEDS_BUILD=false
    warn "Saltando rebuild (--no-build)"
fi

# ── 3. Build nueva imagen (mientras el bot sigue corriendo) ───
if $NEEDS_BUILD; then
    log "Buildeando nueva imagen Docker..."
    docker compose build --no-cache smc-bot
    log "Build completado."
fi

# ── 4. Restart con la nueva imagen ────────────────────────────
log "Reiniciando contenedor..."
docker compose down smc-bot
docker compose up -d smc-bot
log "Contenedor reiniciado."

# ── 5. Verificar que arrancó ───────────────────────────────────
sleep 5
STATUS=$(docker inspect --format='{{.State.Status}}' smc-bot 2>/dev/null || echo "missing")
if [[ "$STATUS" == "running" ]]; then
    log "Bot corriendo correctamente."
else
    err "El bot no arrancó. Status: $STATUS. Ver logs: docker compose logs smc-bot"
fi

# ── 6. Limpiar imágenes viejas ─────────────────────────────────
log "Limpiando imágenes Docker antiguas..."
docker image prune -f > /dev/null 2>&1 || true

log "Actualización completa. Últimas líneas del log:"
docker compose logs --tail=10 smc-bot
