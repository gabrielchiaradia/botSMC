#!/usr/bin/env python3
"""
scripts/run_grid.py
────────────────────
Grid Bot para Binance Spot.

Coloca órdenes de compra y venta en niveles de precio predefinidos.
Cada vez que se ejecuta una orden, repone la contraria.
Ganancias vienen del spread entre niveles.

Configuración via .env3 (o .envN con --bot-number N):
  GRID_SYMBOL=ETHUSDT
  GRID_UPPER=2200        # Techo del grid
  GRID_LOWER=1800        # Piso del grid
  GRID_COUNT=10          # Cantidad de niveles
  GRID_CAPITAL=200       # USDT a usar
  GRID_STOP_LOSS_PCT=5   # Cerrar todo si cae X% debajo del lower
  GRID_REPOSITION=true   # Reposicionar si sube por encima del upper
  GRID_MAX_LOSS_USD=50   # Pérdida máxima absoluta

Uso:
    python scripts/run_grid.py                   # Paper mode
    python scripts/run_grid.py --live             # Órdenes reales
    python scripts/run_grid.py --bot-number 3     # Lee .env3
"""

import sys
import os
import time
import json
import argparse
from pathlib import Path
from datetime import datetime

# Pre-parsear --bot-number antes de importar config
_pre_parser = argparse.ArgumentParser(add_help=False)
_pre_parser.add_argument("--bot-number", type=int, default=None)
_pre_args, _ = _pre_parser.parse_known_args()
if _pre_args.bot_number is not None:
    os.environ["BOT_NUMBER"] = str(_pre_args.bot_number)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.settings import BOT_NUMBER, BOT_TAG, logs as lcfg
from utils.logger import get_logger
from utils.telegram_notify import TelegramNotifier

logger = get_logger("run_grid")


# ══════════════════════════════════════════════════════════
#  CONFIGURACIÓN GRID
# ══════════════════════════════════════════════════════════
class GridConfig:
    SYMBOL:          str   = os.getenv("GRID_SYMBOL", os.getenv("SYMBOL", "ETHUSDT"))
    UPPER:           float = float(os.getenv("GRID_UPPER", "2200"))
    LOWER:           float = float(os.getenv("GRID_LOWER", "1800"))
    COUNT:           int   = int(os.getenv("GRID_COUNT", "10"))
    CAPITAL:         float = float(os.getenv("GRID_CAPITAL", os.getenv("BOT_CAPITAL", "200")))
    STOP_LOSS_PCT:   float = float(os.getenv("GRID_STOP_LOSS_PCT", "5"))
    REPOSITION:      bool  = os.getenv("GRID_REPOSITION", "true").lower() == "true"
    MAX_LOSS_USD:    float = float(os.getenv("GRID_MAX_LOSS_USD", "50"))
    CHECK_INTERVAL:  int   = int(os.getenv("GRID_CHECK_INTERVAL", "30"))  # segundos
    TESTNET:         bool  = os.getenv("TESTNET", "true").lower() == "true"

    API_KEY:         str   = os.getenv("BINANCE_API_KEY", "")
    API_SECRET:      str   = os.getenv("BINANCE_API_SECRET", "")
    TELEGRAM_TOKEN:  str   = os.getenv("TELEGRAM_TOKEN", "")
    TELEGRAM_CHAT_ID:str   = os.getenv("TELEGRAM_CHAT_ID", "")


# ══════════════════════════════════════════════════════════
#  GRID LEVEL
# ══════════════════════════════════════════════════════════
class GridLevel:
    """Representa un nivel del grid."""
    def __init__(self, price: float, index: int):
        self.price = price
        self.index = index
        self.buy_order_id = None
        self.sell_order_id = None
        self.has_position = False  # True = comprado en este nivel
        self.qty = 0.0


