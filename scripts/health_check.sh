#!/bin/bash
# ============================================================================
#  SMC & Scalping Bots — Health Check + Log Rotation
#  Monitorea 4 containers y rota logs cuando superan el límite
#
#  Uso:
#    chmod +x health_check.sh
#    ./health_check.sh           # corre una vez
#    ./health_check.sh --install # instala los crontabs automáticamente
#    ./health_check.sh --status  # muestra estado sin alertar
# ============================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# CONFIGURACIÓN
# ---------------------------------------------------------------------------

# Telegram
TELEGRAM_BOT_TOKEN="${TELEGRAM_BOT_TOKEN:-}"
TELEGRAM_CHAT_ID="${TELEGRAM_CHAT_ID:-}"

# Si no hay variables de entorno, intentar leer del .env del smc-bot
if [[ -z "$TELEGRAM_BOT_TOKEN" ]] && [[ -f "$HOME/smc-bot/.env" ]]; then
    TELEGRAM_BOT_TOKEN=$(grep -E '^TELEGRAM_BOT_TOKEN=' "$HOME/smc-bot/.env" | cut -d'=' -f2- | tr -d '"' | tr -d "'")
    TELEGRAM_CHAT_ID=$(grep -E '^TELEGRAM_CHAT_ID=' "$HOME/smc-bot/.env" | cut -d'=' -f2- | tr -d '"' | tr -d "'")
fi

# Límite de tamaño de log (1 GB en bytes)
LOG_MAX_BYTES=$((1 * 1024 * 1024 * 1024))

# Archivo de estado para evitar spam de alertas
STATE_DIR="$HOME/.bot-health"
mkdir -p "$STATE_DIR"

# Cooldown de alertas (en segundos) — no repite la misma alerta por 30 min
ALERT_COOLDOWN=1800

# Log del propio health check
HC_LOG="$STATE_DIR/health_check.log"

# ---------------------------------------------------------------------------
# DEFINICIÓN DE BOTS
# ---------------------------------------------------------------------------
# Formato: "nombre_display|container_name|compose_dir|log_file"

BOTS=(
    "SMC Conservador|smc-bot|$HOME/smc-bot|$HOME/smc-bot/logs/smc_bot.log"
    "SMC Agresivo|smc-bot-2|$HOME/smc-bot|$HOME/smc-bot/logs/smc_bot_2.log"
    "Scalping BTC|scalping-btc|$HOME/scalping-bot-v3|$HOME/scalping-bot-v3/logs/scalping_btc.log"
    "Scalping ETH|scalping-eth|$HOME/scalping-bot-v3|$HOME/scalping-bot-v3/logs/scalping_eth.log"
)

# ---------------------------------------------------------------------------
# FUNCIONES
# ---------------------------------------------------------------------------

timestamp() {
    date '+%Y-%m-%d %H:%M:%S'
}

log_msg() {
    echo "[$(timestamp)] $1" | tee -a "$HC_LOG"
}

# Enviar mensaje a Telegram
send_telegram() {
    local message="$1"
    if [[ -n "$TELEGRAM_BOT_TOKEN" ]] && [[ -n "$TELEGRAM_CHAT_ID" ]]; then
        curl -s -X POST \
            "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
            -d chat_id="$TELEGRAM_CHAT_ID" \
            -d text="$message" \
            -d parse_mode="HTML" \
            --max-time 10 > /dev/null 2>&1 || true
    fi
}

# Verificar si ya alertamos recientemente sobre este bot
should_alert() {
    local bot_id="$1"
    local state_file="$STATE_DIR/alert_${bot_id}"

    if [[ -f "$state_file" ]]; then
        local last_alert
        last_alert=$(cat "$state_file")
        local now
        now=$(date +%s)
        local diff=$((now - last_alert))
        if [[ $diff -lt $ALERT_COOLDOWN ]]; then
            return 1  # no alertar, todavía en cooldown
        fi
    fi
    return 0  # sí alertar
}

# Marcar que alertamos sobre este bot
mark_alerted() {
    local bot_id="$1"
    date +%s > "$STATE_DIR/alert_${bot_id}"
}

# Limpiar estado de alerta cuando el bot vuelve a estar OK
clear_alert() {
    local bot_id="$1"
    local state_file="$STATE_DIR/alert_${bot_id}"
    if [[ -f "$state_file" ]]; then
        rm -f "$state_file"
        return 0  # estaba caído, ahora se recuperó
    fi
    return 1  # no estaba caído
}

# Chequear si un container está corriendo
check_container() {
    local container_name="$1"
    local status
    status=$(docker inspect -f '{{.State.Status}}' "$container_name" 2>/dev/null || echo "not_found")
    echo "$status"
}

