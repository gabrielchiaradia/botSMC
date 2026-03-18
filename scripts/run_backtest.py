"""
scripts/run_backtest.py
────────────────────────
Punto de entrada para el backtest.

Uso:
    # Un solo perfil
    python scripts/run_backtest.py                      # perfil base (default)
    python scripts/run_backtest.py --strategy ob_bos    # perfil OB+BOS
    python scripts/run_backtest.py --strategy base --dias 60 --capital 500

    # Comparar todos los perfiles disponibles
    python scripts/run_backtest.py --compare

    # Comparar perfiles específicos
    python scripts/run_backtest.py --compare --profiles base ob_bos

    # Listar perfiles disponibles
    python scripts/run_backtest.py --list-profiles
"""

import sys
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import os
os.environ.setdefault("LOG_LEVEL", "WARNING")  # silenciar en output de comparación

from data import obtener_historico_backtest
from backtest.engine import BacktestEngine, exportar_json, exportar_comparacion, BacktestStats
from backtest import BacktestStats
from config import exchange as excfg, backtest as bcfg
from strategy.profiles import get_profile, listar_perfiles, PERFILES
from utils.logger import get_logger

logger = get_logger("run_backtest")

GREEN  = "\033[32m"; RED   = "\033[31m"
YELLOW = "\033[33m"; CYAN  = "\033[36m"
BOLD   = "\033[1m";  DIM   = "\033[2m"; RESET = "\033[0m"


# ══════════════════════════════════════════════════════════
#  ARGS
# ══════════════════════════════════════════════════════════

