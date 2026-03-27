"""
strategy/profiles/base_profile.py
───────────────────────────────────
Clase base que define la interfaz que todo perfil de estrategia
debe implementar.

Un perfil es un conjunto de filtros y reglas que se aplican
ADEMÁS de la lógica SMC base. El motor de backtest y el bot en
vivo reciben un perfil y lo aplican sin saber qué filtros contiene.

Para crear un nuevo perfil:
    1. Crear un archivo en strategy/profiles/mi_perfil.py
    2. Heredar de BaseProfile
    3. Implementar los métodos marcados como abstractos
    4. Registrarlo en strategy/profiles/__init__.py
"""

from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
import pandas as pd

from strategy.smc_signals import Señal, OrderBlock


# ══════════════════════════════════════════════════════════
#  CONTEXTO DE EVALUACIÓN
# ══════════════════════════════════════════════════════════

@dataclass
class FilterContext:
    """
    Todo lo que un filtro puede necesitar para tomar decisiones.
    Se construye una vez por vela y se pasa a cada filtro.
    """
    df:         pd.DataFrame    # DataFrame LTF (velas principales)
    idx:        int             # Índice de la vela actual
    señal:      Señal           # Señal base (ya evaluada por SMC core)
    swings:     pd.Series       # Swing highs/lows detectados
    atr:        pd.Series       # Serie ATR calculada

    # Opcionales: disponibles si el perfil los necesita
    df_htf:     pd.DataFrame = field(default_factory=pd.DataFrame)  # Velas HTF
    obs_full:   list = field(default_factory=list)   # OBs detectados en la ventana
    precio:     float = 0.0
    
    @property
    def precio(self) -> float:
        return float(self.df["close"].iloc[self.idx])

    @property
    def timestamp(self) -> pd.Timestamp:
        return self.df.index[self.idx]

    @property
    def atr_val(self) -> float:
        v = self.atr.iloc[self.idx]
        return float(v) if not pd.isna(v) else 0.0


# ══════════════════════════════════════════════════════════
#  RESULTADO DE FILTRO
# ══════════════════════════════════════════════════════════

@dataclass
class FilterResult:
    """
    Resultado que cada filtro retorna.
    Si pasa=False, la señal se descarta con el motivo indicado.
    Si pasa=True, se puede agregar bonus de score.
    """
    pasa:   bool
    motivo: str  = ""    # Descripción legible del resultado
    bonus:  int  = 0     # Puntos extra al score si pasa


# ══════════════════════════════════════════════════════════
#  PERFIL BASE (abstracto)
# ══════════════════════════════════════════════════════════

class BaseProfile(ABC):
    """
    Clase base para todos los perfiles de estrategia.

    Cada perfil define:
      - nombre: identificador único (ej. "base", "ob_bos", "ema_filter")
      - descripcion: texto legible para reportes
      - filtros: lista de métodos que se ejecutan en orden

    El motor de backtest llama a apply(ctx) por cada vela.
    Si algún filtro retorna pasa=False, la señal se descarta.
    """

    @property
    @abstractmethod
    def nombre(self) -> str:
        """Identificador único del perfil. Ej: 'ob_bos'"""
        ...

    @property
    @abstractmethod
    def descripcion(self) -> str:
        """Descripción legible para reportes y dashboard."""
        ...

    def apply(self, ctx: FilterContext) -> tuple[bool, list[str]]:
        """
        Aplica todos los filtros del perfil en orden.

        Returns:
            (pasa, motivos) donde:
            - pasa: True si la señal sobrevive todos los filtros
            - motivos: lista de strings describiendo qué pasó
        """
        motivos = []
        for filtro in self.filtros():
            result = filtro(ctx)
            if result.motivo:
                motivos.append(result.motivo)
            if not result.pasa:
                return False, motivos
            if result.bonus:
                ctx.señal.score += result.bonus
                motivos.append(f"Bonus +{result.bonus}pts: {result.motivo}")
        return True, motivos

    @abstractmethod
    def filtros(self) -> list:
        """
        Retorna lista de callables en orden de aplicación.
        Cada callable recibe FilterContext y retorna FilterResult.

        Ejemplo:
            return [self.filtro_sesion, self.filtro_ob_bos]
        """
        ...

    def necesita_htf(self) -> bool:
        """Retorna True si el perfil necesita datos HTF para operar."""
        return False

    def __repr__(self) -> str:
        return f"<Profile:{self.nombre}>"
