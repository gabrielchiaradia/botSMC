"""
scripts/run_bot.py  —  Fase 3
──────────────────────────────
Bot completo con:
  - Multi-timeframe (HTF contexto + LTF entrada)
  - WebSocket en tiempo real (reacciona al cierre de cada vela)
  - Notificaciones Telegram
  - Journal completo de señales y trades
  - Multi-bot: --bot-number N carga .envN

Uso:
    python scripts/run_bot.py               # Paper trading (Bot 1, .env)
    python scripts/run_bot.py --live        # Órdenes en Testnet
    python scripts/run_bot.py --bot-number 2  # Bot 2, carga .env2
"""

import sys
import os
import time
import argparse
import json
import websocket # type: ignore
import pandas as pd
from pathlib import Path

# ── Pre-parsear --bot-number ANTES de importar config ────────────────────
# Esto permite que config/settings.py lea la variable BOT_NUMBER
# y cargue el .env correcto.
_pre_parser = argparse.ArgumentParser(add_help=False)
_pre_parser.add_argument("--bot-number", type=int, default=None)
_pre_args, _ = _pre_parser.parse_known_args()
if _pre_args.bot_number is not None:
    os.environ["BOT_NUMBER"] = str(_pre_args.bot_number)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from datetime import datetime

from config import creds, exchange as excfg, mtf as mcfg, ws as wscfg, BOT_TAG, BOT_NUMBER
from config.settings import logs as lcfg, strategy as scfg
from data import (
    crear_cliente_futures,
    obtener_velas,
    obtener_historico_backtest,
    obtener_balance_usdt,
)
from strategy.mtf_analysis import analizar_mercado_mtf
from strategy import analizar_mercado
from strategy.risk import validar_tamaño, resumen_riesgo
from utils.logger import get_logger
from utils.trade_journal import TradeJournal
from utils.telegram_notify import crear_notifier

logger = get_logger("run_bot")

TF_SEGUNDOS = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400}


# ══════════════════════════════════════════════════════════
#  ARGS
# ══════════════════════════════════════════════════════════
def parse_args():
    p = argparse.ArgumentParser(description="SMC Bot Fase 3")
    p.add_argument("--live", action="store_true",
                   help="Ejecutar ordenes reales en Testnet")
    p.add_argument("--poll", action="store_true",
                   help="Usar polling REST en lugar de WebSocket")
    p.add_argument("--bot-number", type=int, default=1,
                   help="Número de bot (1=.env, 2=.env2, etc.)")
    return p.parse_args()


