# 🤖 SMC Futures Bot v2

Bot de trading algorítmico para **Binance Futures** basado en **Smart Money Concepts (SMC)**.
Detecta Fair Value Gaps, Order Blocks y BOS/CHoCH en tiempo real y en backtesting.

Multi-bot, multi-estrategia, grid bot spot, backtesting interactivo, dashboards con tema dark/light.

## 🏆 Configuraciones validadas (ETHUSDT, 360 días)

| Config | Ret | WR | PF | DD | Ret/DD | Trades |
|---|---|---|---|---|---|---|
| 15m/ob_bos/w:13-16/RR 1.5 | **+51%** | 69% | 2.78 | 5.1% | **10.1x** | 71 |
| 15m/base/w:11-18/RR 2.5 | +101% | 39% | 1.36 | 20.2% | 5.0x | 312 |
| 15m/ob_bos/w:13-16/RR 2.5 | +48% | 50% | 2.11 | 9.4% | 5.1x | 66 |

## 🤖 Multi-Bot

| Bot | .env | Estrategia | Config |
|---|---|---|---|
| Bot 1 (conservador) | `.env` | ob_bos | 15m, MTF, w:13-16, RR 1.5 |
| Bot 2 (agresivo) | `.env2` | base | 15m, no MTF, w:11-18, RR 2.5 |
| Grid Bot (spot) | `.env3` | grid | Auto-range, N niveles |

```bash
docker compose up -d smc-bot smc-bot-2 watchtower
docker compose logs -f smc-bot        # logs bot 1
docker compose logs -f smc-bot-2      # logs bot 2
docker compose down                    # parar todo
```

## 🚀 Uso rápido

```bash
# Backtest simple
python scripts/run_backtest.py --symbol ETHUSDT --timeframe 15m --dias 360 \
  --strategy ob_bos --max-trades 3 --windows 13-16 --rr 1.5

# Backtest interactivo (pregunta todo, corre combinaciones, genera Excel)
python scripts/run_interactive.py

# Comparar resultados
python scripts/compare_results.py --sort pf --top 10 --excel resultados.xlsx
python scripts/compare_results.py --from-file backtest/results/_run_XXX.txt

# Limpiar resultados malos
python scripts/clean_results.py --max-dd 20 --min-pf 1.0 --dry-run

# Grid bot backtest
python scripts/run_grid.py --backtest --dias 180 --symbol ETHUSDT --count 15

# Grid bot paper
python scripts/run_grid.py

# Bot live
python scripts/run_bot.py --live
python scripts/run_bot.py --live --bot-number 2
```

## 📊 Estrategias

| Perfil | Descripción |
|---|---|
| `base` | Sin filtros. Acepta toda señal SMC con score ≥ 50 |
| `ob_bos` | OB solo válido si precedido por BOS. Soporta `OB_MAX_AGE` |
| `ema_filter` | EMA 50/200 + distancia + vela confirmación (desactivado por default) |
| `ob_bos_ema` | Combina ob_bos + ema_filter |

## ⚙️ Variables clave del .env

| Variable | Descripción | Ejemplo |
|---|---|---|
| `STRATEGY` | Perfil de estrategia | `ob_bos` |
| `BOT_TAG` | Nombre en Telegram/logs | `15m-conservador` |
| `BOT_CAPITAL` | Capital virtual (0 = balance real) | `1000` |
| `TESTNET` | true = testnet, false = real | `true` |
| `MTF_ENABLED` | Multi-timeframe on/off | `true` |
| `MTF_LTF` / `MTF_HTF` | Timeframes | `15m` / `1h` |
| `TRADING_WINDOWS` | Ventanas horarias UTC | `13-16` |
| `TP_RR_RATIO` | Risk:Reward | `1.5` |
| `MAX_OPEN_TRADES` | Trades simultáneos | `3` |
| `OB_MAX_AGE` | Edad máx del OB en velas | `25` |

## 📖 Documentación

- `CONTEXT.md` — Contexto completo para iniciar nueva sesión de chat
- `operations-guide.html` — Operación del VPS (SSH, Docker, Git, .env)
- `setup-guide.html` — Instalación completa
- `backtesting-guide.html` — Referencia de backtesting

## ⚠️ Disclaimer

Proyecto educativo. Trading con futuros conlleva riesgo de pérdida total del capital.
**TESTNET=false opera con dinero real. Verificá siempre esta variable antes de deployar.**
