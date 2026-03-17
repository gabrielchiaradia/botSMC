"""
scripts/compare_results.py
───────────────────────────
Compara múltiples resultados de backtest y muestra una tabla resumen.

Uso:
    # Comparar todos los JSONs en backtest/results/
    python scripts/compare_results.py

    # Comparar archivos específicos
    python scripts/compare_results.py bt_ethusdt_1h*.json

    # Solo los mejores N
    python scripts/compare_results.py --top 5

    # Ordenar por métrica específica
    python scripts/compare_results.py --sort pf
    python scripts/compare_results.py --sort wr
    python scripts/compare_results.py --sort dd
    python scripts/compare_results.py --sort trades

    # Filtrar por mínimo de trades
    python scripts/compare_results.py --min-trades 50

    # Exportar comparación a CSV
    python scripts/compare_results.py --csv comparacion.csv
"""

import sys
import json
import argparse
import glob
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# ── Colores terminal ──────────────────────────────────────
GREEN  = "\033[32m"; RED    = "\033[31m"
YELLOW = "\033[33m"; CYAN   = "\033[36m"
BOLD   = "\033[1m";  DIM    = "\033[2m"
RESET  = "\033[0m"


def parse_args():
    p = argparse.ArgumentParser(
        description="Comparar resultados de backtest",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ejemplos:
  python scripts/compare_results.py
  python scripts/compare_results.py bt_ethusdt_1h*.json
  python scripts/compare_results.py --top 5 --sort pf
  python scripts/compare_results.py --min-trades 50 --csv resultados.csv
        """
    )
    p.add_argument("files", nargs="*", default=[],
                   help="Archivos JSON a comparar (default: todos en backtest/results/)")
    p.add_argument("--dir", default="backtest/results",
                   help="Directorio donde buscar JSONs (default: backtest/results)")
    p.add_argument("--top", type=int, default=None,
                   help="Mostrar solo los N mejores resultados")
    p.add_argument("--sort", default="ret",
                   choices=["ret", "pf", "wr", "dd", "trades", "sharpe",
                            "symbol", "timeframe", "strategy", "windows"],
                   help="Ordenar por: ret, pf, wr, dd, trades, sharpe, symbol, timeframe, strategy, windows")
    p.add_argument("--min-trades", type=int, default=0,
                   help="Filtrar resultados con menos de N trades")
    p.add_argument("--csv", default=None,
                   help="Exportar comparación a CSV")
    return p.parse_args()


import re


def _extraer_windows(nombre: str) -> str:
    """Extrae la ventana horaria del nombre del archivo.
    Ej: bt_ethusdt_15m_ob_bos_none_180d_mt3_w13-16_rr1.5 → 13-16
        bt_ethusdt_1h_base_none_180d_mt3_rr2.5 → 24h
    """
    m = re.search(r'_w(\d+-\d+(?:_\d+-\d+)*)', nombre)
    if m:
        return m.group(1).replace('_', ',')
    return "24h"


def cargar_json(path: str) -> dict:
    """Carga un JSON de backtest y extrae las métricas."""
    with open(path) as f:
        data = json.load(f)

    nombre = Path(path).stem

    # Detectar si es un systematic (tiene campo "perfiles")
    if "perfiles" in data:
        # Tomar el mejor perfil del systematic
        perfiles = data["perfiles"]
        if not perfiles:
            return None
        mejor = max(perfiles, key=lambda p: (p.get("capital_final", 0) - p.get("capital_inicial", 1000)) / max(p.get("capital_inicial", 1000), 1))
        data = mejor
        nombre = nombre + " (mejor)"

    cap_ini = data.get("capital_inicial", 1000)
    cap_fin = data.get("capital_final", cap_ini)
    ret = (cap_fin - cap_ini) / cap_ini * 100

    return {
        "nombre":     nombre,
        "archivo":    Path(path).name,
        "symbol":     data.get("symbol", "?"),
        "timeframe":  data.get("timeframe", "?"),
        "perfil":     data.get("perfil", "?"),
        "trailing":   data.get("trailing_mode", "?"),
        "rr_config":  data.get("tp_rr_ratio", data.get("rr_ratio_config", "?")),
        "dias":       data.get("dias_backtest", data.get("dias", "?")),
        "windows":    _extraer_windows(nombre),
        "ret":        round(ret, 2),
        "trades":     data.get("total_trades", 0),
        "wr":         data.get("win_rate", 0),
        "pf":         data.get("profit_factor", 0),
        "dd":         data.get("max_drawdown_pct", 0),
        "sharpe":     data.get("sharpe_ratio", 0),
        "cap_ini":    cap_ini,
        "cap_fin":    round(cap_fin, 2),
        "avg_win":    data.get("avg_win_usd", 0),
        "avg_loss":   data.get("avg_loss_usd", 0),
        "avg_rr":     data.get("avg_rr_real", 0),
        "max_r":      data.get("max_r_capturado", 0),
        "filtradas":  data.get("señales_filtradas", 0),
        "motivos_filtro": data.get("motivos_filtro", {}),
    }


def buscar_archivos(args) -> list:
    """Encuentra todos los JSONs a comparar."""
    archivos = []

    if args.files:
        # Archivos pasados por CLI (soporta glob)
        for pattern in args.files:
            # Buscar en dir y en cwd
            matches = glob.glob(pattern)
            if not matches:
                matches = glob.glob(str(Path(args.dir) / pattern))
            archivos.extend(matches)
    else:
        # Todos los JSONs del directorio
        dir_path = Path(args.dir)
        if dir_path.exists():
            archivos = sorted(glob.glob(str(dir_path / "*.json")))

    # Filtrar duplicados y que existan
    vistos = set()
    unicos = []
    for a in archivos:
        real = str(Path(a).resolve())
        if real not in vistos and Path(a).exists():
            vistos.add(real)
            unicos.append(a)

    return unicos


def mostrar_tabla(resultados: list, sort_key: str, top_n: int = None):
    """Muestra la tabla de comparación."""

    # Ordenar
    reverse = True
    if sort_key == "dd":
        reverse = False  # Menor DD es mejor

    # Mapear alias de sort a campos
    sort_field = sort_key
    if sort_key == "strategy":
        sort_field = "perfil"

    # String columns: ordenar alphabetically asc
    string_cols = {"symbol", "timeframe", "perfil", "windows", "strategy"}
    if sort_key in string_cols:
        resultados.sort(key=lambda r: str(r.get(sort_field, "")))
    else:
        resultados.sort(key=lambda r: r.get(sort_field, 0), reverse=reverse)

    if top_n:
        resultados = resultados[:top_n]

    # Header
    print(f"\n{'═' * 120}")
    print(f"{'  COMPARACIÓN DE BACKTESTS':^120}")
    print(f"{'═' * 120}")
    print(f"  {len(resultados)} resultados | Ordenados por: {sort_key.upper()}")
    print(f"{'─' * 120}")

    # Columnas
    W = 150
    print(f"  {'#':>3}  {'Symbol':<8} {'TF':<4} {'Strategy':<10} {'Win':<6} {'Días':>5} {'RR':>5} {'Ret':>8} {'Trades':>7} {'WR':>7} {'PF':>7} {'DD':>7} {'AvgRR':>6} {'Cap.Fin':>10}")
    print(f"  {'─'*3}  {'─'*8} {'─'*4} {'─'*10} {'─'*6} {'─'*5} {'─'*5} {'─'*8} {'─'*7} {'─'*7} {'─'*7} {'─'*7} {'─'*6} {'─'*10}")

    for i, r in enumerate(resultados, 1):
        ret = r["ret"]
        ret_color = GREEN if ret >= 0 else RED
        pf_color = GREEN if r["pf"] >= 1.3 else YELLOW if r["pf"] >= 1.0 else RED
        dd_color = GREEN if r["dd"] <= 10 else YELLOW if r["dd"] <= 20 else RED
        wr_color = GREEN if r["wr"] >= 40 else YELLOW if r["wr"] >= 30 else RED

        dias = str(r.get("dias", "?"))
        rr = r.get("rr_config", "?")
        rr_str = f"1:{rr}" if rr != "?" else "?"
        sym = r.get("symbol", "?")[:8]
        tf = r.get("timeframe", "?")[:4]
        strat = r.get("perfil", "?")[:10]
        win = r.get("windows", "24h")[:6]

        print(f"  {i:>3}  {sym:<8} {tf:<4} {strat:<10} {win:<6} "
              f"{dias:>5} "
              f"{rr_str:>5} "
              f"{ret_color}{ret:>+7.2f}%{RESET} "
              f"{r['trades']:>6} "
              f"{wr_color}{r['wr']:>6.1f}%{RESET} "
              f"{pf_color}{r['pf']:>7.3f}{RESET} "
              f"{dd_color}{r['dd']:>6.1f}%{RESET} "
              f"{r['avg_rr']:>5.1f}R "
              f"${r['cap_fin']:>9,.2f}")

    print(f"{'─' * 120}")

    # Mejor y peor
    if len(resultados) >= 2:
        mejor = resultados[0]
        peor = resultados[-1]
        print(f"\n  {GREEN}★ MEJOR:{RESET} {mejor['symbol']} / {mejor['timeframe']} / {mejor['perfil']} / w:{mejor.get('windows','24h')}")
        print(f"    Ret: {mejor['ret']:+.2f}%  |  {mejor['trades']} trades  |  "
              f"WR {mejor['wr']:.1f}%  |  PF {mejor['pf']:.3f}  |  DD {mejor['dd']:.1f}%")
        print(f"\n  {RED}✗ PEOR:{RESET}  {peor['symbol']} / {peor['timeframe']} / {peor['perfil']} / w:{peor.get('windows','24h')}")
        print(f"    Ret: {peor['ret']:+.2f}%  |  {peor['trades']} trades  |  "
              f"WR {peor['wr']:.1f}%  |  PF {peor['pf']:.3f}  |  DD {peor['dd']:.1f}%")

    print()


def exportar_csv(resultados: list, path: str):
    """Exporta la comparación a CSV."""
    import csv
    campos = ["nombre", "archivo", "symbol", "timeframe", "perfil", "trailing", "windows",
              "ret", "trades", "wr", "pf", "dd", "sharpe", "cap_ini", "cap_fin",
              "avg_win", "avg_loss", "avg_rr", "max_r", "filtradas"]

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=campos)
        writer.writeheader()
        for r in resultados:
            writer.writerow({k: r.get(k, "") for k in campos})

    print(f"  CSV exportado: {path}")


def main():
    args = parse_args()

    archivos = buscar_archivos(args)

    if not archivos:
        print(f"\n  {RED}No se encontraron archivos JSON.{RESET}")
        print(f"  Buscando en: {args.dir}/")
        print(f"  Corré un backtest primero o pasá archivos por CLI.\n")
        return

    print(f"\n  Cargando {len(archivos)} archivos...")

    resultados = []
    errores = 0
    for archivo in archivos:
        try:
            r = cargar_json(archivo)
            if r and r["trades"] >= args.min_trades:
                resultados.append(r)
        except Exception as e:
            errores += 1
            print(f"  {DIM}Skip: {Path(archivo).name} ({e}){RESET}")

    if errores:
        print(f"  {YELLOW}{errores} archivos no pudieron cargarse{RESET}")

    if not resultados:
        print(f"\n  {RED}No hay resultados válidos para mostrar.{RESET}\n")
        return

    mostrar_tabla(resultados, args.sort, args.top)

    if args.csv:
        exportar_csv(resultados, args.csv)


if __name__ == "__main__":
    main()
