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
    from binance.exceptions import BinanceAPIException
    lado    = "BUY"  if senal.direccion == "ALCISTA" else "SELL"
    lado_sl = "SELL" if lado == "BUY" else "BUY"
    
    # Redondear para cumplir con precisión de Binance
    if symbol == "ETHUSDT":
        tamanio = round(tamanio, 3)  # ETH: 3 decimales
        sl_price = round(senal.stop_loss, 2)
        tp_price = round(senal.take_profit, 2)
    else:
        tamanio = round(tamanio, 2)  # BTC: 3 decimales
        sl_price = round(senal.stop_loss, 1)
        tp_price = round(senal.take_profit, 1)
    try:
        orden = client.futures_create_order(
            symbol=symbol, side=lado, type="MARKET", quantity=tamanio
        )
        oid = str(orden["orderId"])
        logger.info("Orden ejecutada: ID %s | %s %.3f %s", oid, lado, tamanio, symbol)
        client.futures_create_order(symbol=symbol, side=lado_sl,
            type="STOP_MARKET", stopPrice=sl_price, closePosition=True)
        client.futures_create_order(symbol=symbol, side=lado_sl,
            type="TAKE_PROFIT_MARKET", stopPrice=tp_price, closePosition=True)
        return oid
    except BinanceAPIException as e:
        logger.error("Error orden: %s", e)
        return ""


def verificar_cierre_live(client, symbol, journal, balance):
    """Verifica si alguno de los trades abiertos se cerró en Binance."""
    cerrados = []
    if not journal.hay_trade_abierto() or not client:
        return cerrados
    try:
        posiciones = client.futures_get_position_risk(symbol=symbol)
        pos_amt = 0
        mark_price = 0
        for p in posiciones:
            if p["symbol"] == symbol:
                pos_amt = float(p["positionAmt"])
                mark_price = float(p["markPrice"])
                break

        # Si no hay posición en Binance pero hay trades abiertos en el journal
        if pos_amt == 0 and journal.hay_trade_abierto():
            for t in journal.trades_abiertos():
                if t.direccion == "LONG":
                    motivo = "TP" if mark_price >= t.take_profit * 0.999 else "SL"
                    pc = t.take_profit if motivo == "TP" else t.stop_loss
                else:
                    motivo = "TP" if mark_price <= t.take_profit * 1.001 else "SL"
                    pc = t.take_profit if motivo == "TP" else t.stop_loss
                trade_cerrado = journal.cerrar_trade(pc, balance, motivo, trade=t)
                if trade_cerrado:
                    cerrados.append(trade_cerrado)
                    exportar_dashboard(journal)
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
#  LOGICA CENTRAL DE ANALISIS
# ══════════════════════════════════════════════════════════
def procesar_velas(df_ltf, df_htf, client, modo_live,
                   journal, notifier, balance_ref, perfil=None):
    """
    Llamado por WebSocket (al cierre de vela) o por el loop de polling.
    Soporta múltiples trades simultáneos.
    """
    balance = obtener_balance_usdt(client) if client else balance_ref[0]

    # Verificar cierre de posiciones abiertas
    trades_cerrados = []
    if modo_live and journal.hay_trade_abierto():
        trades_cerrados = verificar_cierre_live(client, excfg.SYMBOL, journal, balance)
    elif not modo_live and journal.hay_trade_abierto():
        trades_cerrados = simular_cierre_paper(df_ltf, journal, balance)

    for tc in trades_cerrados:
        notifier.trade_cerrado(tc, tc.motivo_cierre)
        exportar_dashboard(journal)

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
        journal.registrar_señal(senal, balance, "SIN_SEÑAL")
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
            señal   = señal,
            swings  = swings,
            atr     = atr,
            precio  = df_ltf["close"].iloc[-1],
        )
        pasa, motivos_filtro = perfil.apply(ctx)
        if not pasa:
            motivo = motivos_filtro[-1] if motivos_filtro else "Perfil"
            logger.info("Señal filtrada por perfil '%s': %s", perfil.nombre, motivo)
            journal.registrar_señal(señal, balance, f"FILTRO_{perfil.nombre.upper()}")
            return
        # Agregar motivos del perfil a la señal
        señal.motivos.extend(m for m in motivos_filtro if m and m not in señal.motivos)

    # Filtro ventana horaria: señal válida pero fuera de horario
    if señal.fuera_de_horario:
        logger.warning(f"DEBUG: {señal.ticker} | Hora Señal (UTC): {señal.timestamp.hour:02d}:00 | Ventana: {señal.ventana} | Fuera de Horario: {señal.fuera_de_horario}")
        logger.info("⏰ Señal %s fuera de horario (%s). Registrando sin operar.",
                    señal.direccion, señal.ventana)
        journal.registrar_señal(señal, balance, "FUERA_DE_HORARIO")
        # Notificar por Telegram para tracking
        notifier.señal_detectada(señal, balance, 0, 0,
                                  "⏰ FUERA DE HORARIO — no operada")
        return

    # Calcular tamanio
    res     = resumen_riesgo(balance, señal.precio_entrada, señal.stop_loss, señal.take_profit)
    tamanio = res["tamaño"]

    logger.info(
        "señal %s | E: $%.2f SL: $%.2f TP: $%.2f | "
        "Tam: %.5f | Riesgo: $%.2f | RR: 1:%.1f",
        señal.direccion, señal.precio_entrada, señal.stop_loss, señal.take_profit,
        tamanio, res["riesgo_usd"], res["rr_ratio"],
    )

    if not validar_tamaño(tamanio, señal.precio_entrada):
        logger.warning("Tamanio invalido, ignorada.")
        journal.registrar_señal(señal, balance, "TAMANIO_INVALIDO", tamanio)
        return

    # Ejecutar
    if modo_live and client:
        orden_id = ejecutar_orden_entrada(client, señal, tamanio, excfg.SYMBOL)
        accion   = "ENTRADA"
    else:
        orden_id = ""
        accion   = "PAPER_ENTRADA"
        logger.info("PAPER — orden simulada.")

    journal.registrar_señal(señal, balance, accion, tamanio,
                            res["riesgo_usd"], res["rr_ratio"], orden_id)
    journal.abrir_trade(señal, tamanio, balance, orden_id)

    notifier.señal_detectada(señal, balance, tamanio, res["riesgo_usd"],
                              "LIVE" if modo_live else "PAPER")

    if not modo_live:
        cerrados = simular_cierre_paper(df_ltf, journal, balance)
        for tc in cerrados:
            notifier.trade_cerrado(tc, tc.motivo_cierre)

    exportar_dashboard(journal)
    balance_ref[0] = obtener_balance_usdt(client) if client else balance_ref[0]


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
    path = lcfg.LOG_DIR / f"dashboard_trades{suffix}_{datetime.now().strftime('%Y%m%d')}.json"
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
        balance_ref = [rcfg.BOT_CAPITAL]
        logger.info("[%s] Capital asignado: $%.2f USDT (balance real: $%.2f)",
                    BOT_TAG, rcfg.BOT_CAPITAL, balance_real)
    else:
        balance_ref = [balance_real]
        logger.info("[%s] Balance inicial: $%.2f USDT", BOT_TAG, balance_ref[0])

    notifier.bot_iniciado(excfg.SYMBOL, mcfg.LTF, mcfg.HTF, modo, balance_ref[0],
                          mtf_enabled=mcfg.ENABLED)

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
