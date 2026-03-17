#!/usr/bin/env python3
"""
scripts/run_interactive.py
──────────────────────────
Backtest interactivo: pregunta todas las opciones y corre
combinaciones múltiples automáticamente.

Uso:
    python scripts/run_interactive.py
"""

import subprocess
import sys
import os
from pathlib import Path
from itertools import product

# Colores
GREEN  = "\033[32m"
YELLOW = "\033[33m"
CYAN   = "\033[36m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
RESET  = "\033[0m"

# Valores por defecto y opciones
SYMBOLS_DEFAULT   = ["ETHUSDT", "BTCUSDT", "SOLUSDT"]
TIMEFRAMES_ALL    = ["5m", "15m", "1h", "4h"]
STRATEGIES_ALL    = ["base", "ob_bos", "ema_filter", "ob_bos_ema"]
TRAILING_ALL      = ["none", "escalones", "atr", "hibrido"]


def ask(prompt, default="", options=None, multi=False, tipo=str):
    """Pregunta interactiva con opciones y soporte multi-valor."""
    hint = ""
    if options:
        hint = f" {DIM}[{', '.join(options)}]{RESET}"
    if default:
        hint += f" {DIM}(default: {default}){RESET}"
    if multi:
        hint += f" {DIM}(separar con coma para múltiples){RESET}"

    raw = input(f"\n{CYAN}▸ {prompt}{hint}\n  > {RESET}").strip()

    if not raw:
        raw = str(default)

    if multi:
        values = [v.strip() for v in raw.split(",") if v.strip()]
        if not values:
            values = [str(default)] if default else []
        # Validar contra opciones
        if options:
            valid = []
            for v in values:
                if v in options:
                    valid.append(v)
                else:
                    print(f"  {YELLOW}⚠ '{v}' no es válido, ignorado{RESET}")
            values = valid if valid else [str(default)]
        return [tipo(v) for v in values]
    else:
        if options and raw not in options:
            print(f"  {YELLOW}⚠ '{raw}' no es válido, usando default: {default}{RESET}")
            raw = str(default)
        return tipo(raw) if raw else tipo(default)


def ask_yn(prompt, default="s"):
    """Pregunta sí/no."""
    hint = " (S/n)" if default == "s" else " (s/N)"
    raw = input(f"\n{CYAN}▸ {prompt}{hint}\n  > {RESET}").strip().lower()
    if not raw:
        return default == "s"
    return raw in ("s", "si", "sí", "y", "yes")


def build_command(symbol, timeframe, dias, capital, strategy, trailing,
                  max_trades, windows, rr, score):
    """Construye el comando de backtest."""
    cmd = [
        sys.executable, "scripts/run_backtest.py",
        "--symbol", symbol,
        "--timeframe", timeframe,
        "--dias", str(dias),
        "--capital", str(capital),
        "--strategy", strategy,
        "--trailing", trailing,
        "--max-trades", str(max_trades),
    ]
    if windows is not None:
        cmd.extend(["--windows", windows])
    if rr is not None:
        cmd.extend(["--rr", str(rr)])
    if score is not None:
        cmd.extend(["--score", str(score)])
    return cmd


def format_combo(i, total, symbol, tf, strategy, trailing, rr, windows):
    """Formatea la descripción de una combinación."""
    parts = [f"{symbol}/{tf}", strategy, f"trail:{trailing}"]
    if rr:
        parts.append(f"RR 1:{rr}")
    if windows is not None:
        parts.append(f"w:{windows}" if windows else "24h")
    return f"[{i}/{total}] {' · '.join(parts)}"