def parse_args():
    p = argparse.ArgumentParser(
        description="SMC Backtest Engine con soporte de perfiles y trailing dinámico",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ejemplos:
  python scripts/run_backtest.py
  python scripts/run_backtest.py --strategy ob_bos
  python scripts/run_backtest.py --trailing escalones
  python scripts/run_backtest.py --windows 8-11,13-18
  python scripts/run_backtest.py --compare
  python scripts/run_backtest.py --compare-trailing
  python scripts/run_backtest.py --list-profiles
        """
    )
    p.add_argument("--strategy",      default="base",
                   help="Perfil de estrategia a usar (default: base)")
    p.add_argument("--trailing",      default="none",
                   choices=["none", "escalones", "atr", "hibrido"],
                   help="Modo de trailing dinámico (default: none)")
    p.add_argument("--compare",       action="store_true",
                   help="Correr y comparar múltiples perfiles")
    p.add_argument("--compare-trailing", action="store_true",
                   help="Comparar los 4 modos de trailing con el mismo perfil")
    p.add_argument("--profiles",      nargs="+",
                   help="Perfiles a comparar (default: todos). Ej: --profiles base ob_bos")
    p.add_argument("--list-profiles", action="store_true",
                   help="Listar perfiles disponibles y salir")
    p.add_argument("--symbol",        default=excfg.SYMBOL)
    p.add_argument("--timeframe",     default=excfg.TIMEFRAME)
    p.add_argument("--dias",          type=int,   default=bcfg.DIAS)
    p.add_argument("--capital",       type=float, default=bcfg.CAPITAL_INICIO)
    p.add_argument("--cooldown",      type=int,   default=3,
                   help="Velas de cooldown post-SL (default: 3)")
    p.add_argument("--atr-mult",      type=float, default=2.0,
                   help="Multiplicador ATR para trailing ATR (default: 2.0)")
    p.add_argument("--max-trades",    type=int,   default=1,
                   help="Máximo de trades simultáneos (default: 1)")
    p.add_argument("--racha-reduce",  type=int,   default=3,
                   help="Tras N SL seguidos, reducir a 1 trade (default: 3)")
    p.add_argument("--windows",       default=None,
                   help="Ventanas horarias UTC. Ej: 8-11,13-18. Vacío=\"\" = 24h sin filtro. "
                        "Cuando se pasa --windows, FILTRO_SESION se desactiva automáticamente.")
    p.add_argument("--score",         type=int, default=None,
                   help="Score mínimo (override .env)")
    p.add_argument("--rr",            type=float, default=None,
                   help="TP/RR ratio (override .env). Ej: 2.0, 3.0")
    p.add_argument("--ob-max-age",    type=int, default=None,
                   help="Edad máxima del OB en velas (solo ob_bos). 0=sin límite. Ej: 20, 25, 30")
    p.add_argument("--output",        default=None,
                   help="Nombre del archivo de salida JSON")
    args = p.parse_args()

    # Override parámetros del .env si se pasaron por CLI
    from config import strategy as _strat, risk as _risk
    if args.windows is not None:
        _strat.TRADING_WINDOWS_RAW = args.windows
        _strat.FILTRO_SESION = False  # --windows reemplaza filtro de sesión
    if args.score is not None:
        _strat.SCORE_MINIMO = args.score
    if args.rr is not None:
        _risk.TP_RR_RATIO = args.rr
    if args.ob_max_age is not None:
        import os
        os.environ["OB_MAX_AGE"] = str(args.ob_max_age)

    return args


# ══════════════════════════════════════════════════════════
#  CORRER UN PERFIL
# ══════════════════════════════════════════════════════════

def correr_perfil(df, perfil_nombre: str, symbol: str, timeframe: str,
                  capital: float, trailing_mode: str = "none",
                  cooldown_barras: int = 3,
                  atr_trailing_mult: float = 2.0,
                  max_open_trades: int = 1,
                  racha_sl_reduce: int = 3) -> BacktestStats:
    """Corre el backtest para un perfil + modo trailing y retorna las stats."""
    perfil = get_profile(perfil_nombre) if perfil_nombre != "base" else None

    engine = BacktestEngine(
        df               = df,
        capital          = capital,
        symbol           = symbol,
        timeframe        = timeframe,
        perfil           = perfil,
        trailing_mode    = trailing_mode,
        cooldown_barras  = cooldown_barras,
        atr_trailing_mult = atr_trailing_mult,
        max_open_trades  = max_open_trades,
        racha_sl_reduce  = racha_sl_reduce,
    )
    return engine.run()


# ══════════════════════════════════════════════════════════
#  DISPLAY
# ══════════════════════════════════════════════════════════

def mostrar_stats(r: BacktestStats, verbose: bool = True):
    ret    = (r.capital_final - r.capital_inicial) / r.capital_inicial * 100
    c_ret  = GREEN if ret >= 0 else RED
    c_pf   = GREEN if r.profit_factor >= 1.5 else (YELLOW if r.profit_factor >= 1.0 else RED)
    c_wr   = GREEN if r.win_rate >= 55 else (YELLOW if r.win_rate >= 45 else RED)

    print(f"\n{BOLD}  Perfil: {CYAN}{r.perfil}{RESET}  —  {r.perfil_descripcion}")
    if r.trailing_mode != "none":
        print(f"  Trailing: {CYAN}{r.trailing_mode}{RESET}")
    print(f"  {'─'*52}")
    print(f"  Período:        {r.fecha_inicio[:10]} → {r.fecha_fin[:10]}")
    print(f"  Capital:        ${r.capital_inicial:,.2f}  →  {c_ret}${r.capital_final:,.2f}{RESET}  ({c_ret}{ret:+.2f}%{RESET})")
    print(f"  Trades:         {r.total_trades}  ({GREEN}{r.wins}W{RESET} / {RED}{r.losses}L{RESET})")
    print(f"  Win Rate:       {c_wr}{r.win_rate}%{RESET}")
    print(f"  Profit Factor:  {c_pf}{r.profit_factor}{RESET}")
    print(f"  Sharpe Ratio:   {r.sharpe_ratio}")
    print(f"  Max Drawdown:   {RED}{r.max_drawdown_pct}%{RESET}  (${r.max_drawdown_usd:,.2f})")
    print(f"  Avg Win:        {GREEN}${r.avg_win_usd:,.2f}{RESET}  |  Avg Loss: {RED}${r.avg_loss_usd:,.2f}{RESET}")
    print(f"  Avg Duración:   {r.avg_duracion_min:.0f} min")
    if r.avg_rr_real > 0:
        print(f"  Avg RR Real:    {GREEN}{r.avg_rr_real:.1f}R{RESET}  |  Max R Capturado: {GREEN}{r.max_r_capturado:.1f}R{RESET}")
    if r.señales_filtradas:
        print(f"  Señales filtradas: {YELLOW}{r.señales_filtradas}{RESET}")
        if hasattr(r, 'motivos_filtro') and r.motivos_filtro:
            for motivo, count in sorted(r.motivos_filtro.items(), key=lambda x: x[1], reverse=True):
                pct = count / r.señales_filtradas * 100
                print(f"    {DIM}· {motivo}: {count} ({pct:.0f}%){RESET}")


def mostrar_comparacion(resultados: list[BacktestStats]):
    """Tabla comparativa de múltiples perfiles lado a lado."""
    print(f"\n{BOLD}{'═'*70}{RESET}")
    print(f"{BOLD}  COMPARACIÓN DE PERFILES{RESET}")
    print(f"{BOLD}{'═'*70}{RESET}\n")

    # Header
    col_w = 14
    header = f"  {'Métrica':<22}"
    for r in resultados:
        header += f"{r.perfil:>{col_w}}"
    print(f"{BOLD}{header}{RESET}")
    print(f"  {'─'*22}" + "─"*(col_w * len(resultados)))

    metricas = [
        ("Trailing",       lambda r: r.trailing_mode),
        ("Trades",         lambda r: str(r.total_trades)),
        ("Win Rate",       lambda r: f"{r.win_rate}%"),
        ("Profit Factor",  lambda r: str(r.profit_factor)),
        ("Sharpe Ratio",   lambda r: str(r.sharpe_ratio)),
        ("Max Drawdown",   lambda r: f"{r.max_drawdown_pct}%"),
        ("Retorno",        lambda r: f"{(r.capital_final-r.capital_inicial)/r.capital_inicial*100:+.2f}%"),
        ("Capital Final",  lambda r: f"${r.capital_final:,.2f}"),
        ("Avg Win",        lambda r: f"${r.avg_win_usd:,.2f}"),
        ("Avg Loss",       lambda r: f"${r.avg_loss_usd:,.2f}"),
        ("Avg RR Real",    lambda r: f"{r.avg_rr_real:.1f}R"),
        ("Max R Capturado",lambda r: f"{r.max_r_capturado:.1f}R"),
        ("Avg Duración",   lambda r: f"{r.avg_duracion_min:.0f}m"),
        ("Filtradas",      lambda r: str(r.señales_filtradas)),
    ]

    for nombre, fn in metricas:
        fila = f"  {nombre:<22}"
        for r in resultados:
            val = fn(r)
            fila += f"{val:>{col_w}}"
        print(fila)

    print(f"\n  {'─'*22}" + "─"*(col_w * len(resultados)))

    # Ganador por métrica
    print(f"\n{BOLD}  Ganador por métrica:{RESET}")
    comparables = [
        ("Win Rate",      lambda r: r.win_rate,      True),
        ("Profit Factor", lambda r: r.profit_factor, True),
        ("Sharpe Ratio",  lambda r: r.sharpe_ratio,  True),
        ("Max Drawdown",  lambda r: r.max_drawdown_pct, False),  # menor = mejor
        ("Retorno %",     lambda r: (r.capital_final-r.capital_inicial)/r.capital_inicial, True),
    ]
    for nombre, fn, mayor_es_mejor in comparables:
        valores = [(fn(r), r.perfil) for r in resultados]
        ganador = max(valores, key=lambda x: x[0]) if mayor_es_mejor else min(valores, key=lambda x: x[0])
        print(f"    {nombre:<18} → {GREEN}{ganador[1]}{RESET}")


# ══════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════

def main():
    args = parse_args()

    # Listar perfiles y salir
    if args.list_profiles:
        print(f"\n{BOLD}  Perfiles disponibles:{RESET}\n")
        for p in listar_perfiles():
            print(f"  {GREEN}{p['nombre']:<15}{RESET}  {p['descripcion']}")
        print(f"\n  Uso: python scripts/run_backtest.py --strategy NOMBRE\n")
        return

    # Mostrar windows activas
    from config import strategy as scfg
    from config.settings import parse_trading_windows
    windows = parse_trading_windows(scfg.TRADING_WINDOWS_RAW)
    win_str = scfg.TRADING_WINDOWS_RAW if scfg.TRADING_WINDOWS_RAW.strip() else "24h (sin filtro)"
    sesion_str = f"{'ON — ' + ','.join(scfg.SESIONES_ACTIVAS)}" if scfg.FILTRO_SESION else "OFF"

    print(f"\n{'═'*60}")
    print(f"{'  SMC BACKTEST ENGINE':^60}")
    print(f"{'═'*60}")
    print(f"  Par:       {args.symbol} / {args.timeframe}")
    print(f"  Capital:   ${args.capital:,.0f} USDT")
    print(f"  Período:   {args.dias} días")
    print(f"  Windows:   {win_str}")
    print(f"  Sesiones:  {sesion_str}")
    print(f"  Max trades:{args.max_trades}")
    if args.rr:
        print(f"  RR:        1:{args.rr}")

    # Cargar datos UNA sola vez (se reusan en el modo comparación)
    print(f"\n  Cargando datos históricos...")
    df = obtener_historico_backtest(args.symbol, args.timeframe, args.dias)
    print(f"  {len(df):,} velas | {str(df.index[0])[:10]} → {str(df.index[-1])[:10]}\n")

    # ── MODO COMPARACIÓN TRAILING ─────────────────────────
    if args.compare_trailing:
        nombre = args.strategy
        modos  = ["none", "escalones", "atr", "hibrido"]
        print(f"  Perfil: {CYAN}{nombre}{RESET}")
        print(f"  Comparando modos de trailing: {', '.join(modos)}")
        print(f"{'═'*60}\n")

        resultados = []
        for modo in modos:
            try:
                label = f"{nombre}+{modo}"
                print(f"  Corriendo '{label}'...", end="", flush=True)
                stats = correr_perfil(
                    df, nombre, args.symbol, args.timeframe, args.capital,
                    trailing_mode=modo,
                    cooldown_barras=args.cooldown,
                    atr_trailing_mult=args.atr_mult,
                    max_open_trades=args.max_trades,
                    racha_sl_reduce=args.racha_reduce,
                )
                # Overridear nombre de perfil para que la tabla sea legible
                stats.perfil = label
                stats.perfil_descripcion = f"Trailing: {modo}"
                resultados.append(stats)
                ret = (stats.capital_final - stats.capital_inicial) / stats.capital_inicial * 100
                color = GREEN if ret >= 0 else RED
                print(f"  {color}{ret:+.2f}%{RESET}  |  {stats.total_trades} trades  |  "
                      f"WR {stats.win_rate}%  |  Avg RR: {stats.avg_rr_real:.1f}R")
            except ValueError as e:
                print(f"\n  {RED}Error: {e}{RESET}")

        if resultados:
            mostrar_comparacion(resultados)
            output = args.output or "comparacion_trailing.json"
            path   = exportar_comparacion(resultados, output)
            print(f"\n  Comparación exportada: {path}\n")

    # ── MODO COMPARACIÓN PERFILES ─────────────────────────
    elif args.compare:
        nombres = args.profiles or list(PERFILES.keys())
        print(f"  Perfiles a comparar: {', '.join(nombres)}")
        print(f"  Trailing: {args.trailing}")
        print(f"{'═'*60}\n")

        resultados = []
        for nombre in nombres:
            try:
                print(f"  Corriendo perfil '{nombre}'...", end="", flush=True)
                stats = correr_perfil(
                    df, nombre, args.symbol, args.timeframe, args.capital,
                    trailing_mode=args.trailing,
                    cooldown_barras=args.cooldown,
                    atr_trailing_mult=args.atr_mult,
                    max_open_trades=args.max_trades,
                    racha_sl_reduce=args.racha_reduce,
                )
                resultados.append(stats)
                ret = (stats.capital_final - stats.capital_inicial) / stats.capital_inicial * 100
                color = GREEN if ret >= 0 else RED
                print(f"  {color}{ret:+.2f}%{RESET}  |  {stats.total_trades} trades  |  WR {stats.win_rate}%")
            except ValueError as e:
                print(f"\n  {RED}Error: {e}{RESET}")

        if not resultados:
            print(f"\n  {RED}No se pudieron correr perfiles.{RESET}")
            return

        mostrar_comparacion(resultados)

        output = args.output or "comparacion.json"
        path   = exportar_comparacion(resultados, output)
        print(f"\n  Comparación exportada: {path}")
        print(f"  Abrí el dashboard y cargá ese archivo para ver la comparación visual.\n")

    # ── MODO SINGLE PERFIL ────────────────────────────────
    else:
        nombre = args.strategy
        print(f"  Perfil: {CYAN}{nombre}{RESET}")
        print(f"  Trailing: {CYAN}{args.trailing}{RESET}")
        print(f"{'═'*60}\n")

        try:
            stats = correr_perfil(
                df, nombre, args.symbol, args.timeframe, args.capital,
                trailing_mode=args.trailing,
                cooldown_barras=args.cooldown,
                atr_trailing_mult=args.atr_mult,
                    max_open_trades=args.max_trades,
                    racha_sl_reduce=args.racha_reduce,
            )
        except ValueError as e:
            print(f"  {RED}Error: {e}{RESET}")
            print(f"  Perfiles disponibles: {', '.join(PERFILES.keys())}")
            return

        mostrar_stats(stats)

        if args.output:
            output = args.output
        else:
            parts = ["bt", args.symbol.lower(), args.timeframe, nombre, args.trailing,
                     f"{args.dias}d", f"mt{args.max_trades}"]
            if args.windows:
                win_safe = args.windows.replace(',','_').replace('"','').replace("'","")
                parts.append(f"w{win_safe}")
            if args.rr:
                parts.append(f"rr{args.rr}")
            if args.score:
                parts.append(f"sc{args.score}")
            if args.ob_max_age:
                parts.append(f"age{args.ob_max_age}")
            output = "_".join(parts) + ".json"
        path   = exportar_json(stats, output)
        print(f"\n  JSON exportado: {path}")
        print(f"  Abrí backtest_dashboard.html y cargá ese archivo.\n")


if __name__ == "__main__":
    main()
