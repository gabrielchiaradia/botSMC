"""
backtest/engine.py
──────────────────
Motor de backtesting barra-a-barra.
Simula ejecuciones reales sin lookahead bias.
Acepta un perfil de estrategia opcional para filtros adicionales.
"""

from __future__ import annotations
import json
import numpy as np
import pandas as pd
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

from config import backtest as bcfg, risk as rcfg, strategy as scfg
from strategy.indicators import calcular_atr, detectar_swings
from strategy.smc_signals import evaluar_señal, detectar_order_blocks
from strategy.risk import calcular_tamaño, validar_tamaño
from utils.logger import get_logger

logger = get_logger(__name__)


# ══════════════════════════════════════════════════════════
#  ESTRUCTURAS
# ══════════════════════════════════════════════════════════

@dataclass
class TradeRecord:
    id:             int
    timestamp_in:   str
    timestamp_out:  str   = ""
    direccion:      str   = ""
    precio_entrada: float = 0.0
    precio_salida:  float = 0.0
    stop_loss:      float = 0.0
    stop_loss_orig: float = 0.0     # SL original (para calcular dist_1r)
    take_profit:    float = 0.0
    tamaño:         float = 0.0
    tamaño_activo:  float = 0.0     # Para cierre parcial (híbrido)
    pnl_usd:        float = 0.0
    pnl_pct:        float = 0.0
    resultado:      str   = ""
    duracion_min:   int   = 0
    score_señal:    int   = 0
    motivos:        list  = field(default_factory=list)
    capital_post:   float = 0.0
    sesion:         str   = ""
    atr_entrada:    float = 0.0
    r_maximo:       float = 0.0     # Máximo R alcanzado durante el trade
    trailing_mode:  str   = ""      # Modo trailing usado
    # Estado trailing per-posición
    _dist_1r:        float = 0.0
    _escalon_actual: int   = 0
    _parcial_cerrado:bool  = False
    _pnl_parcial:    float = 0.0
    _mejor_precio:   float = 0.0


@dataclass
class BacktestStats:
    symbol:             str
    timeframe:          str
    fecha_inicio:       str
    fecha_fin:          str
    capital_inicial:    float
    capital_final:      float
    perfil:             str   = "base"
    perfil_descripcion: str   = ""
    trailing_mode:      str   = "none"       # none | escalones | atr | hibrido
    tp_rr_ratio:        float = 0.0          # RR configurado (1:X)
    dias:               int   = 0            # Días de backtest
    max_open_trades:    int   = 1            # Trades simultáneos configurados
    total_trades:       int   = 0
    wins:               int   = 0
    losses:             int   = 0
    win_rate:           float = 0.0
    profit_factor:      float = 0.0
    max_drawdown_pct:   float = 0.0
    max_drawdown_usd:   float = 0.0
    sharpe_ratio:       float = 0.0
    avg_win_usd:        float = 0.0
    avg_loss_usd:       float = 0.0
    avg_duracion_min:   float = 0.0
    mejor_trade_usd:    float = 0.0
    peor_trade_usd:     float = 0.0
    avg_rr_real:        float = 0.0          # RR promedio real logrado
    max_r_capturado:    float = 0.0          # Máximo R capturado en un trade
    racha_wins:         int   = 0
    racha_losses:       int   = 0
    trades:             list  = field(default_factory=list)
    equity_curve:       list  = field(default_factory=list)
    trades_por_sesion:  dict  = field(default_factory=dict)
    trades_por_hora:    dict  = field(default_factory=dict)
    señales_filtradas:  int   = 0
    motivos_filtro:     dict  = field(default_factory=dict)  # {motivo: count}


# ══════════════════════════════════════════════════════════
#  MOTOR PRINCIPAL
# ══════════════════════════════════════════════════════════