# ══════════════════════════════════════════════════════════
#  GRID BOT
# ══════════════════════════════════════════════════════════
class GridBot:
    def __init__(self, cfg: GridConfig, client=None, live: bool = False):
        self.cfg = cfg
        self.client = client
        self.live = live
        self.levels: list[GridLevel] = []
        self.total_profit = 0.0
        self.total_trades = 0
        self.initial_capital = cfg.CAPITAL
        self.usdt_free = cfg.CAPITAL   # USDT disponible para comprar
        self.running = True
        self.start_time = datetime.now()

        # Calcular niveles
        self._crear_niveles()

        # Calcular cantidad por nivel
        self.qty_per_level = self._calcular_qty_por_nivel()

    def _crear_niveles(self):
        """Crea los niveles del grid equidistantes."""
        step = (self.cfg.UPPER - self.cfg.LOWER) / self.cfg.COUNT
        self.levels = []
        for i in range(self.cfg.COUNT + 1):
            price = round(self.cfg.LOWER + i * step, 2)
            self.levels.append(GridLevel(price, i))
        self.step = round(step, 2)
        logger.info("Grid creado: %d niveles de $%.2f a $%.2f (step: $%.2f)",
                    len(self.levels), self.cfg.LOWER, self.cfg.UPPER, self.step)

    def _calcular_qty_por_nivel(self) -> float:
        """Calcula la cantidad a comprar/vender por nivel."""
        # Dividir capital entre la mitad de los niveles (compramos en la mitad inferior)
        capital_por_nivel = self.cfg.CAPITAL / (self.cfg.COUNT / 2)
        avg_price = (self.cfg.UPPER + self.cfg.LOWER) / 2
        qty = capital_por_nivel / avg_price
        # Redondear a 3 decimales (ETH), ajustar para otros pares
        qty = round(qty, 4)
        logger.info("Cantidad por nivel: %.4f %s (~$%.2f)",
                    qty, self.cfg.SYMBOL.replace("USDT", ""), capital_por_nivel)
        return qty

    def _get_precio_actual(self) -> float:
        """Obtiene el precio actual del par."""
        if self.client:
            try:
                ticker = self.client.get_symbol_ticker(symbol=self.cfg.SYMBOL)
                return float(ticker["price"])
            except Exception as e:
                logger.error("Error obteniendo precio: %s", e)
                return 0.0
        return 0.0

    def _get_step_size(self) -> float:
        """Obtiene el step size mínimo del par desde Binance."""
        if not self.client:
            return 0.0001
        try:
            info = self.client.get_symbol_info(self.cfg.SYMBOL)
            for f in info["filters"]:
                if f["filterType"] == "LOT_SIZE":
                    return float(f["stepSize"])
        except Exception:
            pass
        return 0.0001

    def _redondear_qty(self, qty: float) -> float:
        """Redondea la cantidad al step size del par."""
        step = self._get_step_size()
        if step > 0:
            qty = round(qty - (qty % step), 8)
        return qty

    # ── Órdenes ────────────────────────────────────────────

    def _colocar_orden_compra(self, level: GridLevel) -> bool:
        """Coloca una orden limit de compra en el nivel."""
        qty = self._redondear_qty(self.qty_per_level)
        if qty <= 0:
            return False

        if self.live and self.client:
            try:
                order = self.client.create_order(
                    symbol=self.cfg.SYMBOL,
                    side="BUY",
                    type="LIMIT",
                    timeInForce="GTC",
                    quantity=qty,
                    price=str(level.price),
                )
                level.buy_order_id = order["orderId"]
                logger.info("BUY limit colocada: $%.2f x %.4f (ID: %s)",
                           level.price, qty, order["orderId"])
                return True
            except Exception as e:
                logger.error("Error colocando BUY en $%.2f: %s", level.price, e)
                return False
        else:
            # Paper mode
            level.buy_order_id = f"paper_buy_{level.index}_{int(time.time())}"
            logger.info("PAPER BUY limit: $%.2f x %.4f", level.price, qty)
            return True

    def _colocar_orden_venta(self, level: GridLevel) -> bool:
        """Coloca una orden limit de venta en el nivel."""
        qty = self._redondear_qty(self.qty_per_level)
        if qty <= 0:
            return False

        if self.live and self.client:
            try:
                order = self.client.create_order(
                    symbol=self.cfg.SYMBOL,
                    side="SELL",
                    type="LIMIT",
                    timeInForce="GTC",
                    quantity=qty,
                    price=str(level.price),
                )
                level.sell_order_id = order["orderId"]
                logger.info("SELL limit colocada: $%.2f x %.4f (ID: %s)",
                           level.price, qty, order["orderId"])
                return True
            except Exception as e:
                logger.error("Error colocando SELL en $%.2f: %s", level.price, e)
                return False
        else:
            level.sell_order_id = f"paper_sell_{level.index}_{int(time.time())}"
            logger.info("PAPER SELL limit: $%.2f x %.4f", level.price, qty)
            return True

    def _cancelar_todas_ordenes(self):
        """Cancela todas las órdenes abiertas del par."""
        if self.live and self.client:
            try:
                self.client.cancel_all_orders(symbol=self.cfg.SYMBOL)
                logger.info("Todas las órdenes canceladas para %s", self.cfg.SYMBOL)
            except Exception as e:
                logger.error("Error cancelando órdenes: %s", e)

        for level in self.levels:
            level.buy_order_id = None
            level.sell_order_id = None

    # ── Lógica principal ───────────────────────────────────

    def setup_inicial(self, precio_actual: float):
        """
        Configura el grid inicial:
        - Niveles debajo del precio actual: poner órdenes de COMPRA
        - Niveles arriba del precio actual: no hacer nada (esperamos compra primero)
        """
        logger.info("Setup inicial con precio $%.2f", precio_actual)

        for level in self.levels:
            if level.price < precio_actual:
                # Nivel debajo del precio → orden de compra
                self._colocar_orden_compra(level)
            # Niveles arriba no hacen nada — vendemos cuando suba después de comprar

    def verificar_ejecuciones(self, precio_actual: float):
        """
        Verifica qué órdenes se ejecutaron y repone las contrarias.
        En paper mode, simula la ejecución por precio.
        """
        for level in self.levels:
            # ── Verificar compras ejecutadas ──
            if level.buy_order_id and not level.has_position:
                ejecutada = False

                if self.live and self.client:
                    try:
                        order = self.client.get_order(
                            symbol=self.cfg.SYMBOL,
                            orderId=level.buy_order_id,
                        )
                        ejecutada = order["status"] == "FILLED"
                    except Exception:
                        pass
                else:
                    # Paper: se ejecuta si el precio bajó al nivel
                    ejecutada = precio_actual <= level.price

                if ejecutada:
                    cost = level.price * self.qty_per_level
                    # Verificar que hay capital suficiente
                    if cost > self.usdt_free:
                        level.buy_order_id = None  # Cancelar, no hay plata
                        continue
                    level.has_position = True
                    level.qty = self.qty_per_level
                    level.buy_order_id = None
                    self.usdt_free -= cost

                    logger.info("✅ COMPRA ejecutada: $%.2f x %.4f = $%.2f (USDT libre: $%.2f)",
                               level.price, level.qty, cost, self.usdt_free)

                    # Colocar venta un nivel arriba
                    next_idx = level.index + 1
                    if next_idx < len(self.levels):
                        sell_level = self.levels[next_idx]
                        if not sell_level.sell_order_id:
                            self._colocar_orden_venta(sell_level)

            # ── Verificar ventas ejecutadas ──
            if level.sell_order_id:
                ejecutada = False

                if self.live and self.client:
                    try:
                        order = self.client.get_order(
                            symbol=self.cfg.SYMBOL,
                            orderId=level.sell_order_id,
                        )
                        ejecutada = order["status"] == "FILLED"
                    except Exception:
                        pass
                else:
                    # Paper: se ejecuta si el precio subió al nivel
                    ejecutada = precio_actual >= level.price

                if ejecutada:
                    # Encontrar el nivel de compra (un nivel abajo)
                    prev_idx = level.index - 1
                    if prev_idx >= 0:
                        buy_level = self.levels[prev_idx]
                        if buy_level.has_position:
                            profit = (level.price - buy_level.price) * buy_level.qty
                            revenue = level.price * buy_level.qty
                            self.total_profit += profit
                            self.total_trades += 1
                            self.usdt_free += revenue  # Recibimos USDT de la venta

                            buy_level.has_position = False
                            buy_level.qty = 0.0

                            logger.info("💰 VENTA ejecutada: $%.2f | Profit: $%.4f | Total: $%.4f | USDT: $%.2f (%d trades)",
                                       level.price, profit, self.total_profit, self.usdt_free, self.total_trades)

                    level.sell_order_id = None

                    # Reponer compra un nivel abajo
                    if prev_idx >= 0:
                        buy_level = self.levels[prev_idx]
                        if not buy_level.buy_order_id and not buy_level.has_position:
                            self._colocar_orden_compra(buy_level)

    # ── Protecciones ───────────────────────────────────────

    def verificar_stop_loss(self, precio_actual: float) -> bool:
        """Retorna True si hay que parar el bot por stop loss."""
        sl_price = self.cfg.LOWER * (1 - self.cfg.STOP_LOSS_PCT / 100)
        if precio_actual < sl_price:
            logger.warning("⛔ STOP LOSS activado: precio $%.2f < SL $%.2f (-%s%% del lower)",
                          precio_actual, sl_price, self.cfg.STOP_LOSS_PCT)
            return True
        return False

    def verificar_max_loss(self, precio_actual: float) -> bool:
        """Retorna True si la pérdida supera el máximo."""
        equity = self.calcular_equity(precio_actual)
        loss = self.initial_capital - equity
        if loss > self.cfg.MAX_LOSS_USD:
            logger.warning("⛔ MAX LOSS alcanzado: perdida $%.2f > $%.2f",
                          loss, self.cfg.MAX_LOSS_USD)
            return True
        return False

    def calcular_equity(self, precio_actual: float) -> float:
        """Equity total = USDT libre + valor de ETH en posiciones al precio actual."""
        valor_holdings = sum(precio_actual * l.qty for l in self.levels if l.has_position)
        return self.usdt_free + valor_holdings

    def verificar_reposition(self, precio_actual: float) -> bool:
        """Retorna True si hay que reposicionar el grid hacia arriba."""
        if not self.cfg.REPOSITION:
            return False
        return precio_actual > self.cfg.UPPER

    def reposicionar_grid(self, precio_actual: float):
        """Mueve el grid hacia arriba centrado en el precio actual."""
        logger.info("🔄 Reposicionando grid alrededor de $%.2f", precio_actual)
        self._cancelar_todas_ordenes()

        rango = self.cfg.UPPER - self.cfg.LOWER
        self.cfg.LOWER = round(precio_actual - rango * 0.4, 2)  # 40% abajo
        self.cfg.UPPER = round(precio_actual + rango * 0.6, 2)  # 60% arriba

        self._crear_niveles()
        self.setup_inicial(precio_actual)

    def cerrar_todo(self, precio_actual: float):
        """Cierra todas las posiciones y cancela órdenes."""
        logger.info("Cerrando todas las posiciones al precio $%.2f...", precio_actual)
        self._cancelar_todas_ordenes()

        # Vender todo lo que esté comprado al precio actual
        for level in self.levels:
            if level.has_position and level.qty > 0:
                if self.live and self.client:
                    try:
                        self.client.create_order(
                            symbol=self.cfg.SYMBOL,
                            side="SELL",
                            type="MARKET",
                            quantity=self._redondear_qty(level.qty),
                        )
                    except Exception as e:
                        logger.error("Error vendiendo posición en $%.2f: %s", level.price, e)

                revenue = precio_actual * level.qty
                pnl = (precio_actual - level.price) * level.qty
                self.usdt_free += revenue
                self.total_profit += pnl
                logger.info("Cerrado nivel $%.2f → $%.2f | PnL: $%.4f",
                           level.price, precio_actual, pnl)

                level.has_position = False
                level.qty = 0.0

    # ── Estado ─────────────────────────────────────────────

    def resumen(self, precio_actual: float) -> dict:
        """Genera resumen del estado actual."""
        posiciones_abiertas = sum(1 for l in self.levels if l.has_position)
        ordenes_buy = sum(1 for l in self.levels if l.buy_order_id)
        ordenes_sell = sum(1 for l in self.levels if l.sell_order_id)

        equity = self.calcular_equity(precio_actual)
        valor_holdings = sum(precio_actual * l.qty for l in self.levels if l.has_position)
        elapsed = (datetime.now() - self.start_time).total_seconds() / 3600

        return {
            "precio": precio_actual,
            "rango": f"${self.cfg.LOWER} - ${self.cfg.UPPER}",
            "niveles": len(self.levels),
            "step": self.step,
            "posiciones": posiciones_abiertas,
            "ordenes_buy": ordenes_buy,
            "ordenes_sell": ordenes_sell,
            "usdt_free": round(self.usdt_free, 4),
            "valor_holdings": round(valor_holdings, 4),
            "equity": round(equity, 4),
            "profit_realizado": round(self.total_profit, 4),
            "retorno_pct": round((equity - self.initial_capital) / self.initial_capital * 100, 2),
            "trades": self.total_trades,
            "horas": round(elapsed, 1),
            "capital_inicial": self.initial_capital,
        }

    def exportar_estado(self):
        """Exporta el estado a JSON para el dashboard."""
        precio = self._get_precio_actual() if self.client else 0
        estado = self.resumen(precio)
        estado["bot_tag"] = BOT_TAG
        estado["symbol"] = self.cfg.SYMBOL
        estado["updated_at"] = datetime.now().isoformat()
        estado["niveles_detalle"] = [
            {
                "price": l.price,
                "has_position": l.has_position,
                "has_buy": l.buy_order_id is not None,
                "has_sell": l.sell_order_id is not None,
            }
            for l in self.levels
        ]

        lcfg.LOG_DIR.mkdir(parents=True, exist_ok=True)
        suffix = f"_{BOT_NUMBER}" if BOT_NUMBER > 1 else ""
        path = lcfg.LOG_DIR / f"grid_status{suffix}.json"
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(estado, f, ensure_ascii=False, indent=2, default=str)
        except OSError as e:
            logger.warning("No se pudo exportar estado grid: %s", e)


