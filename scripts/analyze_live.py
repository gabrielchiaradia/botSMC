"""
analyze_live.py
────────────────────────────────────────────────────────────
Compara lo que ejecutó el bot SMC en Binance Testnet
vs lo que debería haber hecho según backtest reciente.

Uso:
    python3 analyze_live.py                  # últimos 30 días
    python3 analyze_live.py --dias 60
    python3 analyze_live.py --symbol BTCUSDT
"""

import os
import sys
import hmac
import hashlib
import time
import argparse
import requests
from datetime import datetime, timezone, timedelta
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ── Colores ───────────────────────────────────────────────────────────────────
class K:
    G = "\033[92m"; R = "\033[91m"; Y = "\033[93m"
    C = "\033[96m"; B = "\033[1m";  D = "\033[2m"; X = "\033[0m"

# ── Config desde .env ─────────────────────────────────────────────────────────
API_KEY    = os.getenv("BINANCE_API_KEY", "")
API_SECRET = os.getenv("BINANCE_API_SECRET", "")
TESTNET    = os.getenv("TESTNET", "true").lower() in ("true", "1", "yes")

BASE_URL = "https://testnet.binancefuture.com" if TESTNET else "https://fapi.binance.com"

if not API_KEY or not API_SECRET:
    print(f"\n  {K.R}✗ BINANCE_API_KEY / BINANCE_API_SECRET no encontradas en .env{K.X}\n")
    sys.exit(1)

# ── Binance API helpers ───────────────────────────────────────────────────────
def _sign(params: dict) -> dict:
    query = "&".join(f"{k}={v}" for k, v in params.items())
    sig   = hmac.new(API_SECRET.encode(), query.encode(), hashlib.sha256).hexdigest()
    params["signature"] = sig
    return params

def _get(endpoint: str, params: dict = None) -> dict | list:
    params = params or {}
    params["timestamp"] = int(time.time() * 1000)
    params = _sign(params)
    r = requests.get(
        f"{BASE_URL}{endpoint}",
        params=params,
        headers={"X-MBX-APIKEY": API_KEY},
        timeout=10,
    )
    r.raise_for_status()
    return r.json()

# ── Obtener datos de cuenta ───────────────────────────────────────────────────
def get_balance() -> float:
    data = _get("/fapi/v2/account")
    for asset in data.get("assets", []):
        if asset["asset"] == "USDT":
            return float(asset["walletBalance"])
    return 0.0

def get_closed_trades(symbol: str, days: int) -> list:
    since_ms = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp() * 1000)
    trades   = []
    try:
        raw = _get("/fapi/v1/userTrades", {"symbol": symbol, "limit": 1000, "startTime": since_ms})
        trades = raw if isinstance(raw, list) else []
    except Exception as e:
        print(f"  {K.Y}⚠ Error obteniendo trades: {e}{K.X}")
    return trades

def get_orders(symbol: str, days: int) -> list:
    since_ms = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp() * 1000)
    try:
        raw = _get("/fapi/v1/allOrders", {"symbol": symbol, "limit": 1000, "startTime": since_ms})
        return raw if isinstance(raw, list) else []
    except Exception as e:
        print(f"  {K.Y}⚠ Error obteniendo órdenes: {e}{K.X}")
        return []

def get_income(symbol: str, days: int) -> list:
    since_ms = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp() * 1000)
    try:
        raw = _get("/fapi/v1/income", {"symbol": symbol, "incomeType": "REALIZED_PNL",
                                        "limit": 1000, "startTime": since_ms})
        return raw if isinstance(raw, list) else []
    except Exception as e:
        print(f"  {K.Y}⚠ Error obteniendo income: {e}{K.X}")
        return []

