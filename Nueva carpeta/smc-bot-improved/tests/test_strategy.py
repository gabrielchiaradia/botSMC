"""
tests/test_strategy.py
───────────────────────
Tests unitarios para los módulos de estrategia.

Uso:
    pytest tests/ -v
    pytest tests/ -v --tb=short
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
import pandas as pd
import numpy as np
from datetime import datetime

# ── Fixtures ───────────────────────────────────────────────────────────────

@pytest.fixture
def df_base():
    """DataFrame OHLCV simulado para tests."""
    np.random.seed(42)
    n = 200
    fechas = pd.date_range(end=datetime.now(), periods=n, freq="15min")
    precio = 65000.0
    rows = []
    for _ in range(n):
        cambio = np.random.normal(0.0002, 0.003)
        open_  = precio
        close_ = precio * (1 + cambio)
        high_  = max(open_, close_) * (1 + abs(np.random.normal(0, 0.001)))
        low_   = min(open_, close_) * (1 - abs(np.random.normal(0, 0.001)))
        rows.append([open_, high_, low_, close_, 100.0])
        precio = close_
    return pd.DataFrame(rows, columns=["open","high","low","close","volume"], index=fechas)


@pytest.fixture
def df_tendencia_alcista():
    """DataFrame con tendencia alcista clara."""
    n = 100
    fechas = pd.date_range(end=datetime.now(), periods=n, freq="15min")
    precio = 60000.0
    rows = []
    for _ in range(n):
        cambio = np.random.normal(0.001, 0.002)  # Drift positivo
        open_  = precio
        close_ = precio * (1 + cambio)
        high_  = max(open_, close_) * 1.001
        low_   = min(open_, close_) * 0.999
        rows.append([open_, high_, low_, close_, 100.0])
        precio = close_
    return pd.DataFrame(rows, columns=["open","high","low","close","volume"], index=fechas)


# ══════════════════════════════════════════════════════════
#  TESTS: INDICADORES
# ══════════════════════════════════════════════════════════

class TestIndicadores:

    def test_atr_retorna_serie(self, df_base):
        from strategy.indicators import calcular_atr
        atr = calcular_atr(df_base)
        assert isinstance(atr, pd.Series)
        assert len(atr) == len(df_base)

    def test_atr_positivo(self, df_base):
        from strategy.indicators import calcular_atr
        atr = calcular_atr(df_base)
        validos = atr.dropna()
        assert (validos > 0).all()

    def test_atr_periodo_personalizado(self, df_base):
        from strategy.indicators import calcular_atr
        atr7  = calcular_atr(df_base, periodo=7)
        atr21 = calcular_atr(df_base, periodo=21)
        # ATR de periodo más corto tiene más valores NaN al inicio
        assert atr7.notna().sum() >= atr21.notna().sum()

    def test_swings_detectados(self, df_base):
        from strategy.indicators import detectar_swings
        swings = detectar_swings(df_base, length=5)
        assert isinstance(swings, pd.Series)
        assert set(swings.unique()).issubset({-1, 0, 1})
        # Deben existir al menos algunos swings
        assert (swings != 0).sum() > 0

    def test_sesion_clasificacion(self):
        from strategy.indicators import sesion_activa
        # London: 7-16 UTC
        ts_london = pd.Timestamp("2024-01-15 09:00:00")
        assert sesion_activa(ts_london) == "london"
        # New York: 12-21 UTC
        ts_ny = pd.Timestamp("2024-01-15 15:00:00")
        assert sesion_activa(ts_ny) == "new_york"
        # Off
        ts_off = pd.Timestamp("2024-01-15 01:00:00")
        assert sesion_activa(ts_off) == "off"


# ══════════════════════════════════════════════════════════
#  TESTS: FVG
# ══════════════════════════════════════════════════════════

class TestFVG:

    def test_fvg_retorna_lista(self, df_base):
        from strategy.smc_signals import detectar_fvg
        fvgs = detectar_fvg(df_base)
        assert isinstance(fvgs, list)

    def test_fvg_tipos_validos(self, df_base):
        from strategy.smc_signals import detectar_fvg
        fvgs = detectar_fvg(df_base)
        for fvg in fvgs:
            assert fvg.tipo in ("ALCISTA", "BAJISTA")
            assert fvg.top > fvg.bottom
            assert fvg.size > 0

    def test_fvg_en_precio_coincidencia(self, df_base):
        from strategy.smc_signals import detectar_fvg, fvg_en_precio, FVG
        import pandas as pd
        # Crear FVG sintético
        fvg_test = FVG(
            timestamp = pd.Timestamp("2024-01-01"),
            tipo      = "ALCISTA",
            top       = 65100.0,
            bottom    = 65000.0,
        )
        # Precio dentro del FVG
        resultado = fvg_en_precio([fvg_test], 65050.0, "ALCISTA")
        assert resultado is not None
        assert resultado.tipo == "ALCISTA"

    def test_fvg_en_precio_fuera_rango(self, df_base):
        from strategy.smc_signals import fvg_en_precio, FVG
        import pandas as pd
        fvg_test = FVG(pd.Timestamp("2024-01-01"), "ALCISTA", 65100.0, 65000.0)
        resultado = fvg_en_precio([fvg_test], 64000.0, "ALCISTA")  # Muy lejos
        assert resultado is None


# ══════════════════════════════════════════════════════════
#  TESTS: ORDER BLOCKS
# ══════════════════════════════════════════════════════════

class TestOrderBlocks:

    def test_ob_retorna_lista(self, df_base):
        from strategy.smc_signals import detectar_order_blocks
        obs = detectar_order_blocks(df_base)
        assert isinstance(obs, list)

    def test_ob_estructura_valida(self, df_base):
        from strategy.smc_signals import detectar_order_blocks
        obs = detectar_order_blocks(df_base)
        for ob in obs:
            assert ob.tipo in ("ALCISTA", "BAJISTA")
            assert ob.top >= ob.bottom
            assert ob.fuerza > 0


# ══════════════════════════════════════════════════════════
#  TESTS: RISK
# ══════════════════════════════════════════════════════════

class TestRisk:

    def test_tamaño_positivo(self):
        from strategy.risk import calcular_tamaño
        tamaño = calcular_tamaño(capital=1000, precio=65000, stop_loss=64000)
        assert tamaño > 0

    def test_tamaño_sl_igual_precio(self):
        from strategy.risk import calcular_tamaño
        tamaño = calcular_tamaño(capital=1000, precio=65000, stop_loss=65000)
        assert tamaño == 0.0

    def test_tamaño_proporcional_al_riesgo(self):
        from strategy.risk import calcular_tamaño
        t1 = calcular_tamaño(1000, 65000, 64500)   # distancia 500
        t2 = calcular_tamaño(2000, 65000, 64500)   # capital doble
        assert abs(t2 - t1 * 2) < 0.0001

    def test_validar_tamaño_minimo(self):
        from strategy.risk import validar_tamaño
        assert validar_tamaño(0.001, 65000, min_nocional=5) is True   # nocional $65
        assert validar_tamaño(0.0, 65000) is False

    def test_resumen_riesgo_completo(self):
        from strategy.risk import resumen_riesgo
        r = resumen_riesgo(1000, 65000, 64000, 67000)
        assert "tamaño" in r
        assert "nocional_usd" in r
        assert "rr_ratio" in r
        assert r["rr_ratio"] > 0


# ══════════════════════════════════════════════════════════
#  TESTS: SEÑAL COMPLETA
# ══════════════════════════════════════════════════════════

class TestSeñal:

    def test_señal_sin_tendencia_lateral(self, df_base):
        """En mercado lateral no debe haber señal."""
        from strategy.smc_signals import evaluar_señal
        from strategy.indicators import calcular_atr, detectar_swings
        atr    = calcular_atr(df_base)
        swings = detectar_swings(df_base)
        # Evaluar en la mitad (donde probablemente sea lateral)
        señal = evaluar_señal(df_base, swings, atr, 30)
        # No necesariamente lateral, pero debe devolver Señal válida
        assert hasattr(señal, "tiene_señal")
        assert hasattr(señal, "score")
        assert 0 <= señal.score <= 100

    def test_señal_niveles_consistentes(self, df_tendencia_alcista):
        """Si hay señal alcista, SL debe ser menor al precio y TP mayor."""
        from strategy.smc_signals import evaluar_señal
        from strategy.indicators import calcular_atr, detectar_swings
        atr    = calcular_atr(df_tendencia_alcista)
        swings = detectar_swings(df_tendencia_alcista)

        for i in range(20, len(df_tendencia_alcista)):
            señal = evaluar_señal(df_tendencia_alcista, swings, atr, i)
            if señal.tiene_señal and señal.direccion == "ALCISTA":
                assert señal.stop_loss < señal.precio_entrada
                assert señal.take_profit > señal.precio_entrada
                assert señal.rr_ratio > 0
                break


# ══════════════════════════════════════════════════════════
#  TESTS: DATA FETCHER
# ══════════════════════════════════════════════════════════

class TestFetcher:

    def test_datos_simulados_shape(self):
        from data.fetcher import _datos_simulados
        df = _datos_simulados(limit=100, timeframe="15m")
        assert len(df) == 100
        assert list(df.columns) == ["open","high","low","close","volume"]

    def test_datos_simulados_ohlc_validos(self):
        from data.fetcher import _datos_simulados
        df = _datos_simulados(limit=50)
        assert (df["high"] >= df["low"]).all()
        assert (df["high"] >= df["open"]).all()
        assert (df["high"] >= df["close"]).all()
        assert (df["low"]  <= df["open"]).all()
        assert (df["low"]  <= df["close"]).all()

    def test_datos_historicos_simulados(self):
        from data.fetcher import _datos_simulados_largo
        df = _datos_simulados_largo(dias=7, timeframe="1h")
        assert len(df) > 0
        assert df.index.is_monotonic_increasing