# ══════════════════════════════════════════════════════════
#  ARGS
# ══════════════════════════════════════════════════════════
def parse_args():
    p = argparse.ArgumentParser(description="Grid Bot Spot")
    p.add_argument("--live", action="store_true",
                   help="Ejecutar órdenes reales")
    p.add_argument("--backtest", action="store_true",
                   help="Correr backtest con datos históricos")
    p.add_argument("--auto-range", action="store_true", default=True,
                   help="Calcular rango automáticamente desde datos históricos (default: on)")
    p.add_argument("--no-auto-range", action="store_true",
                   help="Usar GRID_UPPER/GRID_LOWER del .env sin auto-calcular")
    p.add_argument("--dias", type=int, default=180,
                   help="Días de historia para backtest (default: 180)")
    p.add_argument("--symbol", default=None,
                   help="Override GRID_SYMBOL del .env")
    p.add_argument("--upper", type=float, default=None,
                   help="Override GRID_UPPER (desactiva auto-range)")
    p.add_argument("--lower", type=float, default=None,
                   help="Override GRID_LOWER (desactiva auto-range)")
    p.add_argument("--count", type=int, default=None,
                   help="Override GRID_COUNT")
    p.add_argument("--capital", type=float, default=None,
                   help="Override GRID_CAPITAL")
    p.add_argument("--bot-number", type=int, default=3,
                   help="Número de bot (default: 3, lee .env3)")
    return p.parse_args()