# ── Análisis ──────────────────────────────────────────────────────────────────
def analyze_live(symbol: str, days: int):
    env_label = f"{K.Y}TESTNET{K.X}" if TESTNET else f"{K.R}LIVE{K.X}"
    print(f"\n{K.C}{'═'*62}{K.X}")
    print(f"  {K.B}📊 ANÁLISIS LIVE — {symbol} | {days} días | {env_label}{K.X}")
    print(f"{K.C}{'═'*62}{K.X}")

    # ── Balance ───────────────────────────────────────────────────────────────
    try:
        balance = get_balance()
        bal_c = K.G if balance >= 1000 else K.Y
        print(f"\n  💰 {K.B}Balance USDT:{K.X} {bal_c}{balance:.2f} USDT{K.X}")
    except Exception as e:
        print(f"  {K.R}✗ Error conectando: {e}{K.X}")
        return

    # ── Trades ejecutados ─────────────────────────────────────────────────────
    print(f"\n  {K.B}📋 TRADES EJECUTADOS (últimos {days} días){K.X}")
    print(f"  {K.D}{'─'*55}{K.X}")

    trades = get_closed_trades(symbol, days)
    income = get_income(symbol, days)

    if not trades:
        print(f"  {K.D}  Sin trades ejecutados.{K.X}")
    else:
        # Agrupar por día
        by_day: dict = {}
        for t in trades:
            dt  = datetime.fromtimestamp(t["time"] / 1000, tz=timezone.utc)
            day = dt.date().isoformat()
            by_day.setdefault(day, []).append(t)

        total_fees = sum(float(t.get("commission", 0)) for t in trades)
        total_qty  = len(trades)

        print(f"  {'FECHA':<12} {'TRADES':>6}  {'VOLUMEN':>12}  {'FEES':>10}")
        print(f"  {K.D}{'─'*50}{K.X}")
        for day in sorted(by_day.keys()):
            dt = by_day[day]
            vol  = sum(float(t["qty"]) * float(t["price"]) for t in dt)
            fees = sum(float(t.get("commission", 0)) for t in dt)
            print(f"  {day:<12} {len(dt):>6}  {K.C}{vol:>12,.2f}{K.X}  {K.R}{fees:>10.4f}{K.X}")
        print(f"  {K.D}{'─'*50}{K.X}")
        print(f"  {'TOTAL':<12} {K.B}{total_qty:>6}{K.X}  {'':>12}  {K.R}{total_fees:>10.4f}{K.X}")

    # ── PnL realizado ─────────────────────────────────────────────────────────
    print(f"\n  {K.B}💹 PnL REALIZADO{K.X}")
    print(f"  {K.D}{'─'*55}{K.X}")

    if not income:
        print(f"  {K.D}  Sin PnL registrado.{K.X}")
    else:
        by_day_pnl: dict = {}
        for i in income:
            dt  = datetime.fromtimestamp(int(i["time"]) / 1000, tz=timezone.utc)
            day = dt.date().isoformat()
            by_day_pnl.setdefault(day, []).append(float(i["income"]))

        total_pnl = sum(float(i["income"]) for i in income)
        wins  = sum(1 for i in income if float(i["income"]) > 0)
        losses= sum(1 for i in income if float(i["income"]) < 0)
        win_sum  = sum(float(i["income"]) for i in income if float(i["income"]) > 0)
        loss_sum = abs(sum(float(i["income"]) for i in income if float(i["income"]) < 0))
        pf   = round(win_sum / loss_sum, 2) if loss_sum > 0 else float("inf")
        wr   = round(wins / max(wins + losses, 1) * 100, 1)

        print(f"  {'FECHA':<12} {'CLOSES':>6}  {'PnL NETO':>12}")
        print(f"  {K.D}{'─'*35}{K.X}")
        for day in sorted(by_day_pnl.keys()):
            pnls = by_day_pnl[day]
            dpnl = sum(pnls)
            pc   = K.G if dpnl > 0 else K.R
            print(f"  {day:<12} {len(pnls):>6}  {pc}{dpnl:>+12.2f}{K.X}")
        print(f"  {K.D}{'─'*35}{K.X}")

        pnl_c = K.G if total_pnl > 0 else K.R
        pf_c  = K.G if pf >= 1.3 else (K.Y if pf >= 1.0 else K.R)
        wr_c  = K.G if wr >= 55 else (K.Y if wr >= 45 else K.R)
        pf_s  = f"{pf:.2f}" if pf != float("inf") else "∞"

        print(f"\n  💹 {K.B}PnL Total:{K.X}     {pnl_c}{total_pnl:>+10.2f} USDT{K.X}")
        print(f"  📈 {K.B}Win Rate:{K.X}      {wr_c}{wr}%{K.X}  ({wins}W / {losses}L)")
        print(f"  ⚖️  {K.B}Profit Factor:{K.X} {pf_c}{pf_s}{K.X}")

    # ── Órdenes rechazadas ────────────────────────────────────────────────────
    print(f"\n  {K.B}🚫 ÓRDENES RECHAZADAS / EXPIRADAS{K.X}")
    print(f"  {K.D}{'─'*55}{K.X}")

    orders = get_orders(symbol, days)
    rejected = [o for o in orders if o.get("status") in ("REJECTED", "EXPIRED", "CANCELED")]
    filled   = [o for o in orders if o.get("status") == "FILLED"]
    new_ord  = [o for o in orders if o.get("status") == "NEW"]

    print(f"  ✅ Órdenes FILLED:   {K.G}{len(filled)}{K.X}")
    print(f"  ❌ Rechazadas:       {K.R}{len([o for o in orders if o.get('status') == 'REJECTED'])}{K.X}")
    print(f"  ⏱️  Expiradas:        {K.Y}{len([o for o in orders if o.get('status') == 'EXPIRED'])}{K.X}")
    print(f"  🚫 Canceladas:       {K.Y}{len([o for o in orders if o.get('status') == 'CANCELED'])}{K.X}")
    print(f"  ⏳ Activas (NEW):    {K.C}{len(new_ord)}{K.X}")

    if rejected:
        print(f"\n  {K.D}Últimas rechazadas:{K.X}")
        for o in rejected[-5:]:
            dt = datetime.fromtimestamp(o["time"] / 1000, tz=timezone.utc).strftime("%m-%d %H:%M")
            print(f"  {K.D}  {dt} | {o['side']:<5} | {o['type']:<20} | {o['status']}{K.X}")

    print(f"\n{K.C}{'═'*62}{K.X}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Análisis live SMC vs Binance")
    parser.add_argument("--dias",   type=int, default=30,       help="Días a analizar (default: 30)")
    parser.add_argument("--symbol", type=str, default="ETHUSDT", help="Par a analizar (default: ETHUSDT)")
    args = parser.parse_args()

    analyze_live(symbol=args.symbol, days=args.dias)
