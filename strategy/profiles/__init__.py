"""
strategy/profiles/__init__.py
──────────────────────────────
Registro central de perfiles disponibles.

Para agregar un nuevo perfil:
  1. Crear strategy/profiles/profile_mi_perfil.py
  2. Heredar de BaseProfile e implementar nombre, descripcion, filtros()
  3. Agregar una entrada en PERFILES abajo
  4. Listo — el backtest y el bot lo reconocen automáticamente
"""

from strategy.profiles.base_profile       import BaseProfile, FilterContext, FilterResult
from strategy.profiles.profile_base       import ProfileBase
from strategy.profiles.profile_ob_bos     import ProfileObBos
from strategy.profiles.profile_ema_filter import ProfileEmaFilter, ProfileObBosEma

# Registro de perfiles disponibles
PERFILES: dict[str, type[BaseProfile]] = {
    "base":        ProfileBase,
    "ob_bos":      ProfileObBos,
    "ema_filter":  ProfileEmaFilter,
    "ob_bos_ema":  ProfileObBosEma,
}


def get_profile(nombre: str) -> BaseProfile:
    if nombre not in PERFILES:
        disponibles = ", ".join(PERFILES.keys())
        raise ValueError(f"Perfil '{nombre}' no existe. Disponibles: {disponibles}")
    return PERFILES[nombre]()


def listar_perfiles() -> list[dict]:
    return [
        {"nombre": n, "descripcion": cls().descripcion}
        for n, cls in PERFILES.items()
    ]
