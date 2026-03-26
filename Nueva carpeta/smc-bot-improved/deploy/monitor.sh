#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════
#  deploy/monitor.sh
#  Monitoreo del bot: verifica que el contenedor siga corriendo
#  y envía alerta Telegram si está caído.
#
#  Instalación como cron (ejecutar cada 5 minutos):
#    crontab -e
#    */5 * * * * /home/botuser/smc-bot/deploy/monitor.sh >> /home/botuser/smc-bot/logs/monitor.log 2>&1
# ══════════════════════════════════════════════════════════════

set -euo pipefail

# ── Configuración ──────────────────────────────────────────────
BOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
CONTAINER="smc-bot"
LOG_FILE="$BOT_DIR/logs/monitor.log"
STATE_FILE="/tmp/smc-bot-monitor-state"   # Evita spam de alertas

# Cargar vars de entorno para Telegram
if [[ -f "$BOT_DIR/.env" ]]; then
    export $(grep -v '^#' "$BOT_DIR/.env" | grep -v '^$' | xargs)
fi

TELEGRAM_TOKEN="${TELEGRAM_TOKEN:-}"
TELEGRAM_CHAT_ID="${TELEGRAM_CHAT_ID:-}"

# ── Helpers ────────────────────────────────────────────────────
timestamp() { date '+%Y-%m-%d %H:%M:%S UTC'; }

telegram_alert() {
    local msg="$1"
    [[ -z "$TELEGRAM_TOKEN" || -z "$TELEGRAM_CHAT_ID" ]] && return 0
    curl -s -X POST \
        "https://api.telegram.org/bot${TELEGRAM_TOKEN}/sendMessage" \
        -d "chat_id=${TELEGRAM_CHAT_ID}" \
        -d "text=${msg}" \
        -d "parse_mode=Markdown" \
        --max-time 10 > /dev/null 2>&1 || true
}

log() { echo "[$(timestamp)] $1" | tee -a "$LOG_FILE"; }

# ── Verificar contenedor ───────────────────────────────────────
CONTAINER_STATUS=$(docker inspect --format='{{.State.Status}}' "$CONTAINER" 2>/dev/null || echo "missing")
HEALTH_STATUS=$(docker inspect --format='{{.State.Health.Status}}' "$CONTAINER" 2>/dev/null || echo "unknown")

case "$CONTAINER_STATUS" in
    "running")
        # Contenedor corriendo — verificar si era unhealthy antes
        if [[ -f "$STATE_FILE" ]]; then
            prev_state=$(cat "$STATE_FILE")
            if [[ "$prev_state" == "down" ]]; then
                log "Bot recuperado. Estado: running / Health: $HEALTH_STATUS"
                telegram_alert "✅ *SMC Bot recuperado*%0AContenedor volvió a estar corriendo.%0A$(timestamp)"
            fi
        fi
        # Registrar estado bueno
        echo "up" > "$STATE_FILE"
        log "OK | Status: $CONTAINER_STATUS | Health: $HEALTH_STATUS"

        # Verificar uso de memoria
        MEM_USAGE=$(docker stats "$CONTAINER" --no-stream --format "{{.MemPerc}}" 2>/dev/null | tr -d '%' || echo "0")
        if (( $(echo "$MEM_USAGE > 85" | bc -l 2>/dev/null || echo 0) )); then
            log "WARN: Uso de memoria alto: ${MEM_USAGE}%"
            telegram_alert "⚠️ *SMC Bot — Memoria alta*%0AUso: ${MEM_USAGE}%%0A$(timestamp)"
        fi
        ;;

    "exited"|"dead"|"missing")
        prev_state=$(cat "$STATE_FILE" 2>/dev/null || echo "unknown")

        # Solo alertar si es la primera vez que lo detectamos caído
        # (evita spam de alertas cada 5 minutos)
        if [[ "$prev_state" != "down" ]]; then
            log "ERROR: Contenedor caído. Status: $CONTAINER_STATUS"
            telegram_alert "🚨 *SMC Bot CAÍDO*%0AStatus: $CONTAINER_STATUS%0AIntentando reiniciar...%0A$(timestamp)"
            echo "down" > "$STATE_FILE"
        fi

        # Intentar reiniciar automáticamente
        log "Intentando reiniciar el contenedor..."
        cd "$BOT_DIR"
        if docker compose up -d --no-recreate 2>/dev/null; then
            log "Contenedor reiniciado exitosamente."
            telegram_alert "🔄 *SMC Bot reiniciado*%0AEl monitor lo reinició automáticamente.%0A$(timestamp)"
            echo "up" > "$STATE_FILE"
        else
            log "FALLO al reiniciar el contenedor."
            telegram_alert "❌ *SMC Bot — FALLO de reinicio*%0AIntervención manual necesaria.%0A$(timestamp)"
        fi
        ;;

    "paused")
        log "WARN: Contenedor pausado. Reanudando..."
        docker unpause "$CONTAINER" 2>/dev/null || true
        ;;

    *)
        log "Estado desconocido: $CONTAINER_STATUS"
        ;;
esac

# ── Verificar espacio en disco ─────────────────────────────────
DISK_USAGE=$(df / --output=pcent | tail -1 | tr -d ' %')
if [[ "$DISK_USAGE" -gt 85 ]]; then
    log "WARN: Disco lleno al ${DISK_USAGE}%"
    telegram_alert "⚠️ *VPS — Disco casi lleno*%0AUso: ${DISK_USAGE}%%0A$(timestamp)"
fi
