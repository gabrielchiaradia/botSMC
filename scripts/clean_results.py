#!/usr/bin/env python3
"""
scripts/clean_results.py
─────────────────────────
Elimina archivos de backtest que cumplan condiciones.

Uso:
    # Eliminar backtests con DD mayor a 20%
    python scripts/clean_results.py --max-dd 20

    # Eliminar backtests con WR menor a 40%
    python scripts/clean_results.py --min-wr 40

    # Eliminar backtests con retorno negativo
    python scripts/clean_results.py --min-ret 0

    # Eliminar backtests con PF menor a 1.0
    python scripts/clean_results.py --min-pf 1.0

    # Eliminar backtests con menos de 20 trades
    python scripts/clean_results.py --min-trades 20

    # Combinar condiciones (OR: elimina si cumple CUALQUIERA)
    python scripts/clean_results.py --max-dd 15 --min-wr 35 --min-pf 1.0

    # Solo mostrar qué se eliminaría (sin borrar)
    python scripts/clean_results.py --max-dd 20 --dry-run

    # Eliminar archivos de systematic/comparación
    python scripts/clean_results.py --remove-systematic
"""

import sys
import json
import argparse
import glob
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

GREEN  = "\033[32m"
RED    = "\033[31m"
YELLOW = "\033[33m"
CYAN   = "\033[36m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
RESET  = "\033[0m"

RESULTS_DIR = Path(__file__).resolve().parents[1] / "backtest" / "results"


