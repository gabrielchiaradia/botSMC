"""
utils/trade_journal.py
───────────────────────
Registro persistente de señales detectadas y operaciones ejecutadas.

Genera dos archivos JSON por sesión del bot:
  - logs/signals_YYYYMMDD.json   → toda señal detectada (opere o no)
  - logs/trades_YYYYMMDD.json    → operaciones con entrada, salida y PnL

Ambos archivos son acumulativos dentro del mismo día.
Al abrir el dashboard puedes cargar el trades_*.json para
revisar lo que operó el bot en vivo, igual que el backtest.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from utils.logger import get_logger

logger = get_logger(__name__)


# ══════════════════════════════════════════════════════════
#  ESTRUCTURAS
# ══════════════════════════════════════════════════════════

@dataclass
class SignalLog:
    """Registro de una señal evaluada (con o sin entrada)."""
    timestamp:      str
    symbol:         str
    timeframe:      str
    precio:         float
    tendencia:      str
    sesion:         str
    score:          int
    tiene_señal:    bool
    direccion:      str
    stop_loss:      float
    take_profit:    float
    atr:            float
    motivos:        list
    accion:         str        # "ENTRADA" | "IGNORADA" | "SIN_SEÑAL" | "TAMAÑO_INVALIDO"
    modo:           str        # "LIVE" | "PAPER"
    capital:        float
    tamaño:         float = 0.0
    riesgo_usd:     float = 0.0
    rr_ratio:       float = 0.0
    orden_id:       str   = ""


@dataclass
class TradeLog:
    """Registro completo de una operación (entrada → salida)."""
    id:             str        # YYYYMMDD_HHmmss_SYMBOL
    symbol:         str
    timeframe:      str
    modo:           str        # "LIVE" | "PAPER"

    # Entrada
    timestamp_in:   str
    direccion:      str        # LONG | SHORT
    precio_entrada: float
    stop_loss:      float
    take_profit:    float
    tamaño:         float
    capital_in:     float
    score_señal:    int
    motivos:        list
    sesion:         str
    atr_entrada:    float
    orden_id_in:    str = ""

    # Salida (se rellena al cerrar)
    timestamp_out:  str   = ""
    precio_salida:  float = 0.0
    pnl_usd:        float = 0.0
    pnl_pct:        float = 0.0
    resultado:      str   = ""   # "WIN" | "LOSS" | "ABIERTO" | "CANCELADO"
    duracion_min:   int   = 0
    capital_out:    float = 0.0
    motivo_cierre:  str   = ""   # "TP" | "SL" | "MANUAL" | "FIN_SESION"
    orden_id_out:   str   = ""


# ══════════════════════════════════════════════════════════
#  JOURNAL PRINCIPAL
# ══════════════════════════════════════════════════════════

class TradeJournal:
    """
    Gestiona el registro de señales y trades en disco.
    Soporta múltiples trades abiertos simultáneamente.
    """

    def __init__(self, log_dir: Path = None, symbol: str = "BTCUSDT",
                 timeframe: str = "15m", modo: str = "PAPER",
                 max_open: int = 1, bot_tag: str = ""):
        from config.settings import logs as lcfg, risk as rcfg, BOT_NUMBER
        self.log_dir    = log_dir or lcfg.LOG_DIR
        self.symbol     = symbol
        self.timeframe  = timeframe
        self.modo       = modo
        self.max_open   = max_open or rcfg.MAX_OPEN_TRADES
        self.bot_tag    = bot_tag
        self._bot_num   = BOT_NUMBER
        self.log_dir.mkdir(parents=True, exist_ok=True)

        self._fecha           = datetime.now().strftime("%Y%m%d")
        self._trades_abiertos: list[TradeLog] = []
        self._trade_counter   = self._contar_trades_hoy()
        self._racha_sl        = 0

        # Restaurar trades abiertos que quedaron en disco al reiniciar
        self._restaurar_trades_abiertos()

        logger.info("[%s] TradeJournal iniciado | Modo: %s | Max open: %d | Dir: %s",
                    bot_tag or "Bot1", modo, self.max_open, self.log_dir)

    # ── Paths de archivos ──────────────────────────────────

    @property
    def _bot_suffix(self) -> str:
        """Sufijo para archivos: '' para bot 1, '_2' para bot 2, etc."""
        return f"_{self._bot_num}" if self._bot_num > 1 else ""

    @property
    def _signals_path(self) -> Path:
        return self.log_dir / f"signals{self._bot_suffix}_{self._fecha}.json"

    @property
    def _trades_path(self) -> Path:
        return self.log_dir / f"trades{self._bot_suffix}_{self._fecha}.json"

    # ── API pública ────────────────────────────────────────

    def registrar_señal(self, señal, capital: float, accion: str,
                        tamaño: float = 0.0, riesgo_usd: float = 0.0,
                        rr_ratio: float = 0.0, orden_id: str = "") -> SignalLog:
        """
        Registra cada señal evaluada, se opere o no.

        Args:
            señal:      Objeto Señal de strategy/smc_signals.py
            capital:    Balance en el momento de la señal
            accion:     "ENTRADA" | "PAPER_ENTRADA" | "IGNORADA" |
                        "SIN_SEÑAL" | "TAMAÑO_INVALIDO"
            tamaño:     Unidades base calculadas
            riesgo_usd: Riesgo en USDT
            rr_ratio:   Ratio R:R calculado
            orden_id:   ID de orden de Binance (si se ejecutó)
        """
        log = SignalLog(
            timestamp   = datetime.now().isoformat(),
            symbol      = self.symbol,
            timeframe   = self.timeframe,
            precio      = señal.precio_entrada,
            tendencia   = señal.tendencia,
            sesion      = señal.sesion,
            score       = señal.score,
            tiene_señal = señal.tiene_señal,
            direccion   = señal.direccion,
            stop_loss   = señal.stop_loss,
            take_profit = señal.take_profit,
            atr         = señal.atr,
            motivos     = señal.motivos,
            accion      = accion,
            modo        = self.modo,
            capital     = capital,
            tamaño      = tamaño,
            riesgo_usd  = riesgo_usd,
            rr_ratio    = rr_ratio,
            orden_id    = orden_id,
        )
        self._append_json(self._signals_path, asdict(log))
        logger.debug("Señal registrada: %s score=%d accion=%s",
                     señal.direccion or "—", señal.score, accion)
        return log

    def abrir_trade(self, señal, tamaño: float, capital: float,
                    orden_id: str = "") -> TradeLog:
        """
        Registra la apertura de una operación.
        Guarda el trade como ABIERTO hasta que se llame cerrar_trade().
        """
        self._trade_counter += 1
        trade_id = (f"{self._fecha}_"
                    f"{datetime.now().strftime('%H%M%S')}_"
                    f"{self.symbol}")

        trade = TradeLog(
            id             = trade_id,
            symbol         = self.symbol,
            timeframe      = self.timeframe,
            modo           = self.modo,
            timestamp_in   = datetime.now().isoformat(),
            direccion      = "LONG" if señal.direccion == "ALCISTA" else "SHORT",
            precio_entrada = señal.precio_entrada,
            stop_loss      = señal.stop_loss,
            take_profit    = señal.take_profit,
            tamaño         = tamaño,
            capital_in     = capital,
            score_señal    = señal.score,
            motivos        = señal.motivos,
            sesion         = señal.sesion,
            atr_entrada    = señal.atr,
            resultado      = "ABIERTO",
            orden_id_in    = orden_id,
        )
        self._trades_abiertos.append(trade)
        self._guardar_trade(trade)

        logger.info("Trade abierto: %s %s @ $%.2f | SL $%.2f | TP $%.2f | #%s | Open: %d/%d",
                    trade.direccion, self.symbol, trade.precio_entrada,
                    trade.stop_loss, trade.take_profit, trade_id,
                    len(self._trades_abiertos), self.max_open)
        return trade

    def registrar_posicion_externa(self, pos_binance: dict, capital: float) -> TradeLog:
        """
        Registra una posición abierta en Binance que no fue abierta por el bot.
        Útil para trades manuales o posiciones huérfanas tras un crash/reinicio.
        SL y TP quedan en 0.0 — se deben configurar manualmente si se desea.
        """
        from types import SimpleNamespace
        amt       = float(pos_binance["positionAmt"])
        direccion = "ALCISTA" if amt > 0 else "BAJISTA"

        senal = SimpleNamespace(
            direccion      = direccion,
            precio_entrada = float(pos_binance["entryPrice"]),
            stop_loss      = 0.0,
            take_profit    = 0.0,
            score          = 0,
            motivos        = ["Posición externa — registrada al iniciar bot"],
            sesion         = "unknown",
            atr            = 0.0,
            tiene_señal    = True,
            tendencia      = direccion,
        )
        trade = self.abrir_trade(senal, abs(amt), capital, orden_id="EXTERNAL")
        logger.warning(
            "[%s] ⚠️  Posición externa registrada: %s %.3f @ $%.2f | SL/TP no configurados.",
            self.bot_tag,
            "LONG" if amt > 0 else "SHORT",
            abs(amt),
            float(pos_binance["entryPrice"]),
        )
        return trade

    def cerrar_trade(self, precio_salida: float, capital_out: float,
                     motivo_cierre: str = "MANUAL",
                     orden_id_out: str = "",
                     trade: TradeLog = None) -> Optional[TradeLog]:
        """
        Cierra un trade específico (o el primero abierto) y calcula PnL.
        """
        if trade is None:
            if not self._trades_abiertos:
                logger.warning("cerrar_trade() llamado sin trades abiertos.")
                return None
            trade = self._trades_abiertos[0]

        t = trade
        ts_in  = datetime.fromisoformat(t.timestamp_in)
        ts_out = datetime.now()
        dur    = int((ts_out - ts_in).total_seconds() / 60)

        if t.direccion == "LONG":
            pnl = (precio_salida - t.precio_entrada) * t.tamaño
        else:
            pnl = (t.precio_entrada - precio_salida) * t.tamaño

        pnl_pct   = (pnl / t.capital_in * 100) if t.capital_in > 0 else 0
        resultado = "WIN" if pnl > 0 else "LOSS"

        t.timestamp_out  = ts_out.isoformat()
        t.precio_salida  = round(precio_salida, 2)
        t.pnl_usd        = round(pnl, 4)
        t.pnl_pct        = round(pnl_pct, 3)
        t.resultado      = resultado
        t.duracion_min   = dur
        # capital_out = capital al abrir + PnL de este trade
        # Usamos capital_in del trade para no depender de balance_ref en memoria
        # (que puede estar desactualizado si el container se reinició)
        t.capital_out    = round(t.capital_in + pnl, 2)
        t.motivo_cierre  = motivo_cierre
        t.orden_id_out   = orden_id_out

        self._guardar_trade(t, actualizar=True)

        # Remover de la lista de abiertos
        if t in self._trades_abiertos:
            self._trades_abiertos.remove(t)

        # Racha de SL: si 3 consecutivos, reducir max_open a 1
        if motivo_cierre == "SL":
            self._racha_sl += 1
            if self._racha_sl >= 3:
                self.max_open = 1
                logger.warning("Racha %d SL: reduciendo a 1 trade máximo", self._racha_sl)
        else:
            self._racha_sl = 0
            # Restaurar max_open original al ganar
            from config.settings import risk as rcfg
            self.max_open = rcfg.MAX_OPEN_TRADES

        logger.info(
            "Trade cerrado: %s %s @ $%.2f | PnL: $%+.2f (%.2f%%) | %s | %dmin | Open: %d/%d",
            resultado, t.direccion, precio_salida,
            pnl, pnl_pct, motivo_cierre, dur,
            len(self._trades_abiertos), self.max_open
        )
        return t

    def hay_trade_abierto(self) -> bool:
        return len(self._trades_abiertos) > 0

    def trades_abiertos(self) -> list[TradeLog]:
        """Retorna lista de todos los trades abiertos."""
        return list(self._trades_abiertos)

    def num_trades_abiertos(self) -> int:
        return len(self._trades_abiertos)

    def puede_abrir_trade(self) -> bool:
        """True si hay espacio para más trades."""
        return len(self._trades_abiertos) < self.max_open

    def tiene_direccion_abierta(self, direccion: str) -> bool:
        """True si ya hay un trade abierto en esa dirección."""
        return any(t.direccion == direccion for t in self._trades_abiertos)

    def trade_abierto(self) -> Optional[TradeLog]:
        """Retrocompatibilidad: retorna el primer trade abierto."""
        return self._trades_abiertos[0] if self._trades_abiertos else None

    # ── Consultas ──────────────────────────────────────────

    def leer_trades_hoy(self) -> list[dict]:
        return self._leer_json(self._trades_path)

    def leer_señales_hoy(self) -> list[dict]:
        return self._leer_json(self._signals_path)

    def resumen_hoy(self) -> dict:
        """Retorna estadísticas rápidas del día en curso."""
        trades = [t for t in self.leer_trades_hoy()
                  if t.get("resultado") not in ("ABIERTO", "CANCELADO")]
        if not trades:
            return {"trades": 0, "wins": 0, "losses": 0,
                    "pnl_usd": 0.0, "win_rate": 0.0}

        wins   = [t for t in trades if t["resultado"] == "WIN"]
        losses = [t for t in trades if t["resultado"] == "LOSS"]
        pnl    = sum(t["pnl_usd"] for t in trades)
        wr     = len(wins) / len(trades) * 100

        return {
            "trades":   len(trades),
            "wins":     len(wins),
            "losses":   len(losses),
            "pnl_usd":  round(pnl, 2),
            "win_rate": round(wr, 1),
        }

    def recuperar_capital_hoy(self, capital_default: float) -> float:
        """
        Al reiniciar, recupera el capital acumulado leyendo el último
        capital_out de los trades cerrados de hoy o ayer.
        Si no hay trades cerrados, retorna capital_default.
        """
        from datetime import timedelta
        fechas = [
            datetime.now().strftime("%Y%m%d"),
            (datetime.now() - timedelta(days=1)).strftime("%Y%m%d"),
        ]
        for fecha in fechas:
            path = self.log_dir / f"trades{self._bot_suffix}_{fecha}.json"
            trades = self._leer_json(path)
            cerrados = [t for t in trades
                if t.get("resultado") not in ("ABIERTO", "CANCELADO", "")
                and t.get("capital_out", 0) > 0]
            if cerrados:
                capital_recuperado = cerrados[-1]["capital_out"]
                logger.info("[%s] Capital recuperado del journal (%s): $%.2f",
                        self.bot_tag, fecha, capital_recuperado)
                return capital_recuperado

        return capital_default

    def exportar_posiciones_abiertas(self) -> dict:
        """Exporta las posiciones abiertas actuales como dict para JSON."""
        posiciones = []
        for t in self._trades_abiertos:
            posiciones.append({
                "id":             t.id,
                "symbol":         t.symbol,
                "timeframe":      t.timeframe,
                "direccion":      t.direccion,
                "precio_entrada": t.precio_entrada,
                "stop_loss":      t.stop_loss,
                "take_profit":    t.take_profit,
                "tamaño":         t.tamaño,
                "capital_in":     t.capital_in,
                "score_señal":    t.score_señal,
                "sesion":         t.sesion,
                "timestamp_in":   t.timestamp_in,
                "atr_entrada":    t.atr_entrada,
                "modo":           t.modo,
            })
        return {
            "bot_tag":       self.bot_tag,
            "bot_num":       self._bot_num,
            "symbol":        self.symbol,
            "timeframe":     self.timeframe,
            "modo":          self.modo,
            "num_abiertas":  len(posiciones),
            "max_trades":    self.max_open,
            "posiciones":    posiciones,
            "updated_at":    datetime.now().isoformat(),
        }

    def exportar_como_backtest(self) -> dict:
        trades_raw = self.leer_trades_hoy()
        if not trades_raw:
            return {}

        trades_cerrados = [t for t in trades_raw
                           if t.get("resultado") not in ("ABIERTO", "CANCELADO", "")]

        wins   = [t for t in trades_cerrados if t["resultado"] == "WIN"]
        losses = [t for t in trades_cerrados if t["resultado"] == "LOSS"]
        pnls   = [t["pnl_usd"] for t in trades_cerrados]

        import numpy as np
        capital0      = trades_cerrados[0]["capital_in"] if trades_cerrados else 1000.0
        capital_final = trades_cerrados[-1]["capital_out"] if trades_cerrados else capital0
        retorno_pct   = round((capital_final - capital0) / capital0 * 100, 2) if capital0 > 0 else 0

        # Equity curve
        if trades_cerrados:
            equity = [{"t": trades_cerrados[0]["timestamp_in"], "v": capital0}]
            for t in trades_cerrados:
                equity.append({"t": t["timestamp_out"], "v": t["capital_out"]})
        else:
            equity = [{"t": datetime.now().isoformat(), "v": capital0}]

        # Por sesión
        por_sesion: dict = {}
        for t in trades_cerrados:
            s = t.get("sesion", "off")
            if s not in por_sesion:
                por_sesion[s] = {"total": 0, "wins": 0, "pnl": 0.0}
            por_sesion[s]["total"] += 1
            if t["resultado"] == "WIN":
                por_sesion[s]["wins"] += 1
            por_sesion[s]["pnl"] += t["pnl_usd"]

        # Por hora
        por_hora: dict = {}
        for t in trades_cerrados:
            try:
                h = str(datetime.fromisoformat(t["timestamp_in"]).hour)
            except Exception:
                h = "0"
            if h not in por_hora:
                por_hora[h] = {"total": 0, "wins": 0, "pnl": 0.0}
            por_hora[h]["total"] += 1
            if t["resultado"] == "WIN":
                por_hora[h]["wins"] += 1
            por_hora[h]["pnl"] += t["pnl_usd"]

        bruto_w = sum(t["pnl_usd"] for t in wins)
        bruto_l = abs(sum(t["pnl_usd"] for t in losses))
        pf      = round(bruto_w / bruto_l, 3) if bruto_l > 0 else 0

        # Agrega número de id para la tabla
        trades_export = []
        for i, t in enumerate(trades_cerrados, 1):
            t_copy = dict(t)
            t_copy["id"] = i
            t_copy["pnl_pct"] = t_copy.get("pnl_pct", 0)
            trades_export.append(t_copy)

        return {
            # ── Campos top-level (compatibles con backtest dashboard) ──
            "symbol":            self.symbol,
            "timeframe":         self.timeframe,
            "fecha_inicio":      trades_cerrados[0]["timestamp_in"] if trades_cerrados else "",
            "fecha_fin":         trades_cerrados[-1]["timestamp_out"] if trades_cerrados else "",
            "capital_inicial":   capital0,
            "capital_final":     round(capital_final, 2),
            "retorno_pct":       retorno_pct,
            "total_trades":      len(trades_cerrados),
            "wins":              len(wins),
            "losses":            len(losses),
            "win_rate":          round(len(wins)/len(trades_cerrados)*100, 2) if trades_cerrados else 0,
            "profit_factor":     pf,
            "max_drawdown_pct":  0,
            "max_drawdown_usd":  0,
            "sharpe_ratio":      0,
            "avg_win_usd":       round(bruto_w/len(wins), 2) if wins else 0,
            "avg_loss_usd":      round(-bruto_l/len(losses), 2) if losses else 0,
            "avg_duracion_min":  round(np.mean([t["duracion_min"] for t in trades_cerrados]), 1) if trades_cerrados else 0,
            "mejor_trade_usd":   round(max(pnls), 2) if pnls else 0,
            "peor_trade_usd":    round(min(pnls), 2) if pnls else 0,
            "racha_wins":        0,
            "racha_losses":      0,
            "trades":            trades_export,
            "equity_curve":      equity,
            "trades_por_sesion": por_sesion,
            "trades_por_hora":   por_hora,
            "fuente":            f"bot_en_vivo_{self.modo}",
            # ── Bloque summary (compatible con live dashboard renderLiveKPIs) ──
            "summary": {
                "symbol":          self.symbol,
                "timeframe":       self.timeframe,
                "bot":             self.bot_tag,
                "total":           len(trades_cerrados),
                "wins":            len(wins),
                "losses":          len(losses),
                "winrate":         round(len(wins)/len(trades_cerrados)*100, 2) if trades_cerrados else 0,
                "profit_factor":   pf,
                "capital_inicial": capital0,
                "capital_final":   round(capital_final, 2),
                "balance_actual":  round(capital_final, 2),
                "retorno_pct":     retorno_pct,
                "max_drawdown":    0,
            },
        }

    # ── I/O interno ────────────────────────────────────────

    def _restaurar_trades_abiertos(self):
        """
        Al iniciar, recarga desde disco los trades que quedaron ABIERTOS.
        Busca en el archivo de hoy y también en el de ayer, por si el trade
        se abrió antes de medianoche o el bot corrió sin reiniciarse varios días.
        """
        fechas = [
            self._fecha,
            (datetime.now() - timedelta(days=1)).strftime("%Y%m%d"),
        ]
        restaurados = 0
        for fecha in fechas:
            path = self.log_dir / f"trades{self._bot_suffix}_{fecha}.json"
            data = self._leer_json(path)
            for t in data:
                if t.get("resultado") == "ABIERTO":
                    try:
                        trade = TradeLog(**t)
                        self._trades_abiertos.append(trade)
                        restaurados += 1
                    except Exception as e:
                        logger.warning("No se pudo restaurar trade %s: %s", t.get("id"), e)
            if restaurados:
                break  # Si encontró trades hoy, no buscar en ayer

        if restaurados:
            logger.warning(
                "[%s] ⚠️  %d trade(s) ABIERTO(s) restaurados desde disco al reiniciar.",
                self.bot_tag, restaurados
            )

    def _append_json(self, path: Path, entry: dict):
        """Agrega un entry al array JSON del archivo."""
        data = self._leer_json(path)
        data.append(entry)
        self._escribir_json(path, data)

    def _guardar_trade(self, trade: TradeLog, actualizar: bool = False):
        """Guarda o actualiza un trade en el archivo JSON."""
        data = self._leer_json(self._trades_path)
        if actualizar:
            # Buscar y reemplazar por id
            for i, t in enumerate(data):
                if t.get("id") == trade.id:
                    data[i] = asdict(trade)
                    break
            else:
                data.append(asdict(trade))
        else:
            data.append(asdict(trade))
        self._escribir_json(self._trades_path, data)

    def _leer_json(self, path: Path) -> list:
        if not path.exists():
            return []
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return []

    def _escribir_json(self, path: Path, data: list):
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2, default=str)
        except OSError as e:
            logger.error("Error escribiendo %s: %s", path, e)

    def _contar_trades_hoy(self) -> int:
        return len(self._leer_json(self._trades_path))
