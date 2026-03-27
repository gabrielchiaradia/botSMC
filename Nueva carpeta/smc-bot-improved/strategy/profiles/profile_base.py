"""
strategy/profiles/profile_base.py
───────────────────────────────────
Perfil BASE — la estrategia actual sin filtros adicionales.

Es exactamente lo que el bot hacía antes de los perfiles:
  - FVG en dirección de tendencia  → +35 pts
  - OB en dirección de tendencia   → +40 pts
  - BOS confirmado                 → +25 pts
  - Score ≥ SCORE_MINIMO           → señal válida

No agrega ningún filtro extra. Sirve como baseline para comparar
contra perfiles más restrictivos.
"""

from strategy.profiles.base_profile import BaseProfile, FilterContext, FilterResult


class ProfileBase(BaseProfile):

    @property
    def nombre(self) -> str:
        return "base"

    @property
    def descripcion(self) -> str:
        return "Estrategia base SMC: FVG + OB + BOS sin filtros adicionales"

    def filtros(self) -> list:
        # Sin filtros extra — la señal SMC core ya fue evaluada
        return []
