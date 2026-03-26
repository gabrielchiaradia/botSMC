"""
strategy/profiles/profile_ob_bos.py
─────────────────────────────────────
Perfil OB+BOS — Order Block validado por Break of Structure previo.

Filtro adicional sobre el perfil base:
  Un Order Block solo es válido si el movimiento impulsivo que lo
  generó rompió un swing previo (BOS). Sin ese BOS, el OB es solo
  una vela con movimiento fuerte, no una zona institucional real.

Lógica:
  1. Si la señal tiene OB como confluencia → verificar que ese OB
     esté precedido por un BOS en la misma dirección
  2. Si no hay OB en la señal → dejar pasar (FVG + BOS siguen valiendo)
  3. Si hay OB pero sin BOS previo → descartar y registrar motivo

Esto reduce la cantidad de señales pero mejora significativamente
la calidad de los OBs que sí pasan.
"""

from __future__ import annotations
import pandas as pd

from strategy.profiles.base_profile import BaseProfile, FilterContext, FilterResult
from strategy.smc_signals import detectar_order_blocks, detectar_bos
from strategy.indicators import detectar_swings
from utils.logger import get_logger

logger = get_logger(__name__)


class ProfileObBos(BaseProfile):

    @property
    def nombre(self) -> str:
        return "ob_bos"

    @property
    def descripcion(self) -> str:
        return "OB validado por BOS: solo acepta Order Blocks precedidos por ruptura de estructura"

    def filtros(self) -> list:
        return [self.filtro_ob_precedido_por_bos]

    # ══════════════════════════════════════════════════════
    #  FILTRO PRINCIPAL
    # ══════════════════════════════════════════════════════

    def filtro_ob_precedido_por_bos(self, ctx: FilterContext) -> FilterResult:
        """
        Verifica que si hay un OB en la señal, ese OB haya sido
        precedido por un BOS en la misma dirección.

        Algoritmo:
          1. Detectar OBs en la ventana de lookback
          2. Para cada OB del tipo correcto, buscar si hubo un BOS
             ANTES del OB (en las velas previas al OB)
          3. Si el OB que está tocando el precio tiene BOS previo → pasa
          4. Si el OB no tiene BOS previo → descartado
        """
        señal = ctx.señal

        # Si la señal no tiene OB como motivo, no aplicamos el filtro
        # (FVG + BOS siguen siendo válidos por sí solos)
        tiene_ob = any("OB" in m or "ob" in m.lower() for m in señal.motivos)
        if not tiene_ob:
            return FilterResult(
                pasa=True,
                motivo="Sin OB en señal — filtro OB+BOS no aplica"
            )

        # Buscar OBs válidos con BOS previo
        df       = ctx.df
        idx      = ctx.idx
        precio   = ctx.precio
        tipo_ob  = "ALCISTA" if señal.direccion == "ALCISTA" else "BAJISTA"

        # Calcular swings para detectar BOS
        swings = ctx.swings

        # Obtener OBs de la ventana
        from config import strategy as scfg
        ventana = df.iloc[max(0, idx - scfg.OB_LOOKBACK): idx + 1]
        obs = detectar_order_blocks(ventana)

        # Filtrar OBs del tipo correcto que estén cerca del precio
        margen = precio * 0.003
        obs_relevantes = [
            ob for ob in obs
            if ob.tipo == tipo_ob
            and ob.bottom - margen <= precio <= ob.top + margen
        ]

        if not obs_relevantes:
            # No hay OB relevante cerca — la señal ya no debería tener
            # OB en motivos si llegamos acá, pero lo dejamos pasar
            return FilterResult(pasa=True, motivo="Sin OB relevante cerca del precio")

        # Para cada OB relevante, verificar si tuvo BOS previo
        for ob in obs_relevantes:
            # Encontrar el índice del OB en el DataFrame completo
            try:
                ob_pos = df.index.get_loc(ob.timestamp)
            except KeyError:
                # El timestamp del OB puede estar en la ventana recortada
                # Buscamos el más cercano
                diferencias = abs(df.index - ob.timestamp)
                ob_pos = diferencias.argmin()

            if ob_pos < 5:
                continue  # No hay suficiente historia antes del OB

            # Buscar BOS ANTES del OB (en las velas previas)
            bos_previo = self._buscar_bos_previo_al_ob(
                df, swings, ob_pos, tipo_ob
            )

            if bos_previo:
                logger.debug(
                    "OB+BOS validado: OB en %s precedido por BOS en vela %d",
                    ob.timestamp, bos_previo
                )
                return FilterResult(
                    pasa=True,
                    motivo=f"OB validado: BOS previo confirmado (vela {bos_previo})",
                    bonus=10  # Bonus por calidad del OB
                )

        # Ningún OB relevante tiene BOS previo
        return FilterResult(
            pasa=False,
            motivo="OB rechazado: ningún OB cercano tiene BOS previo en su dirección"
        )

    # ══════════════════════════════════════════════════════
    #  HELPER: buscar BOS previo al OB
    # ══════════════════════════════════════════════════════

    def _buscar_bos_previo_al_ob(
        self,
        df:       pd.DataFrame,
        swings:   pd.Series,
        ob_pos:   int,
        tipo_ob:  str,
        lookback: int = 30
    ) -> int | None:
        """
        Busca si hubo un BOS en dirección tipo_ob en las `lookback`
        velas ANTERIORES al OB.

        Un BOS ocurre cuando el precio cierra por encima de un swing
        high previo (alcista) o por debajo de un swing low previo (bajista).

        Returns:
            El índice de la vela donde ocurrió el BOS, o None si no hay.
        """
        inicio = max(0, ob_pos - lookback)

        # Obtener swings previos al OB
        swings_previos = swings.iloc[inicio:ob_pos]

        if tipo_ob == "ALCISTA":
            # BOS alcista: precio cierra por encima de un swing high previo
            swing_highs = [
                (i, df["high"].iloc[inicio + i])
                for i, v in enumerate(swings_previos)
                if v == 1
            ]
            if not swing_highs:
                return None

            ultimo_sh = swing_highs[-1][1]

            # Buscar vela entre el swing high y el OB que haya cerrado
            # por encima del swing high
            for j in range(swing_highs[-1][0] + 1, ob_pos - inicio):
                idx_real = inicio + j
                if idx_real >= len(df):
                    break
                if df["close"].iloc[idx_real] > ultimo_sh:
                    return idx_real

        else:  # BAJISTA
            # BOS bajista: precio cierra por debajo de un swing low previo
            swing_lows = [
                (i, df["low"].iloc[inicio + i])
                for i, v in enumerate(swings_previos)
                if v == -1
            ]
            if not swing_lows:
                return None

            ultimo_sl = swing_lows[-1][1]

            for j in range(swing_lows[-1][0] + 1, ob_pos - inicio):
                idx_real = inicio + j
                if idx_real >= len(df):
                    break
                if df["close"].iloc[idx_real] < ultimo_sl:
                    return idx_real

        return None