# ══════════════════════════════════════════════════════════
#  EJECUCION DE ORDENES
# ══════════════════════════════════════════════════════════
def ejecutar_orden_entrada(client, senal, tamanio, symbol) -> str:
    import hmac, hashlib, time, urllib.request, urllib.parse, json
    from binance.exceptions import BinanceAPIException
    from config import creds, exchange as excfg

    lado    = "BUY"  if senal.direccion == "ALCISTA" else "SELL"
    lado_sl = "SELL" if lado == "BUY" else "BUY"

    sl_price = round(senal.stop_loss, 2)
    tp_price = round(senal.take_profit, 2)

    # 1. Orden de entrada (MARKET)
    try:
        position_side = "LONG" if lado == "BUY" else "SHORT"
        orden = client.futures_create_order(
            symbol=symbol, side=lado, positionSide=position_side,
            type="MARKET", quantity=tamanio
        )
        oid = str(orden["orderId"])
        logger.info("Orden ejecutada: ID %s | %s %.3f %s", oid, lado, tamanio, symbol)
    except BinanceAPIException as e:
        logger.error("Error orden entrada: %s", e)
        return ""

    # 2. SL y TP via Algo Order API
    base_url = (
        "https://testnet.binancefuture.com"
        if excfg.TESTNET
        else "https://fapi.binance.com"
    )

    def _algo_order(stop_price: float, tipo: str) -> bool:
        params = {
            "symbol":        symbol,
            "side":          lado_sl,
            "positionSide":  "LONG" if lado == "BUY" else "SHORT",
            "quantity":      str(tamanio),
            "triggerprice":  str(stop_price),
            "price":         str(stop_price),
            "type":          tipo,
            "algoType":      "CONDITIONAL",
            "workingType":   "MARK_PRICE",
            "reduceOnly":    "true",
            "timestamp":     str(int(time.time() * 1000)),
        }
        query = urllib.parse.urlencode(params)
        sig   = hmac.new(
            creds.BINANCE_API_SECRET.encode(),
            query.encode(),
            hashlib.sha256
        ).hexdigest()
        url = f"{base_url}/fapi/v1/algoOrder?{query}&signature={sig}"
        req = urllib.request.Request(
            url,
            headers={"X-MBX-APIKEY": creds.BINANCE_API_KEY},
            method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                resp = json.loads(r.read().decode())
                logger.info("AlgoOrder %s OK: algoId=%s", tipo, resp.get("algoId"))
                return True
        except urllib.error.HTTPError as e:
            logger.error("AlgoOrder %s error %s: %s", tipo, e.code, e.read().decode())
            return False
        except Exception as e:
            logger.error("AlgoOrder %s error: %s", tipo, e)
            return False

    _algo_order(sl_price, "STOP")
    _algo_order(tp_price, "TAKE_PROFIT")
    return oid

def verificar_cierre_live(client, symbol, journal, balance):
    """Verifica si alguno de los trades abiertos se cerró en Binance.
    Lee el historial de órdenes para determinar si fue TP o SL."""
    cerrados = []
    if not journal.hay_trade_abierto() or not client:
        return cerrados
    try:
        posiciones = client.futures_position_information(symbol=symbol)
        pos_amt = 0
        for p in posiciones:
            if p["symbol"] == symbol:
                pos_amt = float(p["positionAmt"])
                break

        # Si no hay posición en Binance pero hay trades abiertos en el journal
        if pos_amt == 0 and journal.hay_trade_abierto():
            # Leer órdenes recientes para saber qué se ejecutó
            try:
                ordenes = client.futures_get_all_orders(
                    symbol=symbol, limit=20
                )
                # Buscar órdenes condicionales ejecutadas (SL o TP)
                ordenes_filled = [
                    o for o in ordenes
                    if o["status"] == "FILLED"
                    and o["type"] in ("STOP_MARKET", "TAKE_PROFIT_MARKET",
                                      "STOP", "TAKE_PROFIT",
                                      "TRAILING_STOP_MARKET")
                    and o["reduceOnly"] == True
                ]
            except Exception as e:
                logger.warning("No se pudieron leer órdenes: %s", e)
                ordenes_filled = []

            for t in journal.trades_abiertos():
                motivo = "SL"
                precio_cierre = t.stop_loss

                try:
                    trade_in_ms = int(datetime.fromisoformat(t.timestamp_in).timestamp() * 1000)
                    historial = client.futures_account_trades(
                        symbol=symbol, startTime=trade_in_ms, limit=50
                    )
                    # Buscar trades de cierre (lado contrario a la entrada)
                    close_side = "SELL" if t.direccion == "LONG" else "BUY"
                    cierres = [
                        h for h in historial
                        if h.get("side") == close_side
                        and float(h.get("realizedPnl", 0)) != 0
                    ]
                    if cierres:
                        # Precio promedio ponderado de ejecución real
                        total_qty = sum(float(h["qty"]) for h in cierres)
                        precio_cierre = sum(float(h["price"]) * float(h["qty"]) for h in cierres) / total_qty
                        pnl_real = sum(float(h.get("realizedPnl", 0)) for h in cierres)
                        # Determinar motivo por PnL vs niveles configurados
                        if t.direccion == "LONG":
                            motivo = "TP" if precio_cierre >= t.take_profit * 0.999 else "SL"
                        else:
                            motivo = "TP" if precio_cierre <= t.take_profit * 1.001 else "SL"
                        logger.info("Precio cierre real desde historial: $%.2f | Motivo: %s", precio_cierre, motivo)
                except Exception as e:
                    logger.warning("No se pudo leer historial de trades, usando precio SL/TP: %s", e)

                # Calcular PnL real
                if t.direccion == "LONG":
                    pnl = (precio_cierre - t.precio_entrada) * t.tamaño
                else:
                    pnl = (t.precio_entrada - precio_cierre) * t.tamaño

                nuevo_balance = round(balance + pnl, 2)
                trade_cerrado = journal.cerrar_trade(
                    precio_cierre, nuevo_balance, motivo, trade=t
                )
                if trade_cerrado:
                    cerrados.append(trade_cerrado)
                    exportar_dashboard(journal)
                    balance = nuevo_balance
                    try:
                        client.futures_cancel_all_open_orders(symbol=symbol)
                        logger.info("[%s] Órdenes huérfanas canceladas post-cierre.", symbol)
                    except Exception as e:
                        logger.warning("[%s] No se pudieron cancelar órdenes huérfanas: %s", symbol, e)

    except Exception as e:
        logger.warning("No se pudo verificar posicion: %s", e)
    return cerrados


def simular_cierre_paper(df, journal, balance):
    """Simula cierre de todos los trades abiertos en paper trading."""
    cerrados = []
    if not journal.hay_trade_abierto():
        return cerrados
    for t in journal.trades_abiertos():
        for i in range(max(0, len(df) - 50), len(df)):
            high, low = df["high"].iloc[i], df["low"].iloc[i]
            hit_tp = (high >= t.take_profit) if t.direccion == "LONG" else (low <= t.take_profit)
            hit_sl = (low  <= t.stop_loss)   if t.direccion == "LONG" else (high >= t.stop_loss)
            if hit_tp or hit_sl:
                motivo = "TP" if hit_tp else "SL"
                pc     = t.take_profit if hit_tp else t.stop_loss
                pnl    = (pc - t.precio_entrada) * t.tamaño if t.direccion == "LONG" \
                         else (t.precio_entrada - pc) * t.tamaño
                trade_cerrado = journal.cerrar_trade(pc, round(balance + pnl, 2), motivo, trade=t)
                if trade_cerrado:
                    cerrados.append(trade_cerrado)
                    exportar_dashboard(journal)
                    balance = round(balance + pnl, 2)
                break
    return cerrados

# ══════════════════════════════════════════════════════════
#  TRACKING DE HORARIO OPERATIVO
# ══════════════════════════════════════════════════════════

_en_horario_operativo = False

def chequear_horario_operativo(notifier, balance):
    global _en_horario_operativo
    from strategy.indicators import en_ventana_horaria
    from datetime import datetime, timezone

    # 1. Forzamos la obtención de la hora actual en UTC
    ahora_utc = datetime.now(timezone.utc)
    
    # 2. Convertimos a Timestamp de Pandas sin zona horaria para la comparación
    # Esto asegura que si son las 16:45 UTC, el bot compare contra el 16 de tu config
    ahora_pd = pd.Timestamp(ahora_utc.replace(tzinfo=None))

    # 3. Validamos si estamos dentro del rango (ej. 13-16)
    dentro = en_ventana_horaria(ahora_pd)

    # Log de diagnóstico para auditoría en tiempo real
    logger.info(f"🕒 Validando Horario: UTC={ahora_pd.strftime('%H:%M')} | ¿Dentro de ventana?: {dentro}")

    if dentro and not _en_horario_operativo:
        _en_horario_operativo = True
        from config import strategy as scfg
        windows = scfg.TRADING_WINDOWS_RAW
        notifier._enviar(
            f"🟢 *INICIO HORARIO OPERATIVO*\n"
            f"Ventana: `{windows}` UTC\n"
            f"Balance: `${balance:,.2f}` USDT\n"
            f"🕐 `{ahora_pd.strftime('%H:%M')} UTC / {ahora_utc.astimezone().strftime('%H:%M')} Local`"
        )
        logger.info("🟢 Inicio horario operativo: %s UTC", windows)

    elif not dentro and _en_horario_operativo:
        _en_horario_operativo = False
        from config import strategy as scfg
        windows = scfg.TRADING_WINDOWS_RAW
        notifier._enviar(
            f"🔴 *FIN HORARIO OPERATIVO*\n"
            f"Ventana: `{windows}` UTC cerrada\n"
            f"Balance: `${balance:,.2f}` USDT\n"
            f"🕐 `{ahora_pd.strftime('%H:%M')} UTC / {ahora_utc.astimezone().strftime('%H:%M')} Local`"
        )
        logger.info("🔴 Fin horario operativo: %s UTC", windows)

# ══════════════════════════════════════════════════════════
#  LOGICA CENTRAL DE ANALISIS
# ══════════════════════════════════════════════════════════
def procesar_velas(df_ltf, df_htf, client, modo_live,
                   journal, notifier, balance_ref, perfil=None):
    """
    Llamado por WebSocket (al cierre de vela) o por el loop de polling.
    Soporta múltiples trades simultáneos.
    """
    # balance_real: solo para verificar cierres en Binance
    # capital: capital asignado al bot (BOT_CAPITAL), usado para riesgo y journal
    balance_real = obtener_balance_usdt(client) if client else balance_ref[0]
    capital = balance_ref[0]

    # Chequear inicio/fin de horario operativo
    chequear_horario_operativo(notifier, capital)

    # Verificar cierre de posiciones abiertas
    trades_cerrados = []
    if modo_live and journal.hay_trade_abierto():
        trades_cerrados = verificar_cierre_live(client, excfg.SYMBOL, journal, capital)
    elif not modo_live and journal.hay_trade_abierto():
        trades_cerrados = simular_cierre_paper(df_ltf, journal, capital)

    for tc in trades_cerrados:
        notifier.trade_cerrado(tc, tc.motivo_cierre)
        exportar_dashboard(journal)
        # Actualizar capital con PnL real
        balance_ref[0] = round(balance_ref[0] + tc.pnl_usd, 2)
        logger.info("Capital actualizado: $%.2f (PnL: $%+.2f)", balance_ref[0], tc.pnl_usd)

    # Mostrar estado de posiciones abiertas
    if journal.hay_trade_abierto():
        for t in journal.trades_abiertos():
            logger.info("Posicion abierta: %s @ $%.2f | SL $%.2f | TP $%.2f",
                        t.direccion, t.precio_entrada, t.stop_loss, t.take_profit)

    # Si no hay espacio para más trades, salir
    if not journal.puede_abrir_trade():
        logger.info("Max trades alcanzado (%d/%d). Esperando cierre.",
                    journal.num_trades_abiertos(), journal.max_open)
        return

    # Analizar mercado (MTF si disponible, fallback a STF)
    if df_htf is not None and not df_htf.empty and mcfg.ENABLED:
        resultado = analizar_mercado_mtf(df_ltf, df_htf)
        senal     = resultado["señal"]
        logger.info(
            "MTF | LTF: %-8s HTF: %-8s | Alineacion: %-8s | Score: %d/100",
            resultado["tendencia_ltf"], resultado["tendencia_htf"],
            resultado["alineacion"], resultado["score_final"],
        )
    else:
        resultado = analizar_mercado(df_ltf)
        senal     = resultado["señal"]
        logger.info("STF | Tendencia: %-8s | Score: %d/100",
                    senal.tendencia, senal.score)

    precio = df_ltf["close"].iloc[-1]
    logger.info("Precio: $%.2f | Sesion: %s | Señal: %s | Open: %d/%d",
                precio, senal.sesion, "SI" if senal.tiene_señal else "NO",
                journal.num_trades_abiertos(), journal.max_open)

    if not senal.tiene_señal:
        logger.info("Sin senal. %s", " | ".join(senal.motivos) or "ninguno")
        journal.registrar_señal(senal, capital, "SIN_SEÑAL")
        return

    # Filtro de perfil (ob_bos, ema_filter, etc.)
    if perfil is not None:
        from strategy.profiles.base_profile import FilterContext
        from strategy.indicators import detectar_swings, calcular_atr
        swings = detectar_swings(df_ltf)
        atr = calcular_atr(df_ltf)
        ctx = FilterContext(
            df      = df_ltf,
            idx     = len(df_ltf) - 1,
            señal   = senal,
            swings  = swings,
            atr     = atr,
        )
        pasa, motivos_filtro = perfil.apply(ctx)
        if not pasa:
            motivo = motivos_filtro[-1] if motivos_filtro else "Perfil"
            logger.info("Señal filtrada por perfil '%s': %s", perfil.nombre, motivo)
            journal.registrar_señal(senal, capital, f"FILTRO_{perfil.nombre.upper()}")
            return
        # Agregar motivos del perfil a la señal
        senal.motivos.extend(m for m in motivos_filtro if m and m not in senal.motivos)

    # Filtro ventana horaria: señal válida pero fuera de horario
    if senal.fuera_de_horario:
        logger.info("⏰ Señal %s fuera de horario (%s). Registrando sin operar.",
                    senal.direccion, senal.ventana)
        journal.registrar_señal(senal, capital, "FUERA_DE_HORARIO")
        notifier.señal_fuera_de_horario(senal, capital)
        return

    # Calcular tamanio sobre capital asignado
    res = resumen_riesgo(capital, senal.precio_entrada, senal.stop_loss,
                         senal.take_profit, excfg.SYMBOL)
    tamanio = res["tamaño"]

    logger.info(
        "señal %s | E: $%.2f SL: $%.2f TP: $%.2f | "
        "Tam: %.5f | Riesgo: $%.2f | RR: 1:%.1f",
        senal.direccion, senal.precio_entrada, senal.stop_loss, senal.take_profit,
        tamanio, res["riesgo_usd"], res["rr_ratio"],
    )

    if not validar_tamaño(tamanio, senal.precio_entrada):
        logger.warning("Tamanio invalido, ignorada.")
        journal.registrar_señal(senal, capital, "TAMANIO_INVALIDO", tamanio)
        return

    # Ejecutar
    if modo_live and client:
        orden_id = ejecutar_orden_entrada(client, senal, tamanio, excfg.SYMBOL)
        accion   = "ENTRADA"

        if not orden_id:
            # Timeout -1007: verificar si la orden igual se ejecutó en Binance
            logger.warning("[%s] Timeout en orden — verificando posición real...", BOT_TAG)
            time.sleep(3)
            try:
                pos_info = client.futures_position_information(symbol=excfg.SYMBOL)
                hay_posicion = any(
                    float(p["positionAmt"]) != 0
                    for p in pos_info if p["symbol"] == excfg.SYMBOL
                )
            except Exception as e:
                logger.warning("[%s] No se pudo verificar posición post-timeout: %s", BOT_TAG, e)
                hay_posicion = False

            if not hay_posicion:
                logger.warning("[%s] Timeout confirmado sin posición — trade NO registrado.", BOT_TAG)
                journal.registrar_señal(senal, capital, "ENTRADA_FALLIDA", tamanio,
                                        res["riesgo_usd"], res["rr_ratio"], "")
                return
            else:
                logger.info("[%s] Posición detectada post-timeout — registrando trade.", BOT_TAG)
    else:
        orden_id = ""
        accion   = "PAPER_ENTRADA"
        logger.info("PAPER — orden simulada.")

    journal.registrar_señal(senal, capital, accion, tamanio,
                            res["riesgo_usd"], res["rr_ratio"], orden_id)
    journal.abrir_trade(senal, tamanio, capital, orden_id)

    notifier.señal_detectada(senal, capital, tamanio, res["riesgo_usd"],
                              "LIVE" if modo_live else "PAPER")

    if not modo_live:
        cerrados = simular_cierre_paper(df_ltf, journal, capital)
        for tc in cerrados:
            notifier.trade_cerrado(tc, tc.motivo_cierre)

    exportar_dashboard(journal)


# ══════════════════════════════════════════════════════════
#  EXPORTAR DASHBOARD
# ══════════════════════════════════════════════════════════
def exportar_dashboard(journal):
    datos = journal.exportar_como_backtest()
    if not datos:
        return
    # Agregar bot_tag al export para que el dashboard lo muestre
    datos["bot_tag"] = journal.bot_tag
    lcfg.LOG_DIR.mkdir(parents=True, exist_ok=True)
    suffix = f"_{journal._bot_num}" if journal._bot_num > 1 else ""
    path = lcfg.LOG_DIR / f"dashboard_trades{suffix}.json"

    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(datos, f, ensure_ascii=False, indent=2, default=str)
    except OSError as e:
        logger.warning("No se pudo exportar dashboard: %s", e)

    # Exportar posiciones abiertas
    exportar_posiciones_abiertas(journal)


def exportar_posiciones_abiertas(journal):
    """Escribe las posiciones abiertas actuales a un JSON para el dashboard."""
    lcfg.LOG_DIR.mkdir(parents=True, exist_ok=True)
    suffix = f"_{journal._bot_num}" if journal._bot_num > 1 else ""
    path = lcfg.LOG_DIR / f"open_positions{suffix}.json"
    try:
        datos = journal.exportar_posiciones_abiertas()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(datos, f, ensure_ascii=False, indent=2, default=str)
    except OSError as e:
        logger.warning("No se pudo exportar posiciones abiertas: %s", e)


# ══════════════════════════════════════════════════════════
#  RESUMEN FINAL
# ══════════════════════════════════════════════════════════
def mostrar_resumen(journal, modo, notifier):
    r     = journal.resumen_hoy()
    color = "\033[32m" if r["pnl_usd"] >= 0 else "\033[31m"
    reset = "\033[0m"
    fecha = datetime.now().strftime("%Y%m%d")
    print(f"\n{'='*52}")
    print(f"{'  RESUMEN DEL DIA':^52}")
    print(f"{'='*52}")
    print(f"  Trades:   {r['trades']}   Wins: {r['wins']}   Losses: {r['losses']}")
    print(f"  Win Rate: {r['win_rate']}%")
    print(f"  PnL:      {color}${r['pnl_usd']:+.2f} USDT{reset}")
    print(f"\n  logs/dashboard_trades_{fecha}.json <- dashboard")
    print(f"{'='*52}\n")
    notifier.resumen_diario(r, modo)
    notifier.bot_detenido("Ctrl+C")


# ══════════════════════════════════════════════════════════
#  RECONCILIACION DE POSICIONES
# ══════════════════════════════════════════════════════════
def reconciliar_posiciones(client, journal, balance):
    """
    Sincroniza el journal contra Binance al iniciar.
    - Limpia trades fantasma (están en journal pero no en Binance)
    - Restaura posiciones propias huérfanas usando open_positions{suffix}.json
      como fuente de verdad — ese archivo solo lo escribe este bot,
      por lo que no hay riesgo de registrar trades de otros bots o manuales
    """
    try:
        posiciones = client.futures_position_information(symbol=excfg.SYMBOL)
        pos_reales = {
            ("LONG" if float(p["positionAmt"]) > 0 else "SHORT"): p
            for p in posiciones
            if float(p["positionAmt"]) != 0
        }

        # 1. Limpiar fantasmas del journal
        for t in list(journal.trades_abiertos()):
            if t.direccion not in pos_reales:
                logger.warning(
                    "[%s] ⚠️  Trade fantasma eliminado: %s @ $%.2f",
                    BOT_TAG, t.direccion, t.precio_entrada
                )
                journal.cerrar_trade(
                    precio_salida = t.precio_entrada,
                    capital_out   = balance,
                    motivo_cierre = "CANCELADO",
                    trade         = t,
                )

        # 2. Restaurar huérfanas desde open_positions{suffix}.json
        # Este archivo solo lo escribe este bot → fuente de verdad segura
        from config.settings import logs as lcfg
        import json as _json
        suffix  = f"_{BOT_NUMBER}" if BOT_NUMBER > 1 else ""
        op_path = lcfg.LOG_DIR / f"open_positions{suffix}.json"

        if op_path.exists():
            op_data = _json.loads(op_path.read_text())
            for pos in op_data.get("posiciones", []):
                direccion = pos["direccion"]

                # ¿Ya está en el journal?
                ya_en_journal = any(
                    t.direccion == direccion
                    for t in journal.trades_abiertos()
                )
                if ya_en_journal:
                    continue

                # ¿Sigue abierta en Binance?
                if direccion not in pos_reales:
                    logger.warning(
                        "[%s] Posición propia (%s @ $%.2f) ya no existe en Binance — ignorando.",
                        BOT_TAG, direccion, pos["precio_entrada"]
                    )
                    continue

                logger.warning(
                    "[%s] ⚠️  Posición propia huérfana restaurada: %s @ $%.2f",
                    BOT_TAG, direccion, pos["precio_entrada"]
                )
                journal.registrar_posicion_externa(pos_reales[direccion], balance)

        exportar_posiciones_abiertas(journal)

    except Exception as e:
        logger.error("Error reconciliando posiciones con Binance: %s", e)

# ══════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════
def main():
    args = parse_args()
    modo = "LIVE" if args.live else "PAPER"
    usar_ws = wscfg.ENABLED and not args.poll

    from config import risk as rcfg

    print(f"\n{'='*60}")
    print(f"{'  SMC BOT v2 — ' + BOT_TAG:^60}")
    print(f"{'='*60}")
    print(f"  Bot:        #{BOT_NUMBER} ({BOT_TAG})")
    print(f"  Par:        {excfg.SYMBOL}")
    if mcfg.ENABLED:
        print(f"  LTF:        {mcfg.LTF}  (entrada)")
        print(f"  HTF:        {mcfg.HTF}  (contexto)")
    else:
        print(f"  Timeframe:  {excfg.TIMEFRAME}")
    print(f"  Modo:       {modo}")
    print(f"  Stream:     {'WebSocket' if usar_ws else 'Polling REST'}")
    print(f"  MTF:        {'ON' if mcfg.ENABLED else 'OFF'}")
    print(f"  Max trades: {rcfg.MAX_OPEN_TRADES}")
    print(f"  RR:         1:{rcfg.TP_RR_RATIO}")
    print(f"  Riesgo:     {rcfg.RISK_PER_TRADE*100:.1f}% por trade")
    print(f"  Estrategia: {scfg.STRATEGY}")
    if rcfg.BOT_CAPITAL > 0:
        print(f"  Capital:    ${rcfg.BOT_CAPITAL:,.0f} USDT (asignado)")
    print(f"{'='*60}\n")

    # ── Cargar perfil de estrategia ───────────────────────
    perfil = None
    if scfg.STRATEGY != "base":
        try:
            from strategy.profiles import get_profile
            perfil = get_profile(scfg.STRATEGY)
            logger.info("[%s] Perfil cargado: %s — %s",
                        BOT_TAG, perfil.nombre, perfil.descripcion)
        except Exception as e:
            logger.warning("No se pudo cargar perfil '%s': %s. Usando base.",
                           scfg.STRATEGY, e)

    # ── Conectar exchange ─────────────────────────────────
    client = None
    if args.live:
        creds.validate()
        client = crear_cliente_futures(
            api_key=creds.BINANCE_API_KEY,
            api_secret=creds.BINANCE_API_SECRET,
            testnet=excfg.TESTNET,
        )

    # ── Servicios ─────────────────────────────────────────
    journal  = TradeJournal(symbol=excfg.SYMBOL, timeframe=mcfg.LTF, modo=modo,
                            max_open=rcfg.MAX_OPEN_TRADES, bot_tag=BOT_TAG)
    notifier = crear_notifier(bot_tag=BOT_TAG)

    # Capital: usar BOT_CAPITAL del .env si está seteado, sino balance real
    balance_real = obtener_balance_usdt(client) if client else 1000.0
    if rcfg.BOT_CAPITAL > 0:
        # Recuperar capital acumulado del journal si hay trades cerrados hoy.
        # Asi al reiniciar el container no se pierde el PnL del dia.
        capital_recuperado = journal.recuperar_capital_hoy(rcfg.BOT_CAPITAL)
        balance_ref = [capital_recuperado]
        logger.info("[%s] Capital: $%.2f USDT (base: $%.2f, balance real: $%.2f)",
                    BOT_TAG, capital_recuperado, rcfg.BOT_CAPITAL, balance_real)
    else:
        balance_ref = [balance_real]
        logger.info("[%s] Balance inicial: $%.2f USDT", BOT_TAG, balance_ref[0])

    notifier.bot_iniciado(excfg.SYMBOL, mcfg.LTF, mcfg.HTF, modo, balance_ref[0],
                       mtf_enabled=mcfg.ENABLED,
                       perfil=perfil,
                       rr=rcfg.TP_RR_RATIO,
                       max_trades=rcfg.MAX_OPEN_TRADES,
                       windows=scfg.TRADING_WINDOWS_RAW)


    # ── Reconciliar posiciones abiertas en Binance ────────
    if args.live and client:
        reconciliar_posiciones(client, journal, balance_ref[0])
        # Exportar dashboard al arrancar con todos los trades históricos
    exportar_dashboard(journal)
    logger.info("[%s] Dashboard exportado al iniciar.", BOT_TAG)

    # ── Pre-cargar datos historicos ───────────────────────
    logger.info("Pre-cargando datos historicos...")
    df_ltf_hist = obtener_historico_backtest(excfg.SYMBOL, mcfg.LTF, dias=3)
    df_htf_hist = obtener_historico_backtest(excfg.SYMBOL, mcfg.HTF, dias=10)

    try:
        # ══════════════════════════════════════════════════
        #  MODO WEBSOCKET
        # ══════════════════════════════════════════════════
        if usar_ws:
            try:
                from bot.websocket_stream import MTFStream
            except ImportError:
                logger.error("websocket-client no instalado. Usar: pip install websocket-client")
                logger.info("Cambiando a modo polling...")
                usar_ws = False

        if usar_ws:
            def on_señal(df_ltf, df_htf):
                procesar_velas(df_ltf, df_htf, client, args.live,
                               journal, notifier, balance_ref, perfil)

            stream = MTFStream(
                symbol     = excfg.SYMBOL,
                ltf        = mcfg.LTF,
                htf        = mcfg.HTF,
                on_señal   = on_señal,
                testnet    = excfg.TESTNET,
            )
            stream.iniciar(df_ltf_hist, df_htf_hist)
            logger.info("Esperando velas en tiempo real (Ctrl+C para detener)...")
            while True:
                time.sleep(1)

        # ══════════════════════════════════════════════════
        #  MODO POLLING
        # ══════════════════════════════════════════════════
        else:
            espera  = TF_SEGUNDOS.get(mcfg.LTF, 300)
            ciclo_n = 0
            while True:
                ciclo_n += 1
                logger.info("--- Ciclo %d | %s ---",
                            ciclo_n, datetime.now().strftime("%H:%M:%S"))
                try:
                    df_ltf = obtener_velas(client, excfg.SYMBOL, mcfg.LTF,
                                           limit=mcfg.LTF_CANDLES)
                    df_htf = obtener_velas(client, excfg.SYMBOL, mcfg.HTF,
                                           limit=mcfg.HTF_CANDLES)
                    procesar_velas(df_ltf, df_htf, client, args.live,
                                   journal, notifier, balance_ref, perfil)
                except Exception as e:
                    logger.error("Error en ciclo %d: %s", ciclo_n, e, exc_info=True)
                    notifier.error_critico(str(e))
                logger.info("Proximo ciclo en %d min.\n", espera // 60)
                time.sleep(espera)

    except KeyboardInterrupt:
        logger.info("Bot detenido por el usuario.")
        if usar_ws:
            stream.detener()
        mostrar_resumen(journal, modo, notifier)


if __name__ == "__main__":
    main()