def parse_args():
    p = argparse.ArgumentParser(
        description="Eliminar backtests que cumplan condiciones",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ejemplos:
  python scripts/clean_results.py --max-dd 20
  python scripts/clean_results.py --min-wr 40 --min-pf 1.0
  python scripts/clean_results.py --min-ret 0 --dry-run
  python scripts/clean_results.py --remove-systematic
  python scripts/clean_results.py --max-dd 15 --min-trades 30
        """)
    p.add_argument("--max-dd",     type=float, default=None,
                   help="Eliminar si DD%% >= este valor")
    p.add_argument("--min-wr",     type=float, default=None,
                   help="Eliminar si Win Rate < este valor")
    p.add_argument("--min-ret",    type=float, default=None,
                   help="Eliminar si retorno%% < este valor")
    p.add_argument("--min-pf",     type=float, default=None,
                   help="Eliminar si Profit Factor < este valor")
    p.add_argument("--min-trades", type=int,   default=None,
                   help="Eliminar si total trades < este valor")
    p.add_argument("--max-trades", type=int,   default=None,
                   help="Eliminar si total trades >= este valor")
    p.add_argument("--remove-systematic", action="store_true",
                   help="Eliminar archivos de comparación/systematic")
    p.add_argument("--dir",        default=str(RESULTS_DIR),
                   help=f"Directorio de resultados (default: {RESULTS_DIR})")
    p.add_argument("--dry-run",    action="store_true",
                   help="Solo mostrar qué se eliminaría, sin borrar")
    return p.parse_args()


def cargar_resultado(path):
    """Carga un JSON y extrae métricas. Retorna None si no es un backtest válido."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None

    # Es un archivo de comparación/systematic
    if data.get("comparacion"):
        return {"_tipo": "systematic", "_path": path}

    # Necesita tener campos de backtest
    cap_ini = data.get("capital_inicial", 0)
    cap_fin = data.get("capital_final", 0)
    if not cap_ini:
        return None

    ret = (cap_fin - cap_ini) / cap_ini * 100

    return {
        "_tipo":   "backtest",
        "_path":   path,
        "nombre":  Path(path).stem,
        "ret":     round(ret, 2),
        "wr":      data.get("win_rate", 0),
        "pf":      data.get("profit_factor", 0),
        "dd":      data.get("max_drawdown_pct", 0),
        "trades":  data.get("total_trades", 0),
        "symbol":  data.get("symbol", "?"),
        "tf":      data.get("timeframe", "?"),
    }


def debe_eliminar(r, args):
    """Retorna (True, motivo) si el resultado debe eliminarse."""
    if r["_tipo"] == "systematic":
        if args.remove_systematic:
            return True, "Archivo systematic/comparación"
        return False, ""

    motivos = []

    if args.max_dd is not None and r["dd"] >= args.max_dd:
        motivos.append(f"DD {r['dd']:.1f}% >= {args.max_dd}%")

    if args.min_wr is not None and r["wr"] < args.min_wr:
        motivos.append(f"WR {r['wr']:.1f}% < {args.min_wr}%")

    if args.min_ret is not None and r["ret"] < args.min_ret:
        motivos.append(f"Ret {r['ret']:+.1f}% < {args.min_ret}%")

    if args.min_pf is not None and r["pf"] < args.min_pf:
        motivos.append(f"PF {r['pf']:.2f} < {args.min_pf}")

    if args.min_trades is not None and r["trades"] < args.min_trades:
        motivos.append(f"Trades {r['trades']} < {args.min_trades}")

    if args.max_trades is not None and r["trades"] >= args.max_trades:
        motivos.append(f"Trades {r['trades']} >= {args.max_trades}")

    if motivos:
        return True, " | ".join(motivos)
    return False, ""


def main():
    args = parse_args()
    dir_path = Path(args.dir)

    # Verificar que hay al menos un filtro
    tiene_filtro = any([
        args.max_dd is not None,
        args.min_wr is not None,
        args.min_ret is not None,
        args.min_pf is not None,
        args.min_trades is not None,
        args.max_trades is not None,
        args.remove_systematic,
    ])
    if not tiene_filtro:
        print(f"\n  {YELLOW}⚠ No se especificó ningún filtro.{RESET}")
        print(f"  Usá --help para ver las opciones disponibles.\n")
        return

    # Buscar archivos
    archivos = sorted(glob.glob(str(dir_path / "*.json")))
    if not archivos:
        print(f"\n  No hay archivos en {dir_path}\n")
        return

    print(f"\n{'═'*70}")
    print(f"{BOLD}  LIMPIEZA DE BACKTESTS{RESET}")
    print(f"{'═'*70}")
    print(f"  Directorio: {dir_path}")
    print(f"  Archivos:   {len(archivos)}")

    # Filtros activos
    filtros = []
    if args.max_dd is not None:     filtros.append(f"DD >= {args.max_dd}%")
    if args.min_wr is not None:     filtros.append(f"WR < {args.min_wr}%")
    if args.min_ret is not None:    filtros.append(f"Ret < {args.min_ret}%")
    if args.min_pf is not None:     filtros.append(f"PF < {args.min_pf}")
    if args.min_trades is not None: filtros.append(f"Trades < {args.min_trades}")
    if args.max_trades is not None: filtros.append(f"Trades >= {args.max_trades}")
    if args.remove_systematic:      filtros.append("Archivos systematic")
    print(f"  Eliminar si: {' OR '.join(filtros)}")
    if args.dry_run:
        print(f"  {YELLOW}MODO DRY-RUN — no se elimina nada{RESET}")
    print(f"{'─'*70}")

    # Analizar
    a_eliminar = []
    a_mantener = []
    errores = 0

    for path in archivos:
        r = cargar_resultado(path)
        if r is None:
            errores += 1
            continue

        eliminar, motivo = debe_eliminar(r, args)
        if eliminar:
            a_eliminar.append((r, motivo))
        else:
            a_mantener.append(r)

    # Mostrar lo que se elimina
    if not a_eliminar:
        print(f"\n  {GREEN}✓ Ningún archivo cumple las condiciones. Nada que eliminar.{RESET}\n")
        return

    print(f"\n  {RED}Archivos a eliminar: {len(a_eliminar)}{RESET}")
    print(f"  {GREEN}Archivos que se mantienen: {len(a_mantener)}{RESET}")
    if errores:
        print(f"  {YELLOW}Archivos no parseables: {errores}{RESET}")
    print()

    for r, motivo in a_eliminar:
        nombre = r.get("nombre", Path(r["_path"]).stem)
        if r["_tipo"] == "backtest":
            print(f"  {RED}✗{RESET} {nombre[:50]:<50} {DIM}{motivo}{RESET}")
        else:
            print(f"  {RED}✗{RESET} {nombre[:50]:<50} {DIM}{motivo}{RESET}")

    print(f"\n{'─'*70}")

    if args.dry_run:
        print(f"  {YELLOW}DRY-RUN: no se eliminó nada. Quitá --dry-run para eliminar.{RESET}\n")
        return

    # Confirmar
    resp = input(f"  {BOLD}¿Eliminar {len(a_eliminar)} archivos? (s/N): {RESET}").strip().lower()
    if resp not in ("s", "si", "sí", "y", "yes"):
        print(f"  Cancelado.\n")
        return

    # Eliminar
    eliminados = 0
    for r, _ in a_eliminar:
        try:
            Path(r["_path"]).unlink()
            eliminados += 1
        except OSError as e:
            print(f"  {YELLOW}⚠ No se pudo eliminar {r['_path']}: {e}{RESET}")

    print(f"\n  {GREEN}✓ Eliminados: {eliminados}/{len(a_eliminar)}{RESET}")
    print(f"  Quedan {len(a_mantener)} archivos en {dir_path}\n")


if __name__ == "__main__":
    main()