# Obtener uptime del container
get_uptime() {
    local container_name="$1"
    local started_at
    started_at=$(docker inspect -f '{{.State.StartedAt}}' "$container_name" 2>/dev/null || echo "")
    if [[ -n "$started_at" ]] && [[ "$started_at" != "" ]]; then
        local start_epoch
        start_epoch=$(date -d "$started_at" +%s 2>/dev/null || echo "0")
        local now_epoch
        now_epoch=$(date +%s)
        local diff=$((now_epoch - start_epoch))
        local days=$((diff / 86400))
        local hours=$(( (diff % 86400) / 3600 ))
        local mins=$(( (diff % 3600) / 60 ))
        echo "${days}d ${hours}h ${mins}m"
    else
        echo "desconocido"
    fi
}

# Chequear actividad reciente en el log
check_log_activity() {
    local log_file="$1"
    local max_age_minutes="${2:-30}"

    if [[ ! -f "$log_file" ]]; then
        echo "no_log"
        return
    fi

    local file_mod
    file_mod=$(stat -c %Y "$log_file" 2>/dev/null || echo "0")
    local now
    now=$(date +%s)
    local diff=$(( (now - file_mod) / 60 ))

    if [[ $diff -gt $max_age_minutes ]]; then
        echo "stale_${diff}m"
    else
        echo "active"
    fi
}

# Rotar log si supera el límite
rotate_log() {
    local log_file="$1"
    local display_name="$2"

    if [[ ! -f "$log_file" ]]; then
        return
    fi

    local file_size
    file_size=$(stat -c %s "$log_file" 2>/dev/null || echo "0")

    if [[ $file_size -gt $LOG_MAX_BYTES ]]; then
        local size_mb=$((file_size / 1024 / 1024))
        local backup="${log_file}.old"

        # Mantener solo 1 backup — eliminar el anterior
        rm -f "$backup"
        mv "$log_file" "$backup"
        touch "$log_file"

        log_msg "ROTADO: $display_name — ${size_mb}MB → backup en ${backup}"

        local msg="🔄 <b>Log Rotado</b>
├ Bot: <code>${display_name}</code>
├ Tamaño: ${size_mb} MB (límite: $((LOG_MAX_BYTES / 1024 / 1024)) MB)
└ Backup: <code>$(basename "$backup")</code>"
        send_telegram "$msg"
    fi
}

# Formatear tamaño de archivo
format_size() {
    local bytes="$1"
    if [[ $bytes -gt $((1024 * 1024 * 1024)) ]]; then
        echo "$((bytes / 1024 / 1024 / 1024))GB"
    elif [[ $bytes -gt $((1024 * 1024)) ]]; then
        echo "$((bytes / 1024 / 1024))MB"
    elif [[ $bytes -gt 1024 ]]; then
        echo "$((bytes / 1024))KB"
    else
        echo "${bytes}B"
    fi
}

# ---------------------------------------------------------------------------
# INSTALAR CRONTABS
# ---------------------------------------------------------------------------
install_crons() {
    local script_path
    script_path=$(realpath "$0")

    # Obtener crontab actual (sin nuestras entradas previas)
    local current_cron
    current_cron=$(crontab -l 2>/dev/null | grep -v "health_check.sh" | grep -v "# Bot Health Check" || true)

    local new_cron="$current_cron
# Bot Health Check — SMC cada 15 min, Scalping cada 1 hora
*/15 * * * * $script_path --check-smc >> $HC_LOG 2>&1
0 * * * * $script_path --check-scalping >> $HC_LOG 2>&1
# Log rotation — cada 6 horas para todos
0 */6 * * * $script_path --rotate >> $HC_LOG 2>&1"

    echo "$new_cron" | crontab -

    echo "✅ Crontabs instalados:"
    echo ""
    echo "  • SMC (conservador + agresivo):  cada 15 minutos"
    echo "  • Scalping (BTC + ETH):          cada 1 hora"
    echo "  • Rotación de logs:              cada 6 horas"
    echo ""
    echo "Verificar con: crontab -l"
    echo "Logs del health check: $HC_LOG"
}