def calcular_rango_auto(df, margen_pct: float = 5.0):
    """
    Calcula el rango del grid basándose en percentiles de los precios.
    Usa P5-P95 para evitar outliers y agrega un margen.
    """
    import numpy as np
    precios = df["close"].values
    p5  = np.percentile(precios, 5)
    p95 = np.percentile(precios, 95)

    # Agregar margen
    rango = p95 - p5
    lower = round(p5 - rango * (margen_pct / 100), 2)
    upper = round(p95 + rango * (margen_pct / 100), 2)

    # No bajar de 0
    lower = max(lower, round(p5 * 0.9, 2))

    return lower, upper


# ══════════════════════════════════════════════════════════
#  GRID BACKTEST
# ══════════════════════════════════════════════════════════
def run_grid_backtest(cfg: GridConfig, dias: int, auto_range: bool = True):
    """Backtest del grid bot usando datos históricos."""
    from data.fetcher import obtener_historico_backtest

    print(f"\n{'='*60}")
    print(f"{'  GRID BACKTEST':^60}")
    print(f"{'='*60}")
    print(f"  Par:        {cfg.SYMBOL}")
    print(f"  Capital:    ${cfg.CAPITAL:,.2f} USDT")
    print(f"  Período:    {dias} días")
    print(f"  Cargando datos históricos...")

    # Descargar velas de 1h para el backtest
    df = obtener_historico_backtest(cfg.SYMBOL, "1h", dias=dias)
    if df is None or df.empty:
        print("  Error: no se pudieron obtener datos históricos.")
        return

    print(f"  {len(df)} velas | {df.index[0]} → {df.index[-1]}")

    # Auto-calcular rango si está habilitado
    if auto_range:
        cfg.LOWER, cfg.UPPER = calcular_rango_auto(df)
        print(f"  Auto-rango: ${cfg.LOWER:,.2f} — ${cfg.UPPER:,.2f} (P5-P95 + margen)")

    print(f"  Rango Grid: ${cfg.LOWER:,.2f} — ${cfg.UPPER:,.2f}")
    print(f"  Niveles:    {cfg.COUNT}")
    print(f"  Step:       ${(cfg.UPPER - cfg.LOWER) / cfg.COUNT:,.2f}")
    print(f"{'='*60}\n")

    # Crear grid bot en modo paper
    bot = GridBot(cfg, client=None, live=False)

    # Precio inicial
    precio_inicio = df["close"].iloc[0]
    print(f"  Precio inicio: ${precio_inicio:,.2f}")

    # Verificar rango
    precio_min = df["low"].min()
    precio_max = df["high"].max()
    print(f"  Rango histórico: ${precio_min:,.2f} — ${precio_max:,.2f}")

    if cfg.LOWER > precio_max or cfg.UPPER < precio_min:
        print(f"\n  ⚠️  El grid está completamente fuera del rango histórico.")
        print(f"  Ajustá GRID_LOWER y GRID_UPPER.\n")
        return

    # Setup inicial
    bot.setup_inicial(precio_inicio)

    # Estadísticas
    equity_curve = [{"t": str(df.index[0]), "v": cfg.CAPITAL}]
    max_invested = 0.0
    stopped = False
    trades_log = []

    # Recorrer barra a barra
    for i in range(1, len(df)):
        high = df["high"].iloc[i]
        low  = df["low"].iloc[i]
        close = df["close"].iloc[i]

        # Simular: el precio puede haber tocado varios niveles en una vela
        # Recorremos de bajo a alto (compras primero, ventas después)
        trades_antes = bot.total_trades

        # Primero verificar compras (precio bajó al nivel)
        bot.verificar_ejecuciones(low)
        # Después verificar ventas (precio subió al nivel)
        bot.verificar_ejecuciones(high)

        # Equity = USDT libre + valor holdings al cierre
        equity_total = bot.calcular_equity(close)
        equity_curve.append({"t": str(df.index[i]), "v": round(equity_total, 2)})

        # Track max invested
        valor_holdings = sum(close * l.qty for l in bot.levels if l.has_position)
        max_invested = max(max_invested, valor_holdings)

        # Log de trades nuevos
        if bot.total_trades > trades_antes:
            new_trades = bot.total_trades - trades_antes
            trades_log.append({
                "timestamp": str(df.index[i]),
                "precio": close,
                "trades_en_vela": new_trades,
                "profit_acum": round(bot.total_profit, 4),
            })

        # Verificar stop loss
        if bot.verificar_stop_loss(low):
            bot.cerrar_todo(close)
            stopped = True
            equity_curve.append({"t": str(df.index[i]), "v": round(bot.usdt_free, 2)})
            break

        # Verificar reposición
        if bot.verificar_reposition(high):
            bot.reposicionar_grid(close)

    # Si no paró por SL, cerrar posiciones abiertas al precio final
    if not stopped:
        precio_final = df["close"].iloc[-1]
        bot.cerrar_todo(precio_final)

    # ── Resultados ─────────────────────────────────────────
    precio_final = df["close"].iloc[-1]
    resumen = bot.resumen(precio_final)
    capital_final = bot.usdt_free  # After cerrar_todo, all is USDT
    ret_pct = (capital_final - cfg.CAPITAL) / cfg.CAPITAL * 100

    # Max drawdown
    peak = cfg.CAPITAL
    max_dd = 0
    for point in equity_curve:
        v = point["v"]
        if v > peak:
            peak = v
        dd = (peak - v) / peak * 100
        if dd > max_dd:
            max_dd = dd

    print(f"\n{'═'*60}")
    print(f"{'  GRID BACKTEST — RESULTADOS':^60}")
    print(f"{'═'*60}")
    print(f"  Par:              {cfg.SYMBOL}")
    print(f"  Período:          {df.index[0].date()} → {df.index[-1].date()}")
    print(f"  Rango Grid:       ${cfg.LOWER:,.2f} — ${cfg.UPPER:,.2f} ({cfg.COUNT} niveles, step ${bot.step:,.2f})")
    print(f"  Precio:           ${precio_inicio:,.2f} → ${precio_final:,.2f}")
    print(f"  Capital:          ${cfg.CAPITAL:,.2f} → ${capital_final:,.2f}")
    print(f"  Retorno:          {ret_pct:+.2f}%")
    print(f"  Trades:           {bot.total_trades}")
    print(f"  Profit grid:      ${bot.total_profit:+,.4f}")
    print(f"  Max Drawdown:     {max_dd:.2f}%")
    print(f"  Max en holdings:  ${max_invested:,.2f}")
    if stopped:
        print(f"  ⛔ Stop Loss activado durante el backtest")
    print(f"{'═'*60}")

    # Exportar resultados
    results = {
        "tipo": "grid_backtest",
        "symbol": cfg.SYMBOL,
        "timeframe": "1h",
        "fecha_inicio": str(df.index[0]),
        "fecha_fin": str(df.index[-1]),
        "dias": dias,
        "grid_upper": cfg.UPPER,
        "grid_lower": cfg.LOWER,
        "grid_count": cfg.COUNT,
        "grid_step": round((cfg.UPPER - cfg.LOWER) / cfg.COUNT, 2),
        "capital_inicial": cfg.CAPITAL,
        "capital_final": round(capital_final, 2),
        "retorno_pct": round(ret_pct, 2),
        "total_trades": bot.total_trades,
        "profit_realizado": round(bot.total_profit, 4),
        "max_drawdown_pct": round(max_dd, 2),
        "max_invested": round(max_invested, 2),
        "stopped": stopped,
        "equity_curve": equity_curve,
        "trades_log": trades_log,
    }

    output_dir = Path(__file__).resolve().parents[1] / "backtest" / "results"
    output_dir.mkdir(parents=True, exist_ok=True)
    fname = f"grid_{cfg.SYMBOL.lower()}_{cfg.COUNT}lv_{int(cfg.LOWER)}-{int(cfg.UPPER)}_{dias}d.json"
    path = output_dir / fname
    with open(path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2, default=str)

    print(f"\n  JSON exportado: {path}")
    print(f"  Equity curve: {len(equity_curve)} puntos\n")

    return results


