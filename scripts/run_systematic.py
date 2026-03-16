"""
scripts/run_systematic.py
──────────────────────────
Comparación sistemática de la estrategia SMC.
Corre múltiples combinaciones de par, timeframe, perfil y trailing,
y genera una tabla resumen para encontrar la mejor configuración.

Uso:
    python scripts/run_systematic.py
    python scripts/run_systematic.py --dias 60
    python scripts/run_systematic.py --capital 2000
"""

import sys
import argparse
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import os
os.environ.setdefault("LOG_LEVEL", "ERROR")  # Silenciar logs durante la comparación

from data import obtener_historico_backtest
from backtest.engine import BacktestEngine, exportar_comparacion, BacktestStats
from strategy.profiles import get_profile, PERFILES
from utils.logger import get_logger

logger = get_logger("systematic")


# ══════════════════════════════════════════════════════════
#  CONFIGURACIÓN DE LA COMPARACIÓN
# ══════════════════════════════════════════════════════════

SYMBOLS    = ["BTCUSDT", "ETHUSDT"]
TIMEFRAMES = ["15m", "1h"]
PERFILES_A_PROBAR   = ["base", "ob_bos"]
TRAILING_A_PROBAR   = ["none", "escalones", "hibrido"]

# Parámetros alternativos a probar
SCORE_MINIMOS = [50, 65, 75]
ATR_MULTIPLIERS = [1.5, 2.0]


def parse_args():
    p = argparse.ArgumentParser(description="Comparación sistemática SMC")
    p.add_argument("--dias",          type=int,   default=90)
    p.add_argument("--capital",       type=float, default=1000)
    p.add_argument("--symbol",        default=None,
                   help="Forzar par específico (salta Fase 1). Ej: ETHUSDT")
    p.add_argument("--timeframe",     default=None,
                   help="Forzar timeframe específico (salta Fase 1). Ej: 1h")
    p.add_argument("--max-trades",    type=int,   default=1,
                   help="Máximo de trades simultáneos (default: 1)")
    p.add_argument("--racha-reduce",  type=int,   default=3,
                   help="Tras N SL seguidos, reducir a 1 trade (default: 3)")
    p.add_argument("--output",        default=None,
                   help="Nombre del JSON de salida (auto-generado si no se pasa)")
    args = p.parse_args()

    # Generar nombre de output descriptivo si no se pasó
    if args.output is None:
        parts = ["systematic"]
        if args.symbol:
            parts.append(args.symbol.lower())
        if args.timeframe:
            parts.append(args.timeframe)
        parts.append(f"{args.dias}d")
        parts.append(f"mt{args.max_trades}")
        args.output = "_".join(parts) + ".json"

    return args


def correr_test(df, perfil_nombre, symbol, timeframe, capital,
                trailing_mode="none", score_min=None, atr_mult=None,
                tp_rr=None, max_open_trades=1, racha_sl_reduce=3):
    """Corre un backtest individual con parámetros opcionales."""
    import config.settings as settings

    # Override temporal de parámetros si se pasan
    orig_score = settings.strategy.SCORE_MINIMO
    orig_atr   = settings.risk.ATR_MULTIPLIER
    orig_rr    = settings.risk.TP_RR_RATIO

    if score_min is not None:
        settings.strategy.SCORE_MINIMO = score_min
    if atr_mult is not None:
        settings.risk.ATR_MULTIPLIER = atr_mult
    if tp_rr is not None:
        settings.risk.TP_RR_RATIO = tp_rr

    try:
        perfil = get_profile(perfil_nombre) if perfil_nombre != "base" else None
        engine = BacktestEngine(
            df=df, capital=capital, symbol=symbol,
            timeframe=timeframe, perfil=perfil,
            trailing_mode=trailing_mode,
            cooldown_barras=3,
            max_open_trades=max_open_trades,
            racha_sl_reduce=racha_sl_reduce,
        )
        stats = engine.run()
    finally:
        # Restaurar parámetros originales
        settings.strategy.SCORE_MINIMO = orig_score
        settings.risk.ATR_MULTIPLIER = orig_atr
        settings.risk.TP_RR_RATIO = orig_rr

    return stats


