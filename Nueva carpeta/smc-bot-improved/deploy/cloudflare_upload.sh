#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════
#  deploy/cloudflare_upload.sh
#  Sube el dashboard + el JSON más reciente a Cloudflare Pages.
#  Genera una URL pública: https://smc-bot.TU_USUARIO.pages.dev
#
#  Setup inicial (una sola vez):
#    1. Ir a https://dash.cloudflare.com/profile/api-tokens
#    2. Crear token con permiso "Cloudflare Pages: Edit"
#    3. Ir a https://dash.cloudflare.com → Account ID (barra lateral)
#    4. Agregar al .env:
#         CF_API_TOKEN=tu_token
#         CF_ACCOUNT_ID=tu_account_id
#         CF_PROJECT_NAME=smc-bot       (nombre del proyecto Pages)
#
#  Uso:
#    bash deploy/cloudflare_upload.sh                    # Sube el JSON de hoy
#    bash deploy/cloudflare_upload.sh backtest_results   # Sube el backtest
#    bash deploy/cloudflare_upload.sh all                # Sube todos los JSONs recientes
#
#  Configurar en cron para subida automática cada hora:
#    0 * * * * /home/botuser/smc-bot/deploy/cloudflare_upload.sh >> /home/botuser/smc-bot/logs/cf_upload.log 2>&1
# ══════════════════════════════════════════════════════════════

set -euo pipefail
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
log()  { echo "[$(date '+%H:%M:%S')] $1"; }
ok()   { echo -e "${GREEN}[OK]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
err()  { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }

BOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

# ── Cargar variables de entorno ────────────────────────────
if [[ -f "$BOT_DIR/.env" ]]; then
    export $(grep -v '^#' "$BOT_DIR/.env" | grep -v '^$' | xargs 2>/dev/null) || true
fi

CF_API_TOKEN="${CF_API_TOKEN:-}"
CF_ACCOUNT_ID="${CF_ACCOUNT_ID:-}"
CF_PROJECT_NAME="${CF_PROJECT_NAME:-smc-bot}"
MODO="${1:-today}"   # today | backtest | all

# ── Validar credenciales ───────────────────────────────────
if [[ -z "$CF_API_TOKEN" || -z "$CF_ACCOUNT_ID" ]]; then
    err "Faltan CF_API_TOKEN y/o CF_ACCOUNT_ID en .env
    
    Setup:
      1. https://dash.cloudflare.com/profile/api-tokens → Crear token 'Cloudflare Pages: Edit'
      2. https://dash.cloudflare.com → Account ID (barra lateral derecha)
      3. Agregar al .env:
           CF_API_TOKEN=tu_token
           CF_ACCOUNT_ID=tu_account_id
           CF_PROJECT_NAME=smc-bot"
fi

# ── API de Cloudflare Pages ────────────────────────────────
CF_API="https://api.cloudflare.com/client/v4/accounts/${CF_ACCOUNT_ID}/pages/projects/${CF_PROJECT_NAME}/deployments"

# ── Crear directorio temporal para el deploy ──────────────
DEPLOY_DIR=$(mktemp -d)
trap "rm -rf $DEPLOY_DIR" EXIT

log "Preparando archivos para Cloudflare Pages..."

# Siempre incluir el HTML del dashboard
cp "$BOT_DIR/dashboard/index.html" "$DEPLOY_DIR/index.html"

# Determinar qué JSONs subir según el modo
case "$MODO" in
    "today"|"")
        # JSON del día de hoy
        FECHA=$(date -u '+%Y%m%d')
        JSON_FILE="$BOT_DIR/logs/dashboard_trades_${FECHA}.json"
        if [[ -f "$JSON_FILE" ]]; then
            # Cloudflare Pages sirve archivos estáticos
            # El dashboard los carga por nombre conocido → renombrar a data.json
            cp "$JSON_FILE" "$DEPLOY_DIR/data.json"
            # También copiar con nombre original por si se quiere cargar manualmente
            cp "$JSON_FILE" "$DEPLOY_DIR/dashboard_trades_${FECHA}.json"
            ok "JSON del día incluido: $(basename $JSON_FILE)"
        else
            warn "No hay JSON del día de hoy ($FECHA). El dashboard mostrará sin datos en vivo."
        fi
        ;;

    "backtest")
        # Resultados del backtest más reciente
        BT_FILE="$BOT_DIR/backtest/results/backtest_results.json"
        if [[ -f "$BT_FILE" ]]; then
            cp "$BT_FILE" "$DEPLOY_DIR/backtest_results.json"
            cp "$BT_FILE" "$DEPLOY_DIR/data.json"
            ok "Backtest incluido: $(basename $BT_FILE)"
        else
            err "No se encontró backtest/results/backtest_results.json"
        fi
        ;;

    "all")
        # Todos los JSONs disponibles (últimos 7 días + backtest)
        for f in "$BOT_DIR/logs/dashboard_trades_"*.json; do
            [[ -f "$f" ]] && cp "$f" "$DEPLOY_DIR/"
        done
        [[ -f "$BOT_DIR/backtest/results/backtest_results.json" ]] && \
            cp "$BOT_DIR/backtest/results/backtest_results.json" "$DEPLOY_DIR/"
        # data.json = el más reciente
        LATEST=$(ls -t "$BOT_DIR/logs/dashboard_trades_"*.json 2>/dev/null | head -1 || echo "")
        [[ -n "$LATEST" ]] && cp "$LATEST" "$DEPLOY_DIR/data.json"
        ok "Todos los JSONs incluidos"
        ;;
