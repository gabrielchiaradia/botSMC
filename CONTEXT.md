# SMC Futures Bot — Contexto del proyecto
# Fecha: 2026-03-18
# Usar este archivo para dar contexto a una nueva sesión de chat.

## Repo y acceso
- GitHub: https://github.com/gabrielchiaradia/botSMC (privado)
- VPS: Vultr, IP 167.179.114.168
- SSH: `ssh -i C:\Users\Gabriel\.ssh\id_ed25519 root@167.179.114.168` luego `su - botuser`
- Dashboard: http://167.179.114.168:8080 (nginx, auth con .htpasswd)
- Working dir local: `C:\Users\Gabriel\Downloads\smc-bot-improved`
- Working dir VPS: `~/smc-bot`

## Estructura del proyecto
```
smc-bot-improved/
├── config/
│   ├── __init__.py          # Exporta settings, creds, exchange, strategy, etc.
│   └── settings.py          # Todo configurable desde .env. BOT_NUMBER, BOT_TAG,
│                             # StrategyConfig (STRATEGY, SCORE_MINIMO, TRADING_WINDOWS),
│                             # RiskConfig (BOT_CAPITAL, TP_RR_RATIO, MAX_OPEN_TRADES),
│                             # ExchangeConfig, MTFConfig, etc.
│
├── strategy/
│   ├── __init__.py           # Exporta analizar_mercado()
│   ├── smc_signals.py        # Core SMC: detecta FVG, Order Blocks, BOS/CHoCH.
│   │                         # Scoring 0-100. Score ≥ 50 = señal válida.
│   ├── indicators.py         # ATR, swing highs/lows, sesiones, ventanas horarias,
│   │                         # calcular_atr(), detectar_swings()
│   ├── mtf_analysis.py       # Multi-timeframe: LTF señal + HTF contexto
│   ├── risk.py               # Cálculo de tamaño, resumen de riesgo
│   └── profiles/
│       ├── __init__.py       # get_profile(), listar_perfiles(), PERFILES dict
│       ├── base_profile.py   # BaseProfile, FilterContext, FilterResult (clases base)
│       ├── profile_base.py   # ProfileBase — sin filtros, acepta toda señal ≥ score
│       ├── profile_ob_bos.py # ProfileObBos — OB validado por BOS previo.
│       │                     # OB_MAX_AGE: descarta OBs > N velas (env var)
│       ├── profile_ema_filter.py  # EMA 50/200 + distancia + confirmación vela
│       │                          # (filtros desactivados por default, necesitan env vars)
│       └── (ob_bos_ema en profile_ema_filter.py)  # Combo OB+BOS + EMA
│
├── backtest/
│   ├── __init__.py
│   ├── engine.py             # Motor barra-a-barra. Multi-posición (hasta N trades),
│   │                         # trailing dinámico (none/escalones/atr/hibrido),
│   │                         # riesgo adaptativo, cooldown post-SL, perfil de filtro,
│   │                         # exporta JSON con equity curve + trades.
│   │                         # pnl_pct calculado sobre capital_antes (no inicial).
│   └── results/              # JSONs de backtest (en .gitignore)
│       └── .gitkeep
│
├── scripts/
│   ├── run_bot.py            # Bot en tiempo real. WebSocket para LTF+HTF.
│   │                         # Paper y live mode. STRATEGY configurable desde .env.
│   │                         # Aplica perfil (ob_bos, etc.) a cada señal.
│   │                         # Multi-bot: --bot-number N lee .envN.
│   │                         # Soporta BOT_CAPITAL virtual.
│   │
│   ├── run_backtest.py       # CLI completo: --symbol, --timeframe, --dias, --strategy,
│   │                         # --trailing, --max-trades, --windows, --score, --rr,
│   │                         # --ob-max-age, --compare, --compare-trailing.
│   │
│   ├── run_systematic.py     # Optimización 5 fases: par+tf, perfil+trailing,
│   │                         # score+ATR, RR ratio, trailing final.
│   │
│   ├── run_interactive.py    # Backtest interactivo: pregunta todas las opciones,
│   │                         # permite múltiples valores por campo (productos cartesianos).
│   │                         # Genera manifest .txt + Excel .xlsx al finalizar.
│   │                         # Llama a compare_results.py con --from-file.
│   │
│   ├── run_grid.py           # Grid Bot Spot. Paper/live/backtest modes.
│   │                         # Auto-range (P5-P95 en backtest, ±15% en live).
│   │                         # Protecciones: stop loss, max loss USD, reposición.
│   │                         # Lee .env3 (bot-number 3 por default).
│   │
│   ├── compare_results.py    # Compara JSONs: tabla con Symbol, TF, Strategy, Windows,
│   │                         # Ret, Trades, WR, PF, DD. --sort por cualquier columna.
│   │                         # --from-file (lee manifest), --excel, --csv.
│   │
│   └── clean_results.py      # Elimina backtests por condiciones: --max-dd, --min-wr,
│                              # --min-ret, --min-pf, --min-trades. OR logic, --dry-run.
│
├── bot/
│   ├── __init__.py
│   └── websocket_stream.py   # MTFStream: WebSocket Binance para LTF + HTF.
│
├── data/
│   ├── __init__.py
│   └── fetcher.py            # obtener_velas(), obtener_historico_backtest(),
│                              # crear_cliente_futures(). Descarga de Binance.
│
├── utils/
│   ├── logger.py             # get_logger() con archivo rotativo y consola.
│   ├── telegram_notify.py    # TelegramNotifier: inicio, señal, trade, error, resumen.
│   │                         # Prefija todo con [BOT_TAG]. Fallback text si Markdown falla.
│   └── trade_journal.py      # TradeJournal: multi-posición, registro de señales,
│                              # exporta open_positions.json, per-bot filenames.
│
├── dashboard/
│   ├── index.html            # Dashboard live: tema dark/light, WebSocket PnL en tiempo real,
│   │                         # posiciones abiertas, sortable tables, log links header,
│   │                         # carga local + servidor, comparación systematic.
│   │                         # Trades limit: 500.
│   └── nginx.conf            # Config nginx con auth y autoindex para logs/backtest.
│
├── backtest_dashboard.html   # Dashboard backtest local: carga JSON, tema dark/light,
│                              # equity curve, PnL dist, hora/mes/sesión charts,
│                              # tabla de trades sortable, botón volver, duration legible,
│                              # nombre archivo visible.
│
├── deploy/
│   ├── setup_vps.sh          # Setup inicial VPS (Docker, botuser, firewall)
│   ├── setup_dashboard.sh    # Setup nginx dashboard con auth
│   ├── update.sh             # Script de actualización
│   └── monitor.sh            # Monitoreo del bot
│
├── docker-compose.yml        # smc-bot (.env), smc-bot-2 (.env2), smc-grid (.env3, comentado),
│                              # watchtower, dashboard nginx.
├── Dockerfile                # Python 3.11, pip install, corre run_bot.py --live
├── requirements.txt
├── .env3.example             # Template para grid bot
├── .gitignore                # .env[0-9]* ignorado, !.env*.example permitido
│
├── operations-guide.html     # Guía de operaciones VPS (SSH, Docker, Git, .env)
├── setup-guide.html          # Guía de instalación completa
├── backtesting-guide.html    # Referencia de backtesting
└── CONTEXT.md                # Este archivo
```