def main():
    args = parse_args()
    inicio = time.time()

    # Detectar si se forzó par/tf
    forced = args.symbol is not None and args.timeframe is not None

    print()
    print("=" * 80)
    print("  SMC — COMPARACIÓN SISTEMÁTICA".center(80))
    print("=" * 80)
    print(f"  Capital:    ${args.capital:,.0f}")
    print(f"  Período:    {args.dias} días")
    print(f"  Max trades: {args.max_trades}")
    if forced:
        print(f"  Par forzado:  {args.symbol} / {args.timeframe}")
    else:
        print(f"  Pares:      {', '.join(SYMBOLS)}")
        print(f"  Timeframes: {', '.join(TIMEFRAMES)}")
    print(f"  Perfiles:   {', '.join(PERFILES_A_PROBAR)}")
    print(f"  Trailing:   {', '.join(TRAILING_A_PROBAR)}")
    print(f"  Scores:     {SCORE_MINIMOS}")
    print(f"  ATR mult:   {ATR_MULTIPLIERS}")
    print("=" * 80)
    print()

    resultados_fase1 = []

    if forced:
        # ── SKIP FASE 1: Usar par/tf forzado ──────────────────
        mejor_symbol = args.symbol
        mejor_tf     = args.timeframe
        print(f"FASE 1: SKIP — usando {mejor_symbol}/{mejor_tf}")
        print("-" * 80)
        print(f"  Cargando datos {mejor_symbol}/{mejor_tf}...")
        df_mejor = obtener_historico_backtest(mejor_symbol, mejor_tf, args.dias)
        if len(df_mejor) < 100:
            print("  Datos insuficientes.")
            return
        stats = correr_test(df_mejor, "base", mejor_symbol, mejor_tf, args.capital,
                            max_open_trades=args.max_trades, racha_sl_reduce=args.racha_reduce)
        ret = (stats.capital_final - stats.capital_inicial) / stats.capital_inicial * 100
        print(f"  Baseline: {ret:+.2f}%  |  {stats.total_trades} trades  |  WR {stats.win_rate:.1f}%  |  PF {stats.profit_factor:.3f}")
        resultados_fase1.append({"label": f"{mejor_symbol}/{mejor_tf}", "symbol": mejor_symbol, "tf": mejor_tf,
                                 "trades": stats.total_trades, "wr": stats.win_rate, "pf": stats.profit_factor,
                                 "ret": ret, "dd": stats.max_drawdown_pct, "stats": stats})
    else:
        # ── FASE 1: Par + Timeframe (encontrar el mejor mercado) ──────
        print("FASE 1: Mejor par + timeframe")
        print("-" * 80)

        for symbol in SYMBOLS:
            for tf in TIMEFRAMES:
                label = f"{symbol}/{tf}"
                print(f"  {label:<20}", end="", flush=True)
                try:
                    df = obtener_historico_backtest(symbol, tf, args.dias)
                    if len(df) < 100:
                        print("  datos insuficientes")
                        continue

                    stats = correr_test(df, "base", symbol, tf, args.capital, max_open_trades=args.max_trades, racha_sl_reduce=args.racha_reduce)
                    ret = (stats.capital_final - stats.capital_inicial) / stats.capital_inicial * 100
                    resultados_fase1.append({
                        "label": label, "symbol": symbol, "tf": tf,
                        "trades": stats.total_trades, "wr": stats.win_rate,
                        "pf": stats.profit_factor, "ret": ret,
                        "dd": stats.max_drawdown_pct, "sharpe": stats.sharpe_ratio,
                        "stats": stats,
                    })
                    print(f"  {ret:+7.2f}%  |  {stats.total_trades:3d} trades  |  "
                          f"WR {stats.win_rate:5.1f}%  |  PF {stats.profit_factor:.3f}  |  "
                          f"DD {stats.max_drawdown_pct:.1f}%")
                except Exception as e:
                    print(f"  ERROR: {e}")

        if not resultados_fase1:
            print("\n  No se pudieron obtener datos. Verificá la conexión.")
            return

        resultados_fase1.sort(key=lambda x: x["ret"], reverse=True)

        print()
        print("  Top 3 mercados:")
        for i, r in enumerate(resultados_fase1[:3], 1):
            print(f"    {i}. {r['label']:<20}  {r['ret']:+.2f}%  |  WR {r['wr']:.1f}%  |  PF {r['pf']:.3f}")

        mejor = resultados_fase1[0]
        mejor_symbol = mejor["symbol"]
        mejor_tf     = mejor["tf"]
        print(f"\n  Usando {mejor_symbol}/{mejor_tf} para las siguientes fases")

    # ── Cargar datos para fases 2-5 ───────────────────────────
    if not forced:
        df_mejor = obtener_historico_backtest(mejor_symbol, mejor_tf, args.dias)

    # ── FASE 2: Perfil + Trailing ─────────────────────────────
    print()
    print(f"FASE 2: Perfil + Trailing ({mejor_symbol}/{mejor_tf})")
    print("-" * 80)

    resultados_fase2 = []
    for perfil in PERFILES_A_PROBAR:
        for trailing in TRAILING_A_PROBAR:
            label = f"{perfil}+{trailing}"
            print(f"  {label:<25}", end="", flush=True)
            try:
                stats = correr_test(
                    df_mejor, perfil, mejor_symbol, mejor_tf,
                    args.capital, trailing_mode=trailing,
                    max_open_trades=args.max_trades, racha_sl_reduce=args.racha_reduce
                )
                ret = (stats.capital_final - stats.capital_inicial) / stats.capital_inicial * 100
                resultados_fase2.append({
                    "label": label, "perfil": perfil, "trailing": trailing,
                    "trades": stats.total_trades, "wr": stats.win_rate,
                    "pf": stats.profit_factor, "ret": ret,
                    "dd": stats.max_drawdown_pct, "rr": stats.avg_rr_real,
                    "stats": stats,
                })
                print(f"  {ret:+7.2f}%  |  {stats.total_trades:3d} trades  |  "
                      f"WR {stats.win_rate:5.1f}%  |  PF {stats.pf if hasattr(stats, 'pf') else stats.profit_factor:.3f}  |  "
                      f"RR {stats.avg_rr_real:.1f}R")
            except Exception as e:
                print(f"  ERROR: {e}")

    resultados_fase2.sort(key=lambda x: x["ret"], reverse=True)

    print()
    print("  Top 3 combinaciones:")
    for i, r in enumerate(resultados_fase2[:3], 1):
        print(f"    {i}. {r['label']:<25}  {r['ret']:+.2f}%  |  WR {r['wr']:.1f}%  |  PF {r['pf']:.3f}")

    mejor_perfil   = resultados_fase2[0]["perfil"]   if resultados_fase2 else "base"
    mejor_trailing = resultados_fase2[0]["trailing"]  if resultados_fase2 else "none"

    # ── FASE 3: Score mínimo + ATR multiplier ─────────────────────
    print()
    print(f"FASE 3: Optimizar parámetros ({mejor_symbol}/{mejor_tf}, {mejor_perfil}+{mejor_trailing})")
    print("-" * 80)

    resultados_fase3 = []
    for score in SCORE_MINIMOS:
        for atr_m in ATR_MULTIPLIERS:
            label = f"score={score} atr={atr_m}"
            print(f"  {label:<25}", end="", flush=True)
            try:
                stats = correr_test(
                    df_mejor, mejor_perfil, mejor_symbol, mejor_tf,
                    args.capital, trailing_mode=mejor_trailing,
                    score_min=score, atr_mult=atr_m,
                    max_open_trades=args.max_trades, racha_sl_reduce=args.racha_reduce,
                )
                ret = (stats.capital_final - stats.capital_inicial) / stats.capital_inicial * 100
                resultados_fase3.append({
                    "label": label, "score": score, "atr_mult": atr_m,
                    "trades": stats.total_trades, "wr": stats.win_rate,
                    "pf": stats.profit_factor, "ret": ret,
                    "dd": stats.max_drawdown_pct,
                    "stats": stats,
                })
                print(f"  {ret:+7.2f}%  |  {stats.total_trades:3d} trades  |  "
                      f"WR {stats.win_rate:5.1f}%  |  PF {stats.profit_factor:.3f}  |  "
                      f"DD {stats.max_drawdown_pct:.1f}%")
            except Exception as e:
                print(f"  ERROR: {e}")

    resultados_fase3.sort(key=lambda x: x["ret"], reverse=True)

    # Tomar mejores parámetros de fase 3
    mejor_score = resultados_fase3[0]["score"] if resultados_fase3 else 50
    mejor_atr_m = resultados_fase3[0]["atr_mult"] if resultados_fase3 else 1.5

    # ── FASE 4: TP/RR Ratio (dejar correr más los trades) ────────
    print()
    print(f"FASE 4: TP/RR Ratio ({mejor_symbol}/{mejor_tf}, {mejor_perfil}+{mejor_trailing}, "
          f"score={mejor_score}, ATR x{mejor_atr_m})")
    print("-" * 80)

    TP_RR_RATIOS = [1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 5.0]

    resultados_fase4 = []
    for rr in TP_RR_RATIOS:
        label = f"RR 1:{rr}"
        print(f"  {label:<25}", end="", flush=True)
        try:
            stats = correr_test(
                df_mejor, mejor_perfil, mejor_symbol, mejor_tf,
                args.capital, trailing_mode=mejor_trailing,
                score_min=mejor_score, atr_mult=mejor_atr_m,
                tp_rr=rr,
                max_open_trades=args.max_trades, racha_sl_reduce=args.racha_reduce,
            )
            ret = (stats.capital_final - stats.capital_inicial) / stats.capital_inicial * 100
            resultados_fase4.append({
                "label": label, "rr": rr,
                "trades": stats.total_trades, "wr": stats.win_rate,
                "pf": stats.profit_factor, "ret": ret,
                "dd": stats.max_drawdown_pct,
                "avg_rr": stats.avg_rr_real,
                "max_r": stats.max_r_capturado,
                "avg_win": stats.avg_win_usd,
                "avg_loss": stats.avg_loss_usd,
                "stats": stats,
            })
            print(f"  {ret:+7.2f}%  |  {stats.total_trades:3d} trades  |  "
                  f"WR {stats.win_rate:5.1f}%  |  PF {stats.profit_factor:.3f}  |  "
                  f"DD {stats.max_drawdown_pct:.1f}%  |  "
                  f"AvgW ${stats.avg_win_usd:.2f}  AvgL ${stats.avg_loss_usd:.2f}")
        except Exception as e:
            print(f"  ERROR: {e}")

    resultados_fase4.sort(key=lambda x: x["ret"], reverse=True)

    print()
    print("  Top 3 RR ratios:")
    for i, r in enumerate(resultados_fase4[:3], 1):
        print(f"    {i}. {r['label']:<12}  {r['ret']:+.2f}%  |  WR {r['wr']:.1f}%  |  "
              f"PF {r['pf']:.3f}  |  DD {r['dd']:.1f}%  |  AvgW ${r['avg_win']:.2f}")

    mejor_rr = resultados_fase4[0]["rr"] if resultados_fase4 else 2.0

    # ── FASE 5: Re-test trailing con el RR óptimo ────────────────
    print()
    print(f"FASE 5: Trailing con RR optimo 1:{mejor_rr} ({mejor_symbol}/{mejor_tf}, "
          f"{mejor_perfil}, score={mejor_score}, ATR x{mejor_atr_m})")
    print("-" * 80)

    TRAILING_FINAL = ["none", "escalones", "atr", "hibrido"]

    resultados_fase5 = []
    for trailing in TRAILING_FINAL:
        label = f"RR={mejor_rr}+{trailing}"
        print(f"  {label:<25}", end="", flush=True)
        try:
            stats = correr_test(
                df_mejor, mejor_perfil, mejor_symbol, mejor_tf,
                args.capital, trailing_mode=trailing,
                score_min=mejor_score, atr_mult=mejor_atr_m,
                tp_rr=mejor_rr,
                max_open_trades=args.max_trades, racha_sl_reduce=args.racha_reduce,
            )
            ret = (stats.capital_final - stats.capital_inicial) / stats.capital_inicial * 100
            resultados_fase5.append({
                "label": label, "trailing": trailing,
                "trades": stats.total_trades, "wr": stats.win_rate,
                "pf": stats.profit_factor, "ret": ret,
                "dd": stats.max_drawdown_pct,
                "avg_rr": stats.avg_rr_real,
                "max_r": stats.max_r_capturado,
                "stats": stats,
            })
            print(f"  {ret:+7.2f}%  |  {stats.total_trades:3d} trades  |  "
                  f"WR {stats.win_rate:5.1f}%  |  PF {stats.profit_factor:.3f}  |  "
                  f"DD {stats.max_drawdown_pct:.1f}%  |  "
                  f"RR {stats.avg_rr_real:.1f}R  MaxR {stats.max_r_capturado:.1f}R")
        except Exception as e:
            print(f"  ERROR: {e}")

    resultados_fase5.sort(key=lambda x: x["ret"], reverse=True)

    mejor_trailing_final = resultados_fase5[0]["trailing"] if resultados_fase5 else mejor_trailing

    # ── RESUMEN FINAL ─────────────────────────────────────────────
    elapsed = time.time() - inicio

    print()
    print("=" * 80)
    print("  RESUMEN FINAL".center(80))
    print("=" * 80)

    if resultados_fase1:
        m = resultados_fase1[0]
        print(f"  Mejor mercado:      {m['symbol']}/{m['tf']}  ({m['ret']:+.2f}%)")

    if resultados_fase2:
        m = resultados_fase2[0]
        print(f"  Mejor combinacion:  {m['perfil']} + {m['trailing']}  ({m['ret']:+.2f}%)")

    if resultados_fase3:
        m = resultados_fase3[0]
        print(f"  Mejores parametros: score={m['score']}, ATR x{m['atr_mult']}  ({m['ret']:+.2f}%)")

    if resultados_fase4:
        m = resultados_fase4[0]
        print(f"  Mejor RR ratio:     1:{m['rr']}  ({m['ret']:+.2f}%)")

    if resultados_fase5:
        m = resultados_fase5[0]
        print(f"  Trailing con RR:    {m['trailing']}  ({m['ret']:+.2f}%)")

    print()
    print(f"  CONFIGURACION OPTIMA:")
    best_ret = resultados_fase5[0] if resultados_fase5 else (resultados_fase4[0] if resultados_fase4 else None)
    if best_ret:
        print(f"    Par:        {mejor_symbol}")
        print(f"    Timeframe:  {mejor_tf}")
        print(f"    Perfil:     {mejor_perfil}")
        print(f"    Trailing:   {mejor_trailing_final}")
        print(f"    Score min:  {mejor_score}")
        print(f"    ATR mult:   {mejor_atr_m}")
        print(f"    TP RR:      1:{mejor_rr}")
        print(f"    Resultado:  {best_ret['ret']:+.2f}%  |  {best_ret['trades']} trades  |  "
              f"WR {best_ret['wr']:.1f}%  |  PF {best_ret['pf']:.3f}")
        print()
        print(f"  .env recomendado:")
        print(f"    SYMBOL={mejor_symbol}")
        print(f"    TIMEFRAME={mejor_tf}")
        print(f"    SCORE_MINIMO={mejor_score}")
        print(f"    ATR_MULTIPLIER={mejor_atr_m}")
        print(f"    TP_RR_RATIO={mejor_rr}")

    print()
    print(f"  Tiempo total: {elapsed:.0f} segundos")
    total_tests = (len(resultados_fase1) + len(resultados_fase2) +
                   len(resultados_fase3) + len(resultados_fase4) + len(resultados_fase5))
    print(f"  Tests corridos: {total_tests}")

    # Exportar todos los stats
    all_stats = (
        [r["stats"] for r in resultados_fase1] +
        [r["stats"] for r in resultados_fase2] +
        [r["stats"] for r in resultados_fase3] +
        [r["stats"] for r in resultados_fase4] +
        [r["stats"] for r in resultados_fase5]
    )
    if all_stats:
        path = exportar_comparacion(all_stats, args.output)
        print(f"  JSON exportado: {path}")

    print("=" * 80)
    print()


if __name__ == "__main__":
    main()