class BacktestEngine:
    """
    Ejecuta backtesting barra-a-barra con gestión de posición,
    comisiones y métricas completas.

    Modos de trailing dinámico (trailing_mode):

      "none"       → TP/SL fijos. Comportamiento original.

      "escalones"  → Cada vez que el precio avanza 1R a favor,
                     el SL sube 1R. El TP se elimina: el trade
                     solo cierra por trailing SL. Captura 3R, 4R, 5R...
                     Ejemplo LONG entrada $100, SL $98 (dist=2):
                       Precio llega a $102 → SL sube a $100 (BE)
                       Precio llega a $104 → SL sube a $102 (+1R)
                       Precio llega a $106 → SL sube a $104 (+2R)
                       Precio revierte a $104 → cierra en $104 = +2R

      "atr"        → El SL sigue al precio a N×ATR de distancia.
                     Sin TP fijo. Captura tendencias largas pero
                     devuelve algo de ganancia al cierre.

      "hibrido"    → Cierra 50% en TP original (2R). El otro 50%
                     queda corriendo con trailing por escalones sin
                     límite de TP. Mejor de ambos mundos: asegura
                     ganancia + captura tendencia.
    """

    # Barras por año según timeframe (para Sharpe)
    BARRAS_POR_AÑO = {
        "1m": 525600, "5m": 105120, "15m": 35040,
        "30m": 17520, "1h": 8760, "4h": 2190,
    }

    # Modos de trailing válidos
    TRAILING_MODES = ("none", "escalones", "atr", "hibrido")

    def __init__(self, df: pd.DataFrame, capital: float = None,
                 symbol: str = "BTCUSDT", timeframe: str = "15m",
                 perfil=None, cooldown_barras: int = 3,
                 trailing_mode: str = "none",
                 atr_trailing_mult: float = 2.0,
                 hibrido_cierre_pct: float = 0.5,
                 max_open_trades: int = 1,
                 racha_sl_reduce: int = 3):
        self.df        = df.copy()
        self.capital0  = capital or bcfg.CAPITAL_INICIO
        self.symbol    = symbol
        self.timeframe = timeframe
        self.perfil    = perfil

        # Validar trailing mode
        if trailing_mode not in self.TRAILING_MODES:
            raise ValueError(
                f"trailing_mode '{trailing_mode}' inválido. "
                f"Opciones: {self.TRAILING_MODES}"
            )
        self.trailing_mode     = trailing_mode
        self.cooldown_barras   = cooldown_barras
        self.atr_trailing_mult = atr_trailing_mult
        self.hibrido_cierre_pct = hibrido_cierre_pct

        # Multi-posición y riesgo adaptativo
        self.max_open_trades   = max_open_trades    # Máx trades simultáneos
        self.racha_sl_reduce   = racha_sl_reduce    # Tras N SL seguidos, reducir a 1 trade

        # Pre-calcular indicadores una sola vez
        self.atr    = calcular_atr(df)
        self.swings = detectar_swings(df)

        # Estado interno
        self._capital           = self.capital0
        self._posiciones: list[TradeRecord] = []
        self._trades:   list[TradeRecord] = []
        self._equity:   list[dict]        = []
        self._trade_id          = 0
        self._señales_filtradas = 0
        self._motivos_filtro: dict = {}   # {motivo: count}
        self._cooldown_restante = 0
        self._perdida_dia       = 0.0
        self._dia_actual        = None

        # Riesgo adaptativo
        self._racha_sl          = 0    # SL consecutivos actuales
        self._max_trades_actual = max_open_trades  # Se reduce con racha de SL

    # ── Ejecución ──────────────────────────────────────────

    def run(self) -> BacktestStats:
        """Ejecuta el backtest completo. Retorna estadísticas."""
        nombre_perfil = self.perfil.nombre if self.perfil else "base"
        logger.info("Backtest: %s %s | %d velas | $%.0f | perfil=%s | "
                    "trailing=%s | cooldown=%d | daily_loss=%.1f%% | "
                    "max_trades=%d | racha_reduce=%d",
                    self.symbol, self.timeframe, len(self.df),
                    self.capital0, nombre_perfil,
                    self.trailing_mode, self.cooldown_barras,
                    rcfg.MAX_DAILY_LOSS * 100,
                    self.max_open_trades, self.racha_sl_reduce)

        warmup = max(scfg.SWING_LENGTH * 2 + 20, 50)
        self._equity.append({"t": str(self.df.index[0]), "v": self.capital0})

        for i in range(warmup, len(self.df)):
            self._log_progreso(i)
            self._actualizar_dia(i)
            # Gestionar TODAS las posiciones abiertas
            self._gestionar_posiciones_abiertas(i)
            if not self._capital_suficiente():
                break
            # Cooldown: esperar N velas después de un SL
            if self._cooldown_restante > 0:
                self._cooldown_restante -= 1
                continue
            # Límite de pérdida diaria
            if not self._dentro_limite_diario():
                continue
            # Solo buscar entrada si hay espacio para más trades
            if len(self._posiciones) < self._max_trades_actual:
                self._buscar_entrada(i)

        # Cerrar posiciones abiertas al final
        for pos in list(self._posiciones):
            self._cerrar_posicion(pos, len(self.df) - 1, motivo="fin_periodo")

        stats = self._calcular_stats()
        logger.info("Backtest listo: %d trades | WR: %.1f%% | Capital: $%.2f | Filtradas: %d",
                    stats.total_trades, stats.win_rate,
                    stats.capital_final, stats.señales_filtradas)
        return stats

    # ── Gestión de posiciones (multi) ──────────────────────

    def _gestionar_posiciones_abiertas(self, i: int):
        """Gestiona TP/SL/trailing de TODAS las posiciones abiertas."""
        high = self.df["high"].iloc[i]
        low  = self.df["low"].iloc[i]

        # Iterar sobre copia porque podemos cerrar durante el loop
        for pos in list(self._posiciones):
            # Actualizar mejor precio
            if pos.direccion == "LONG":
                pos._mejor_precio = max(pos._mejor_precio, high)
            else:
                pos._mejor_precio = min(pos._mejor_precio, low)

            # Calcular R actual
            if pos._dist_1r > 0:
                if pos.direccion == "LONG":
                    r_actual = (pos._mejor_precio - pos.precio_entrada) / pos._dist_1r
                else:
                    r_actual = (pos.precio_entrada - pos._mejor_precio) / pos._dist_1r
                pos.r_maximo = max(pos.r_maximo, r_actual)

            # Aplicar trailing según modo
            if self.trailing_mode == "escalones":
                self._trailing_escalones(pos, high, low)
            elif self.trailing_mode == "atr":
                self._trailing_atr(pos, high, low, i)
            elif self.trailing_mode == "hibrido":
                self._trailing_hibrido(pos, high, low, i)

            # Evaluar cierre
            hit_tp = hit_sl = False
            if pos.direccion == "LONG":
                hit_sl = low  <= pos.stop_loss
                hit_tp = high >= pos.take_profit if pos.take_profit > 0 else False
            else:
                hit_sl = high >= pos.stop_loss
                hit_tp = low  <= pos.take_profit if pos.take_profit > 0 else False

            # Resolución conservadora
            if hit_sl and hit_tp:
                self._cerrar_posicion(pos, i, motivo="sl")
            elif hit_tp:
                self._cerrar_posicion(pos, i, motivo="tp")
            elif hit_sl:
                self._cerrar_posicion(pos, i, motivo="sl")

    # ── TRAILING: Escalones de R ───────────────────────────

    def _trailing_escalones(self, pos: TradeRecord, high: float, low: float):
        if pos._dist_1r <= 0:
            return
        pos.take_profit = 0.0

        if pos.direccion == "LONG":
            avance = high - pos.precio_entrada
        else:
            avance = pos.precio_entrada - low

        nuevo_escalon = int(avance / pos._dist_1r)

        if nuevo_escalon > pos._escalon_actual:
            pos._escalon_actual = nuevo_escalon
            offset = (nuevo_escalon - 1) * pos._dist_1r
            if pos.direccion == "LONG":
                nuevo_sl = pos.precio_entrada + offset
                pos.stop_loss = round(max(pos.stop_loss, nuevo_sl), 2)
            else:
                nuevo_sl = pos.precio_entrada - offset
                pos.stop_loss = round(min(pos.stop_loss, nuevo_sl), 2)

    # ── TRAILING: ATR dinámico ─────────────────────────────

    def _trailing_atr(self, pos: TradeRecord, high: float, low: float, i: int):
        atr_val = self.atr.iloc[i]
        if pd.isna(atr_val) or atr_val <= 0:
            return
        pos.take_profit = 0.0
        distancia = atr_val * self.atr_trailing_mult

        if pos.direccion == "LONG":
            nuevo_sl = pos._mejor_precio - distancia
            if nuevo_sl > pos.stop_loss:
                pos.stop_loss = round(nuevo_sl, 2)
        else:
            nuevo_sl = pos._mejor_precio + distancia
            if nuevo_sl < pos.stop_loss:
                pos.stop_loss = round(nuevo_sl, 2)

    # ── TRAILING: Híbrido (parcial + escalones) ────────────

    def _trailing_hibrido(self, pos: TradeRecord, high: float, low: float, i: int):
        if pos._dist_1r <= 0:
            return

        if not pos._parcial_cerrado:
            hit_tp_parcial = False
            if pos.direccion == "LONG":
                hit_tp_parcial = high >= pos.take_profit and pos.take_profit > 0
            else:
                hit_tp_parcial = low <= pos.take_profit and pos.take_profit > 0

            if hit_tp_parcial:
                tamaño_cerrar = pos.tamaño * self.hibrido_cierre_pct
                tamaño_restante = pos.tamaño - tamaño_cerrar

                if pos.direccion == "LONG":
                    pnl_parcial = (pos.take_profit - pos.precio_entrada) * tamaño_cerrar
                else:
                    pnl_parcial = (pos.precio_entrada - pos.take_profit) * tamaño_cerrar

                comision_parcial = tamaño_cerrar * pos.take_profit * bcfg.COMISION_PCT
                pos._pnl_parcial = pnl_parcial - comision_parcial
                pos.tamaño_activo = round(tamaño_restante, 6)
                pos._parcial_cerrado = True
                pos.stop_loss = pos.precio_entrada
                pos.take_profit = 0.0
                pos._escalon_actual = 1
                return

        if pos._parcial_cerrado:
            self._trailing_escalones(pos, high, low)

    # ── Cerrar posición ────────────────────────────────────

    def _cerrar_posicion(self, pos: TradeRecord, i: int, motivo: str):
        precio_cierre = {
            "tp": pos.take_profit,
            "sl": pos.stop_loss,
        }.get(motivo, self.df["close"].iloc[i])

        if precio_cierre == 0.0:
            precio_cierre = self.df["close"].iloc[i]

        tamaño_cierre = pos.tamaño_activo if pos.tamaño_activo > 0 else pos.tamaño

        if pos._parcial_cerrado:
            comision_entrada = tamaño_cierre * pos.precio_entrada * bcfg.COMISION_PCT
        else:
            comision_entrada = pos.tamaño * pos.precio_entrada * bcfg.COMISION_PCT
        comision_salida  = tamaño_cierre * precio_cierre * bcfg.COMISION_PCT
        comision_total   = comision_entrada + comision_salida

        if pos.direccion == "LONG":
            pnl = (precio_cierre - pos.precio_entrada) * tamaño_cierre - comision_total
        else:
            pnl = (pos.precio_entrada - precio_cierre) * tamaño_cierre - comision_total

        pnl += pos._pnl_parcial

        self._capital = max(0.01, self._capital + pnl)
        self._perdida_dia += pnl

        ts_in  = pd.Timestamp(pos.timestamp_in)
        ts_out = self.df.index[i]
        dur    = int((ts_out - ts_in).total_seconds() / 60)

        pos.timestamp_out  = str(ts_out)
        pos.precio_salida  = round(precio_cierre, 2)
        pos.pnl_usd        = round(pnl, 4)
        capital_antes = self._capital - pnl  # capital antes de sumar el PnL
        pos.pnl_pct        = round(pnl / capital_antes * 100, 3) if capital_antes > 0 else 0.0
        pos.resultado      = "WIN" if pnl > 0 else "LOSS"
        pos.duracion_min   = dur
        pos.capital_post   = round(self._capital, 2)
        pos.trailing_mode  = self.trailing_mode

        self._trades.append(pos)
        self._equity.append({"t": str(ts_out), "v": round(self._capital, 2)})

        # Remover de posiciones abiertas
        if pos in self._posiciones:
            self._posiciones.remove(pos)

        # Riesgo adaptativo: tracking de racha SL
        if motivo == "sl":
            self._racha_sl += 1
            if self._racha_sl >= self.racha_sl_reduce:
                self._max_trades_actual = 1
                logger.debug("Racha %d SL: reduciendo a 1 trade máximo", self._racha_sl)
            if self.cooldown_barras > 0:
                self._cooldown_restante = self.cooldown_barras
        else:
            # WIN o TP: resetear racha y restaurar max trades
            self._racha_sl = 0
            self._max_trades_actual = self.max_open_trades

        logger.debug("Trade #%d: %s $%.2f | PnL: $%+.2f | R máx: %.1f | mode: %s | open: %d",
                     pos.id, pos.resultado, precio_cierre, pnl,
                     pos.r_maximo, self.trailing_mode, len(self._posiciones))

    def _registrar_filtro(self, motivo: str):
        """Registra un motivo de filtro para estadísticas."""
        self._señales_filtradas += 1
        # Simplificar motivo para agrupar
        clave = motivo.split("(")[0].strip().split(":")[0].strip()
        self._motivos_filtro[clave] = self._motivos_filtro.get(clave, 0) + 1

    def _buscar_entrada(self, i: int):
        # 1. Evaluar señal SMC base
        señal = evaluar_señal(self.df, self.swings, self.atr, i)
        if not señal.tiene_señal:
            return

        # 1b. Filtro ventana horaria — en backtest se descarta
        if señal.fuera_de_horario:
            self._registrar_filtro("Fuera de horario")
            return

        # 2. Aplicar perfil (filtros adicionales)
        if self.perfil is not None:
            from strategy.profiles.base_profile import FilterContext
            ctx = FilterContext(
                df      = self.df,
                idx     = i,
                señal   = señal,
                swings  = self.swings,
                atr     = self.atr,
                precio  = self.df["close"].iloc[i]
            )
            pasa, motivos_filtro = self.perfil.apply(ctx)
            if not pasa:
                motivo = motivos_filtro[-1] if motivos_filtro else "Perfil"
                self._registrar_filtro(motivo)
                logger.debug("Señal filtrada por perfil '%s': %s",
                             self.perfil.nombre, motivo)
                return
            señal.motivos.extend(
                m for m in motivos_filtro
                if m and m not in señal.motivos
            )

        # 3. Evitar duplicar entrada en la misma vela
        dir_nueva = "LONG" if señal.direccion == "ALCISTA" else "SHORT"
        timestamp_actual = str(self.df.index[i])
        for pos_abierta in self._posiciones:
            if pos_abierta.timestamp_in == timestamp_actual:
                return  # Ya se abrió un trade en esta vela

        # 4. Calcular tamaño y abrir posición
        tamaño = calcular_tamaño(self._capital, señal.precio_entrada, señal.stop_loss)
        if not validar_tamaño(tamaño, señal.precio_entrada):
            return

        self._trade_id += 1
        dist_1r = abs(señal.precio_entrada - señal.stop_loss)

        pos = TradeRecord(
            id             = self._trade_id,
            timestamp_in   = str(self.df.index[i]),
            direccion      = dir_nueva,
            precio_entrada = señal.precio_entrada,
            precio_salida  = 0.0,
            stop_loss      = señal.stop_loss,
            stop_loss_orig = señal.stop_loss,
            take_profit    = señal.take_profit,
            tamaño         = tamaño,
            tamaño_activo  = tamaño,
            score_señal    = señal.score,
            motivos        = señal.motivos,
            sesion         = señal.sesion,
            atr_entrada    = round(señal.atr, 2),
            trailing_mode  = self.trailing_mode,
            # Estado trailing per-posición
            _dist_1r       = dist_1r,
            _escalon_actual = 0,
            _parcial_cerrado = False,
            _pnl_parcial   = 0.0,
            _mejor_precio  = señal.precio_entrada,
        )
        self._posiciones.append(pos)

    # ── Helpers ────────────────────────────────────────────

    def _capital_suficiente(self) -> bool:
        return self._capital > self.capital0 * 0.05

    def _actualizar_dia(self, i: int):
        """Reset de pérdida diaria cuando cambia el día."""
        ts = self.df.index[i]
        dia = ts.date() if hasattr(ts, 'date') else pd.Timestamp(ts).date()
        if dia != self._dia_actual:
            self._dia_actual = dia
            self._perdida_dia = 0.0

    def _dentro_limite_diario(self) -> bool:
        """Verifica que no se excedió el límite de pérdida diaria."""
        if self._capital <= 0:
            return False
        perdida_pct = abs(self._perdida_dia) / self._capital
        if self._perdida_dia < 0 and perdida_pct >= rcfg.MAX_DAILY_LOSS:
            return False
        return True

    def _log_progreso(self, i: int):
        if i % 500 == 0:
            pct = i / len(self.df) * 100
            logger.debug("Progreso: %.1f%% | Capital: $%.2f | Filtradas: %d",
                         pct, self._capital, self._señales_filtradas)

    # ── Estadísticas ───────────────────────────────────────

    def _calcular_stats(self) -> BacktestStats:
        nombre_perfil = self.perfil.nombre       if self.perfil else "base"
        desc_perfil   = self.perfil.descripcion  if self.perfil else "Estrategia base SMC"

        trades = self._trades
        if not trades:
            return BacktestStats(
                symbol=self.symbol, timeframe=self.timeframe,
                fecha_inicio=str(self.df.index[0]),
                fecha_fin=str(self.df.index[-1]),
                capital_inicial=self.capital0,
                capital_final=round(self._capital, 2),
                perfil=nombre_perfil,
                perfil_descripcion=desc_perfil,
                señales_filtradas=self._señales_filtradas,
                equity_curve=self._equity,
            )

        wins   = [t for t in trades if t.resultado == "WIN"]
        losses = [t for t in trades if t.resultado == "LOSS"]
        pnls   = [t.pnl_usd for t in trades]

        bruto_w = sum(t.pnl_usd for t in wins)
        bruto_l = abs(sum(t.pnl_usd for t in losses))
        pf      = round(bruto_w / bruto_l, 3) if bruto_l > 0 else float("inf")

        eq_vals  = [e["v"] for e in self._equity]
        peak, dd = self.capital0, 0.0
        for v in eq_vals:
            peak = max(peak, v)
            dd   = max(dd, (peak - v) / peak * 100)

        mean_p = np.mean(pnls)
        std_p  = np.std(pnls)
        # Anualizar según el timeframe real, no asumir datos diarios
        barras_año = self.BARRAS_POR_AÑO.get(self.timeframe, 35040)
        sharpe = round((mean_p / std_p * np.sqrt(barras_año)) if std_p > 0 else 0.0, 3)

        mw = ml = cw = cl = 0
        for t in trades:
            if t.resultado == "WIN":
                cw += 1; cl = 0
            else:
                cl += 1; cw = 0
            mw = max(mw, cw); ml = max(ml, cl)

        por_sesion: dict = {}
        for t in trades:
            s = t.sesion or "off"
            if s not in por_sesion:
                por_sesion[s] = {"total": 0, "wins": 0, "pnl": 0.0}
            por_sesion[s]["total"] += 1
            if t.resultado == "WIN": por_sesion[s]["wins"] += 1
            por_sesion[s]["pnl"] += t.pnl_usd

        por_hora: dict = {}
        for t in trades:
            h = str(pd.Timestamp(t.timestamp_in).hour)
            if h not in por_hora:
                por_hora[h] = {"total": 0, "wins": 0, "pnl": 0.0}
            por_hora[h]["total"] += 1
            if t.resultado == "WIN": por_hora[h]["wins"] += 1
            por_hora[h]["pnl"] += t.pnl_usd

        # RR real promedio y máximo R capturado
        r_maximos = [t.r_maximo for t in trades if t.r_maximo > 0]
        avg_rr = round(np.mean(r_maximos), 2) if r_maximos else 0.0
        max_r  = round(max(r_maximos), 2) if r_maximos else 0.0

        return BacktestStats(
            symbol              = self.symbol,
            timeframe           = self.timeframe,
            fecha_inicio        = str(self.df.index[0]),
            fecha_fin           = str(self.df.index[-1]),
            capital_inicial     = self.capital0,
            capital_final       = round(self._capital, 2),
            perfil              = nombre_perfil,
            perfil_descripcion  = desc_perfil,
            trailing_mode       = self.trailing_mode,
            tp_rr_ratio         = rcfg.TP_RR_RATIO,
            dias                = (self.df.index[-1] - self.df.index[0]).days,
            max_open_trades     = self.max_open_trades,
            total_trades        = len(trades),
            wins                = len(wins),
            losses              = len(losses),
            win_rate            = round(len(wins)/len(trades)*100, 2),
            profit_factor       = pf,
            max_drawdown_pct    = round(dd, 2),
            max_drawdown_usd    = round(max(eq_vals) - min(eq_vals), 2),
            sharpe_ratio        = sharpe,
            avg_win_usd         = round(bruto_w / len(wins), 2)   if wins   else 0,
            avg_loss_usd        = round(-bruto_l / len(losses), 2) if losses else 0,
            avg_duracion_min    = round(np.mean([t.duracion_min for t in trades]), 1),
            mejor_trade_usd     = round(max(pnls), 2),
            peor_trade_usd      = round(min(pnls), 2),
            avg_rr_real         = avg_rr,
            max_r_capturado     = max_r,
            racha_wins          = mw,
            racha_losses        = ml,
            trades              = [asdict(t) for t in trades],
            equity_curve        = self._equity,
            trades_por_sesion   = por_sesion,
            trades_por_hora     = por_hora,
            señales_filtradas   = self._señales_filtradas,
            motivos_filtro      = dict(self._motivos_filtro),
        )


# ══════════════════════════════════════════════════════════
#  EXPORTAR
# ══════════════════════════════════════════════════════════

def exportar_json(stats: BacktestStats, nombre: str = None) -> Path:
    """Guarda resultados en JSON. Nombre default incluye el perfil."""
    if nombre is None:
        nombre = f"backtest_{stats.perfil}.json"
    bcfg.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = bcfg.OUTPUT_DIR / nombre
    with open(path, "w", encoding="utf-8") as f:
        json.dump(asdict(stats), f, ensure_ascii=False, indent=2, default=str)
    logger.info("Exportado: %s", path)
    return path


def exportar_comparacion(resultados: list[BacktestStats],
                          nombre: str = "comparacion.json") -> Path:
    """
    Exporta múltiples resultados en un solo JSON para el dashboard
    de comparación. Formato especial que el dashboard detecta por
    la clave 'comparacion': true.
    """
    bcfg.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = bcfg.OUTPUT_DIR / nombre

    data = {
        "comparacion": True,
        "fecha":       str(pd.Timestamp.now()),
        "perfiles":    [asdict(r) for r in resultados],
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)
    logger.info("Comparación exportada: %s (%d perfiles)", path, len(resultados))
    return path