# ══════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════
def main():
    args = parse_args()
    cfg = GridConfig()

    # Override desde CLI
    if args.symbol:  cfg.SYMBOL = args.symbol
    if args.upper:   cfg.UPPER = args.upper
    if args.lower:   cfg.LOWER = args.lower
    if args.count:   cfg.COUNT = args.count
    if args.capital: cfg.CAPITAL = args.capital

    # Auto-range: desactivar si se pasaron upper/lower explícitos o --no-auto-range
    use_auto_range = args.auto_range and not args.no_auto_range
    if args.upper or args.lower:
        use_auto_range = False

    # Modo backtest
    if args.backtest:
        run_grid_backtest(cfg, args.dias, auto_range=use_auto_range)
        return

    modo = "LIVE" if args.live else "PAPER"

    print(f"\n{'='*60}")
    print(f"{'  GRID BOT SPOT — ' + BOT_TAG:^60}")
    print(f"{'='*60}")
    print(f"  Bot:        #{BOT_NUMBER} ({BOT_TAG})")
    print(f"  Par:        {cfg.SYMBOL}")
    print(f"  Rango:      ${cfg.LOWER:,.2f} — ${cfg.UPPER:,.2f}")
    print(f"  Niveles:    {cfg.COUNT}")
    print(f"  Step:       ${(cfg.UPPER - cfg.LOWER) / cfg.COUNT:,.2f}")
    print(f"  Capital:    ${cfg.CAPITAL:,.2f} USDT")
    print(f"  Modo:       {modo}")
    print(f"  Testnet:    {'Sí' if cfg.TESTNET else '⚠️  NO — REAL'}")
    print(f"  Stop Loss:  {cfg.STOP_LOSS_PCT}% debajo del lower")
    print(f"  Max Loss:   ${cfg.MAX_LOSS_USD}")
    print(f"  Reposition: {'ON' if cfg.REPOSITION else 'OFF'}")
    print(f"  Check:      cada {cfg.CHECK_INTERVAL}s")
    print(f"{'='*60}\n")

    # ── Conectar Binance Spot ─────────────────────────────
    client = None
    if args.live:
        from binance.client import Client
        client = Client(cfg.API_KEY, cfg.API_SECRET, testnet=cfg.TESTNET)
        logger.info("Cliente Binance Spot conectado. Testnet=%s", cfg.TESTNET)

    # ── Auto-range en live: centrar grid alrededor del precio actual ──
    if use_auto_range and client:
        ticker = client.get_symbol_ticker(symbol=cfg.SYMBOL)
        precio_actual = float(ticker["price"])
        rango_pct = 15  # ±15% del precio
        cfg.LOWER = round(precio_actual * (1 - rango_pct / 100), 2)
        cfg.UPPER = round(precio_actual * (1 + rango_pct / 100), 2)
        logger.info("Auto-rango: $%.2f — $%.2f (±%d%% de $%.2f)",
                    cfg.LOWER, cfg.UPPER, rango_pct, precio_actual)

    print(f"  Rango:      ${cfg.LOWER:,.2f} — ${cfg.UPPER:,.2f}")
    print(f"  Auto-range: {'ON' if use_auto_range else 'OFF'}")

    # ── Telegram ──────────────────────────────────────────
    notifier = TelegramNotifier(
        token=cfg.TELEGRAM_TOKEN,
        chat_id=cfg.TELEGRAM_CHAT_ID,
        bot_tag=BOT_TAG,
    )

    # ── Crear Grid Bot ────────────────────────────────────
    bot = GridBot(cfg, client, live=args.live)

    # Obtener precio actual
    if client:
        precio = bot._get_precio_actual()
    else:
        # Paper mode: simular precio medio del rango
        precio = (cfg.UPPER + cfg.LOWER) / 2
        logger.info("PAPER mode: usando precio simulado $%.2f", precio)

    if precio <= 0:
        logger.error("No se pudo obtener precio. Abortando.")
        return

    # Verificar que el precio está dentro del rango
    if precio < cfg.LOWER or precio > cfg.UPPER:
        logger.warning("⚠️  Precio actual $%.2f fuera del rango [$%.2f - $%.2f]",
                      precio, cfg.LOWER, cfg.UPPER)

    # Setup inicial
    bot.setup_inicial(precio)

    # Notificar inicio
    notifier._enviar(
        f"🔲 *Grid Bot Iniciado*\n"
        f"{'─'*28}\n"
        f"Par:     `{cfg.SYMBOL}`\n"
        f"Rango:   `${cfg.LOWER} - ${cfg.UPPER}`\n"
        f"Niveles: `{cfg.COUNT}`\n"
        f"Capital: `${cfg.CAPITAL:,.2f} USDT`\n"
        f"Modo:    `{modo}`\n"
        f"🕐 `{datetime.now().strftime('%Y-%m-%d %H:%M')} Local`"
    )

    # ── Loop principal ────────────────────────────────────
    ciclo = 0
    last_summary = 0

    try:
        while bot.running:
            ciclo += 1
            time.sleep(cfg.CHECK_INTERVAL)

            # Obtener precio
            if client:
                precio = bot._get_precio_actual()
            else:
                # Paper: simular movimiento de precio
                import random
                precio += random.uniform(-5, 5)
                precio = max(cfg.LOWER * 0.9, min(cfg.UPPER * 1.1, precio))

            if precio <= 0:
                logger.warning("Precio no disponible, saltando ciclo.")
                continue

            # Verificar stop loss
            if bot.verificar_stop_loss(precio):
                bot.cerrar_todo(precio)
                resumen = bot.resumen(precio)
                notifier._enviar(
                    f"⛔ *Grid Bot — STOP LOSS*\n"
                    f"Precio: `${precio:,.2f}`\n"
                    f"Equity: `${resumen['equity']:,.2f} USDT`\n"
                    f"Trades: `{resumen['trades']}`"
                )
                break

            # Verificar max loss
            if bot.verificar_max_loss(precio):
                bot.cerrar_todo(precio)
                resumen = bot.resumen(precio)
                notifier._enviar(
                    f"⛔ *Grid Bot — MAX LOSS*\n"
                    f"Equity: `${resumen['equity']:,.2f} USDT`\n"
                    f"Trades: `{resumen['trades']}`"
                )
                break

            # Verificar reposición
            if bot.verificar_reposition(precio):
                bot.reposicionar_grid(precio)
                notifier._enviar(
                    f"🔄 *Grid Reposicionado*\n"
                    f"Nuevo rango: `${cfg.LOWER} - ${cfg.UPPER}`\n"
                    f"Precio: `${precio:,.2f}`"
                )

            # Verificar ejecuciones
            bot.verificar_ejecuciones(precio)

            # Exportar estado
            bot.exportar_estado()

            # Resumen periódico (cada 10 min)
            if time.time() - last_summary > 600:
                resumen = bot.resumen(precio)
                logger.info(
                    "Grid | $%.2f | Pos: %d | Buy: %d | Sell: %d | "
                    "Equity: $%.2f | Trades: %d",
                    precio, resumen["posiciones"], resumen["ordenes_buy"],
                    resumen["ordenes_sell"], resumen["equity"],
                    resumen["trades"]
                )
                last_summary = time.time()

    except KeyboardInterrupt:
        logger.info("Grid bot detenido por el usuario.")
        precio = bot._get_precio_actual() if client else precio
        bot.cerrar_todo(precio)

    # ── Resumen final ─────────────────────────────────────
    resumen = bot.resumen(precio)
    print(f"\n{'='*52}")
    print(f"{'  GRID BOT — RESUMEN FINAL':^52}")
    print(f"{'='*52}")
    print(f"  Trades completados: {resumen['trades']}")
    print(f"  Profit realizado:   ${resumen['profit_realizado']:+,.4f}")
    print(f"  Equity final:       ${resumen['equity']:,.2f}")
    print(f"  Retorno:            {resumen['retorno_pct']:+.2f}%")
    print(f"  Duración:           {resumen['horas']:.1f} horas")
    print(f"{'='*52}\n")

    notifier._enviar(
        f"🔴 *Grid Bot Detenido*\n"
        f"{'─'*28}\n"
        f"Trades: `{resumen['trades']}`\n"
        f"Equity: `${resumen['equity']:,.2f} USDT`\n"
        f"Retorno: `{resumen['retorno_pct']:+.2f}%`\n"
        f"Duración: `{resumen['horas']:.1f}h`\n"
        f"🕐 `{datetime.now().strftime('%Y-%m-%d %H:%M')} Local`"
    )


if __name__ == "__main__":
    main()