## Estado actual en producción
- **2 bots corriendo** en LIVE testnet con Binance Futures
- **Bot 1** (.env): ETH/15m, STRATEGY=ob_bos, MTF ON (LTF=15m, HTF=1h), ventana 13-16, RR 1.5, 3 max trades, $1000 capital
- **Bot 2** (.env2): ETH/15m, STRATEGY=base, MTF OFF, ventana 11-18, RR 2.5, 3 max trades, $500 capital
- **Grid bot** (.env3): implementado, no desplegado
- Leverage 3x, 1% riesgo por trade
- fail2ban activo (SSH 3 retries, nginx 10 retries)

## Estrategias (perfiles)
| Perfil | Descripción | Filtros |
|---|---|---|
| base | Sin filtros adicionales | Score ≥ 50 solamente |
| ob_bos | OB validado por BOS | OB requiere BOS previo en 30 velas. OB_MAX_AGE configurable |
| ema_filter | EMA 50/200 | Desactivado por default. Necesita EMA_FILTER_ENABLED=true |
| ob_bos_ema | ob_bos + ema_filter | Combina ambos |

## Resultados backtesting (360 días, ETHUSDT)

### Top configs validadas
| Config | Ret | WR | PF | DD | Ret/DD | Trades |
|---|---|---|---|---|---|---|
| 15m/ob_bos/13-16/RR 1.5 | +51% | 69% | 2.78 | 5.1% | 10.1x | 71 |
| 15m/base/11-18/RR 2.5 | +101% | 39% | 1.36 | 20.2% | 5.0x | 312 |
| 15m/ob_bos/11-18/RR 2.5 | +45% | 40% | 1.41 | 23.1% | 1.9x | 142 |