esac

# Contar archivos a subir
N_FILES=$(find "$DEPLOY_DIR" -type f | wc -l)
log "Archivos a subir: $N_FILES"

# ── Crear deployment en Cloudflare Pages ──────────────────
# Cloudflare Pages Direct Upload usa multipart/form-data
log "Subiendo a Cloudflare Pages (proyecto: $CF_PROJECT_NAME)..."

# Construir el comando curl con todos los archivos
CURL_CMD="curl -s -X POST \"$CF_API\" \
  -H \"Authorization: Bearer $CF_API_TOKEN\""

# Agregar cada archivo como parte del form
for filepath in "$DEPLOY_DIR"/*; do
    filename=$(basename "$filepath")
    CURL_CMD="$CURL_CMD -F \"${filename}=@${filepath}\""
done

# Ejecutar
RESPONSE=$(eval $CURL_CMD 2>/dev/null || echo '{"success":false,"errors":[{"message":"curl failed"}]}')

# Parsear respuesta
SUCCESS=$(echo "$RESPONSE" | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    print('true' if d.get('success') else 'false')
except:
    print('false')
")

if [[ "$SUCCESS" == "true" ]]; then
    URL=$(echo "$RESPONSE" | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    print(d.get('result', {}).get('url', ''))
except:
    print('')
")
    ok "Deploy exitoso!"
    echo ""
    echo -e "${GREEN}  URL pública: https://${CF_PROJECT_NAME}.pages.dev${NC}"
    [[ -n "$URL" ]] && echo -e "${GREEN}  Deploy URL:  ${URL}${NC}"
    echo ""
else
    ERROR=$(echo "$RESPONSE" | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    errs = d.get('errors', [])
    print(errs[0].get('message','unknown') if errs else 'unknown error')
except:
    print(sys.stdin.read()[:200])
" 2>/dev/null || echo "Error desconocido")
    err "Deploy fallido: $ERROR
    
    Verificá:
      - CF_API_TOKEN tiene permiso 'Cloudflare Pages: Edit'
      - CF_ACCOUNT_ID es correcto
      - El proyecto '$CF_PROJECT_NAME' existe en Cloudflare Pages
      
    Crear proyecto si no existe:
      https://dash.cloudflare.com → Pages → Create project → Direct Upload"
fi