def main():
    print(f"\n{'='*60}")
    print(f"{BOLD}{'  SMC BACKTEST INTERACTIVO':^60}{RESET}")
    print(f"{'='*60}")
    print(f"{DIM}  Configurá las opciones. Usá coma para múltiples valores.{RESET}")
    print(f"{DIM}  Enter = valor por defecto.{RESET}")

    # ── Preguntas ──────────────────────────────────────────
    symbols    = ask("Symbols", default="ETHUSDT",
                     options=SYMBOLS_DEFAULT + ["BNBUSDT", "XRPUSDT", "ADAUSDT", "DOGEUSDT"],
                     multi=True)

    timeframes = ask("Timeframes", default="15m",
                     options=TIMEFRAMES_ALL, multi=True)

    dias_list  = ask("Días de historia", default="360",
                     multi=True, tipo=int)

    capital    = ask("Capital inicial USDT", default="5000", tipo=float)

    strategies = ask("Estrategias", default="base",
                     options=STRATEGIES_ALL, multi=True)

    trailings  = ask("Trailing modes", default="none",
                     options=TRAILING_ALL, multi=True)

    max_trades_list = ask("Max trades simultáneos", default="1",
                          multi=True, tipo=int)

    # Windows
    use_windows = ask_yn("¿Definir ventanas horarias? (si no, usa .env)")
    windows_list = [None]
    if use_windows:
        raw = input(f"\n{CYAN}▸ Ventanas UTC (ej: 13-16). Vacío = 24h. Separar con ; para múltiples\n  > {RESET}").strip()
        if raw:
            windows_list = [w.strip() for w in raw.split(";")]
        else:
            windows_list = [""]  # vacío = 24h sin filtro

    # RR
    use_rr = ask_yn("¿Definir RR ratio? (si no, usa .env)")
    rr_list = [None]
    if use_rr:
        rr_list = ask("RR ratios", default="1.5",
                       multi=True, tipo=float)

    # Score
    use_score = ask_yn("¿Definir score mínimo? (si no, usa .env)", default="n")
    score_list = [None]
    if use_score:
        score_list = ask("Score mínimo", default="50",
                          multi=True, tipo=int)

    # ── Calcular combinaciones ─────────────────────────────
    combos = list(product(
        symbols, timeframes, dias_list, strategies, trailings,
        max_trades_list, windows_list, rr_list, score_list
    ))

    print(f"\n{'─'*60}")
    print(f"{BOLD}  RESUMEN{RESET}")
    print(f"{'─'*60}")
    print(f"  Symbols:     {', '.join(symbols)}")
    print(f"  Timeframes:  {', '.join(timeframes)}")
    print(f"  Días:        {', '.join(str(d) for d in dias_list)}")
    print(f"  Capital:     ${capital:,.0f}")
    print(f"  Estrategias: {', '.join(strategies)}")
    print(f"  Trailing:    {', '.join(trailings)}")
    print(f"  Max trades:  {', '.join(str(m) for m in max_trades_list)}")
    print(f"  Windows:     {', '.join(str(w) for w in windows_list)}")
    print(f"  RR:          {', '.join(str(r) for r in rr_list)}")
    print(f"  Score:       {', '.join(str(s) for s in score_list)}")
    print(f"\n  {BOLD}{GREEN}Total combinaciones: {len(combos)}{RESET}")
    print(f"{'─'*60}")

    if not ask_yn("¿Ejecutar?"):
        print("  Cancelado.\n")
        return

    # ── Ejecutar ───────────────────────────────────────────
    resultados = []
    errores = []
    results_dir = Path(__file__).resolve().parents[1] / "backtest" / "results"

    # Snapshot de archivos antes de empezar
    archivos_antes = set(results_dir.glob("*.json")) if results_dir.exists() else set()

    for i, (sym, tf, dias, strat, trail, mt, win, rr, sc) in enumerate(combos, 1):
        desc = format_combo(i, len(combos), sym, tf, strat, trail, rr, win)
        print(f"\n{'═'*60}")
        print(f"  {GREEN}{desc}{RESET}")
        print(f"{'═'*60}")

        cmd = build_command(sym, tf, dias, capital, strat, trail, mt, win, rr, sc)

        try:
            result = subprocess.run(
                cmd,
                cwd=str(Path(__file__).resolve().parents[1]),
                timeout=600,  # 10 min max por backtest
            )
            if result.returncode == 0:
                resultados.append(desc)
            else:
                errores.append((desc, f"Exit code {result.returncode}"))
        except subprocess.TimeoutExpired:
            errores.append((desc, "Timeout (>10 min)"))
            print(f"  {YELLOW}⚠ Timeout, saltando...{RESET}")
        except Exception as e:
            errores.append((desc, str(e)))
            print(f"  {YELLOW}⚠ Error: {e}{RESET}")

    # ── Detectar archivos nuevos ───────────────────────────
    archivos_despues = set(results_dir.glob("*.json")) if results_dir.exists() else set()
    archivos_nuevos = sorted(archivos_despues - archivos_antes)

    # ── Resumen final ──────────────────────────────────────
    print(f"\n\n{'═'*60}")
    print(f"{BOLD}{'  RESUMEN FINAL':^60}{RESET}")
    print(f"{'═'*60}")
    print(f"  {GREEN}✓ Completados: {len(resultados)}/{len(combos)}{RESET}")
    if errores:
        print(f"  {YELLOW}✗ Errores: {len(errores)}{RESET}")
        for desc, err in errores:
            print(f"    · {desc}: {err}")

    print(f"  Archivos generados: {len(archivos_nuevos)}")
    print(f"{'═'*60}")

    # ── Tabla comparativa ──────────────────────────────────
    if len(archivos_nuevos) >= 2:
        print(f"\n  {CYAN}Generando tabla comparativa...{RESET}\n")
        try:
            compare_cmd = [
                sys.executable, "scripts/compare_results.py",
                "--sort", "pf",
            ] + [str(f) for f in archivos_nuevos]
            subprocess.run(
                compare_cmd,
                cwd=str(Path(__file__).resolve().parents[1]),
            )
        except Exception as e:
            print(f"  {YELLOW}⚠ No se pudo generar comparación: {e}{RESET}")
    elif len(archivos_nuevos) == 1:
        print(f"\n  Resultado: {archivos_nuevos[0].name}")

    print(f"\n  Los resultados están en backtest/results/")
    print(f"  Abrí el dashboard para visualizarlos.\n")


if __name__ == "__main__":
    main()