### Patrones confirmados
- 15m > 1h en eficiencia (Ret/DD)
- ob_bos siempre supera a base en mismo par/TF/ventana
- Ventana 13-16 (apertura NY) es la mejor
- ETH domina, otros pares mucho peores
- 24h sin filtro → alto DD en todos los pares

## Deploy commands
```bash
# Desde Windows
cd C:\Users\Gabriel\Downloads\smc-bot-improved
git add . && git commit -m "message" && git push

# En VPS
su - botuser && cd ~/smc-bot && git pull

# Rebuild (cambios de código)
docker compose down
docker compose build --no-cache smc-bot
docker compose up -d smc-bot smc-bot-2 watchtower
docker builder prune -af

# Solo .env changes (force recreate)
docker compose down && docker compose up -d smc-bot smc-bot-2 watchtower

# Dashboard (cambios HTML)
docker restart smc-dashboard

# Logs
docker logs smc-bot --tail 20
docker logs smc-bot-2 --tail 20
docker exec smc-bot-2 env | grep VARIABLE
```

## Variables .env clave
| Variable | Descripción | Ejemplo |
|---|---|---|
| STRATEGY | Perfil (base, ob_bos) | ob_bos |
| BOT_TAG | Nombre en Telegram/logs | 15m-conservador |
| BOT_CAPITAL | Capital virtual (0=real) | 1000 |
| TESTNET | true=testnet, false=real | true |
| MTF_ENABLED | Multi-timeframe | true |
| MTF_LTF / MTF_HTF | Timeframes | 15m / 1h |
| TRADING_WINDOWS | Ventanas UTC | 13-16 |
| TP_RR_RATIO | Risk:Reward | 1.5 |
| MAX_OPEN_TRADES | Trades simultáneos | 3 |
| RISK_PER_TRADE | % capital por trade | 0.01 |
| OB_MAX_AGE | Edad máx OB en velas | 25 |

## Notas técnicas importantes
- `.env` NO se sube a Git. Editar en VPS: `nano ~/smc-bot/.env`
- No poner comentarios inline en .env (Docker no los stripea)
- `docker compose restart` no siempre recarga env_file. Usar `down` + `up`.
- El backtest descarga datos frescos cada vez (no cache)
- pnl_pct se calcula sobre capital al momento del trade (no inicial)
- Señales fuera de ventana se evalúan pero no se operan (se notifican)
- Python 3.12 recomendado para backtests (3.14 tiene issues con pandas)

## Features pendientes
1. Walk-forward optimization
2. Trailing en vivo (gestión dinámica SL en Binance)
3. Grid bot testing y despliegue en VPS
4. Telegram /status command interactivo
5. OB_MAX_AGE testing con valores 15, 20, 25, 30