# ---------------------------------------------------------------------------
# CHECK: containers específicos
# ---------------------------------------------------------------------------
run_check() {
    local filter="$1"  # "all", "smc", "scalping"
    local silent="${2:-false}"
    local issues=()
    local recoveries=()
    local status_lines=()

    for bot_entry in "${BOTS[@]}"; do
        IFS='|' read -r display_name container_name compose_dir log_file <<< "$bot_entry"

        # Filtrar según grupo
        case "$filter" in
            smc)      [[ "$container_name" != smc-bot* ]] && continue ;;
            scalping) [[ "$container_name" != scalping* ]] && continue ;;
        esac

        # Sanitizar nombre para archivo de estado
        local bot_id
        bot_id=$(echo "$container_name" | tr -c 'a-zA-Z0-9' '_')

        # 1. Chequear container
        local status
        status=$(check_container "$container_name")

        if [[ "$status" == "running" ]]; then
            local uptime
            uptime=$(get_uptime "$container_name")

            # Chequear si se recuperó de una caída anterior
            if clear_alert "$bot_id" 2>/dev/null; then
                recoveries+=("$display_name")
            fi

            # 2. Chequear actividad del log
            local log_status
            local max_age=30
            [[ "$container_name" == scalping* ]] && max_age=90
            log_status=$(check_log_activity "$log_file" "$max_age")

            local log_size="0"
            [[ -f "$log_file" ]] && log_size=$(stat -c %s "$log_file" 2>/dev/null || echo "0")
            local log_size_fmt
            log_size_fmt=$(format_size "$log_size")

            if [[ "$log_status" == stale_* ]]; then
                local stale_mins="${log_status#stale_}"
                stale_mins="${stale_mins%m}"
                status_lines+=("⚠️  $display_name — running (${uptime}) pero log inactivo hace ${stale_mins}m | log: ${log_size_fmt}")

                if should_alert "$bot_id"; then
                    issues+=("⚠️ <b>${display_name}</b>: container running pero log inactivo hace ${stale_mins} min")
                    mark_alerted "$bot_id"
                fi
            elif [[ "$log_status" == "no_log" ]]; then
                status_lines+=("⚠️  $display_name — running (${uptime}) pero no se encontró archivo de log")
            else
                status_lines+=("✅ $display_name — running (${uptime}) | log: ${log_size_fmt}")
            fi
        else
            status_lines+=("❌ $display_name — $status")

            if should_alert "$bot_id"; then
                issues+=("❌ <b>${display_name}</b>: container <code>${status}</code>")
                mark_alerted "$bot_id"
            fi
        fi
    done

    # Mostrar estado local
    echo ""
    echo "═══════════════════════════════════════════"
    echo "  Bot Health Check — $(timestamp)"
    echo "  Grupo: $filter"
    echo "═══════════════════════════════════════════"
    for line in "${status_lines[@]}"; do
        echo "  $line"
    done
    echo "═══════════════════════════════════════════"
    echo ""

    # Alertar recuperaciones por Telegram
    if [[ ${#recoveries[@]} -gt 0 ]] && [[ "$silent" == "false" ]]; then
        local rec_msg="✅ <b>Bot Recuperado</b>"
        for rec in "${recoveries[@]}"; do
            rec_msg+="\n└ ${rec} volvió a estar online"
        done
        send_telegram "$rec_msg"
        log_msg "RECUPERADO: ${recoveries[*]}"
    fi

    # Alertar problemas por Telegram + log local
    if [[ ${#issues[@]} -gt 0 ]] && [[ "$silent" == "false" ]]; then
        local alert_msg="🚨 <b>ALERTA — Bot Health Check</b>
🕐 $(timestamp)"
        for issue in "${issues[@]}"; do
            alert_msg+="\n${issue}"
        done
        alert_msg+="\n\n🔧 Conectate al VPS para revisar"

        send_telegram "$alert_msg"
        log_msg "ALERTA: ${issues[*]}"
    fi

    if [[ ${#issues[@]} -eq 0 ]]; then
        log_msg "OK [$filter]: todos los containers corriendo"
    fi
}

# ---------------------------------------------------------------------------
# ROTATE: logs de todos los bots
# ---------------------------------------------------------------------------
run_rotate() {
    log_msg "Iniciando rotación de logs..."

    for bot_entry in "${BOTS[@]}"; do
        IFS='|' read -r display_name container_name compose_dir log_file <<< "$bot_entry"
        rotate_log "$log_file" "$display_name"
    done

    # También rotar el log del propio health check
    if [[ -f "$HC_LOG" ]]; then
        local hc_size
        hc_size=$(stat -c %s "$HC_LOG" 2>/dev/null || echo "0")
        if [[ $hc_size -gt $((50 * 1024 * 1024)) ]]; then  # 50MB para el HC log
            rm -f "${HC_LOG}.old"
            mv "$HC_LOG" "${HC_LOG}.old"
            touch "$HC_LOG"
            log_msg "Rotado log del health check ($(format_size "$hc_size"))"
        fi
    fi

    log_msg "Rotación completada"
}

# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------
case "${1:-}" in
    --install)
        install_crons
        ;;
    --check-smc)
        run_check "smc"
        ;;
    --check-scalping)
        run_check "scalping"
        ;;
    --rotate)
        run_rotate
        ;;
    --status)
        run_check "all" "true"
        ;;
    --help|-h)
        echo "Uso: $(basename "$0") [opción]"
        echo ""
        echo "Opciones:"
        echo "  (sin args)       Chequea todos los bots + rota logs"
        echo "  --install        Instala crontabs automáticos"
        echo "  --check-smc      Chequea solo SMC (conservador + agresivo)"
        echo "  --check-scalping Chequea solo Scalping (BTC + ETH)"
        echo "  --rotate         Rota logs que superen 1GB"
        echo "  --status         Muestra estado sin enviar alertas"
        echo "  --help           Muestra esta ayuda"
        ;;
    *)
        run_check "all"
        run_rotate
        ;;
esac
