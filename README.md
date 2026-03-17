# 🤖 SMC Futures Bot v2

Bot de trading algorítmico para **Binance Futures** basado en **Smart Money Concepts (SMC)**.
Detecta Fair Value Gaps, Order Blocks y BOS/CHoCH en tiempo real y en backtesting.
Sistema de **perfiles de estrategia**, **trailing dinámico**, **multi-trade**, **ventanas horarias**, **riesgo adaptativo** y **multi-bot**.

## 🏆 Configuraciones validadas (360 días)

### Bot 1 — Conservador (15m, ob_bos, ventana 13-16)
```
SYMBOL=ETHUSDT | MTF_LTF=15m | MTF_HTF=1h | TP_RR_RATIO=1.5
MAX_OPEN_TRADES=3 | TRADING_WINDOWS=13-16 | Perfil: ob_bos
Resultado: +51% | 69% WR | PF 2.78 | DD 5.1% | 71 trades
```

### Bot 2 — Agresivo (1h, base, 24hs)
```
SYMBOL=ETHUSDT | TIMEFRAME=1h | MTF_ENABLED=false | TP_RR_RATIO=2.5
MAX_OPEN_TRADES=3 | TRADING_WINDOWS= (sin filtro) | Perfil: base
Resultado: +86% | 38% WR | PF 1.41 | DD 21.6% | 234 trades
```

## 🤖 Multi-Bot

El bot soporta múltiples instancias corriendo en paralelo, cada una con su propia configuración:

- **Bot 1** → lee `.env` (default)
- **Bot 2** → lee `.env2`
- **Bot N** → lee `.envN`

Cada bot tiene su propio capital asignado (`BOT_CAPITAL`), journal de trades, archivo de log, y tag en Telegram. Comparten la misma conexión Binance y el mismo código.

```bash
# Arrancar ambos bots
docker compose up -d smc-bot smc-bot-2 watchtower

# Ver logs de cada uno
docker compose logs -f smc-bot
docker compose logs -f smc-bot-2

# Parar solo uno
docker compose stop smc-bot-2

# Parar todo
docker compose down
```

## 🚀 Uso rápido

```bash
# Backtest
python scripts/run_backtest.py --symbol ETHUSDT --timeframe 15m --dias 360 \
  --strategy ob_bos --max-trades 3 --windows 13-16 --rr 1.5

# Backtest sin filtro de horario (--windows "" desactiva sesión automáticamente)
python scripts/run_backtest.py --symbol ETHUSDT --timeframe 1h --dias 360 \
  --max-trades 3 --windows "" --rr 2.5

# Optimización sistemática
python scripts/run_systematic.py --symbol ETHUSDT --timeframe 15m --dias 360 \
  --max-trades 3 --windows 13-16

# Comparar resultados
python scripts/compare_results.py --sort pf --top 5

# Bot en vivo (un solo bot)
python scripts/run_bot.py --live

# Bot 2 (lee .env2)
python scripts/run_bot.py --live --bot-number 2
```

## ⏰ Ventanas horarias

| Ventana | Horario UTC | Resultado |
|---|---|---|
| `13-16` | Apertura NY | **Mejor: PF 2.78, DD 5%** |
| `13-17` | NY extendido | Bueno: PF 2.64, DD 4.3% |
| *(vacío)* | 24hs sin filtro | Más trades, más DD |
| `8-11` | Apertura London | Malo: -10%, evitar |

## ⚙️ Variables clave del .env

| Variable | Descripción | Ejemplo |
|---|---|---|
| `BOT_TAG` | Nombre del bot (aparece en Telegram) | `15m-conservador` |
| `BOT_CAPITAL` | Capital asignado al bot (0 = balance real) | `500` |
| `TESTNET` | `true` = testnet, `false` = dinero real | `true` |
| `MTF_ENABLED` | Multi-timeframe on/off | `true` |
| `TRADING_WINDOWS` | Ventanas horarias UTC (vacío = 24h) | `13-16` |
| `FILTRO_SESION` | Filtrar por sesión London/NY | `true` |

## 📖 Documentación

- `setup-guide.html` — Instalación completa
- `operations-guide.html` — Operación del VPS (multi-bot, Docker, SSH)
- `backtesting-guide.html` — Todos los comandos de testing

## ⚠️ Disclaimer

Proyecto educativo. Trading con futuros conlleva riesgo de pérdida total del capital.
**TESTNET=false opera con dinero real. Verificá siempre esta variable antes de deployar.**
