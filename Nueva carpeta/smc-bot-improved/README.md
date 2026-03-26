# 🤖 SMC Futures Bot v2

Bot de trading algorítmico para **Binance Futures** basado en **Smart Money Concepts (SMC)**.
Detecta Fair Value Gaps, Order Blocks y BOS/CHoCH en tiempo real y en backtesting.
Sistema de **perfiles de estrategia**, **trailing dinámico**, **multi-trade**, **ventanas horarias** y **riesgo adaptativo**.

## 🏆 Configuración óptima (validada 360 días)

```
SYMBOL=ETHUSDT | TIMEFRAME=15m | TP_RR_RATIO=1.5 | MAX_OPEN_TRADES=3
TRADING_WINDOWS=13-16 | Perfil: ob_bos
Resultado: +51% | 69% WR | PF 2.78 | DD 5.1% | 71 trades
```

## 🚀 Uso rápido

```bash
# Backtest
python scripts/run_backtest.py --symbol ETHUSDT --timeframe 15m --dias 360 \
  --strategy ob_bos --max-trades 3 --windows 13-16 --rr 1.5

# Optimización sistemática
python scripts/run_systematic.py --symbol ETHUSDT --timeframe 15m --dias 360 \
  --max-trades 3 --windows 13-16

# Comparar resultados
python scripts/compare_results.py --sort pf --top 5

# Bot en vivo
python scripts/run_bot.py --live
```

## ⏰ Ventanas horarias

| Ventana | Horario UTC | Resultado |
|---|---|---|
| `13-16` | Apertura NY | **Mejor: PF 2.78, DD 5%** |
| `8-11` | Apertura London | Malo: -10%, evitar |

## 📖 Documentación

- `setup-guide.html` — Instalación completa
- `operations-guide.html` — Operación del VPS
- `backtesting-guide.html` — Todos los comandos de testing

## ⚠️ Disclaimer

Proyecto educativo. Trading con futuros conlleva riesgo de pérdida total del capital.
