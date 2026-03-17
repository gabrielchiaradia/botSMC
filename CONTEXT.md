# SMC Futures Bot — Contexto del proyecto
# Fecha: 2026-03-17
# Usar este archivo para dar contexto a una nueva sesión de chat.

## Repo y acceso
- GitHub: https://github.com/gabrielchiaradia/botSMC (privado)
- VPS: Vultr, IP 167.179.114.168
- SSH: `ssh -i C:\Users\Gabriel\.ssh\id_ed25519 root@167.179.114.168` luego `su - botuser`
- Dashboard: http://167.179.114.168:8080
- El bot corre en Docker como `smc-bot` con `docker compose`
- El dashboard corre como container nginx separado (`smc-dashboard`)

## Estado actual del bot en producción
- Corriendo en **LIVE testnet** con Binance Futures
- Config: ETH/USDT, 15m LTF, 1h HTF, perfil ob_bos, RR 1:1.5, 3 trades max, ventana 13-16 UTC
- Leverage 3x, 1% riesgo por trade
- Notificaciones Telegram funcionando (hora local Argentina GMT-3)
- Balance testnet: $5,000 USDT

## Arquitectura del bot
- **strategy/smc_signals.py**: Detecta FVG, Order Blocks, BOS/CHoCH. Evalúa señal con scoring (0-100). Score ≥ 50 = señal válida.
- **strategy/indicators.py**: ATR, swing highs/lows, sesiones de trading, ventanas horarias.
- **strategy/profiles/**: Filtros adicionales. `ob_bos` es el recomendado (solo OBs con BOS previo).
- **backtest/engine.py**: Motor barra-a-barra. Soporta multi-posición (hasta N trades), trailing dinámico (4 modos), riesgo adaptativo (reduce trades tras racha SL), cooldown post-SL, límite pérdida diaria.
- **scripts/run_bot.py**: Bot en tiempo real. WebSocket para ambos TF. Soporta paper y live. Filtro de ventana horaria con alerta Telegram fuera de horario.
- **scripts/run_backtest.py**: CLI completo con --symbol, --timeframe, --dias, --strategy, --trailing, --max-trades, --windows, --score, --rr, --compare, --compare-trailing.
- **scripts/run_systematic.py**: Optimización en 5 fases (par+tf, perfil+trailing, score+ATR, RR ratio, trailing final). Soporta --symbol, --timeframe para saltar fase 1, y --windows.
- **scripts/compare_results.py**: Compara múltiples JSONs de backtest. Muestra tabla con RR, días, retorno, trades, WR, PF, DD. Soporta --sort, --top, --min-trades, --csv.
- **utils/trade_journal.py**: Multi-posición, racha SL adaptativa, registro de señales.
- **utils/telegram_notify.py**: Notificaciones de inicio, señal, trade abierto/cerrado, fuera de horario.
- **config/settings.py**: Todo configurable desde .env. TRADING_WINDOWS parseado como lista de tuplas.

## Configuración .env recomendada
```
SYMBOL=ETHUSDT
TIMEFRAME=15m
LEVERAGE=3
RISK_PER_TRADE=0.01
TP_RR_RATIO=1.5
ATR_MULTIPLIER=1.5
MAX_OPEN_TRADES=3
MAX_DAILY_LOSS=0.03
SCORE_MINIMO=50
FILTRO_SESION=true
SESIONES_ACTIVAS=london,new_york
TRADING_WINDOWS=13-16
TESTNET=true
MTF_ENABLED=true
MTF_HTF=1h
MTF_LTF=15m
WS_ENABLED=true
TZ=America/Argentina/Buenos_Aires
TELEGRAM_TOKEN=configurado
TELEGRAM_CHAT_ID=configurado
```

## Resultados de backtesting clave
### Config ganadora: ETH/15m, ob_bos, mt3, ventana 13-16, RR 1:1.5 (360 días)
- Retorno: +51.09%
- Win Rate: 69%
- Profit Factor: 2.78
- Max Drawdown: 5.1%
- 71 trades
- Señales filtradas: 797 (694 fuera de horario, 103 OB sin BOS)

### Comparación de RR (360 días, ob_bos, mt3, w13-16)
| RR  | Ret     | WR   | PF   | DD    |
|-----|---------|------|------|-------|
| 1.5 | +51.1%  | 69%  | 2.78 | 5.1%  |
| 2.0 | +38.8%  | 54%  | 2.00 | 7.8%  |
| 2.5 | +47.8%  | 50%  | 2.11 | 9.4%  |
| 3.0 | +57.6%  | 47%  | 2.30 | 12.1% |

### Comparación de ventanas (180 días, ob_bos, mt3)
| Ventana   | Ret     | WR   | PF   | DD    |
|-----------|---------|------|------|-------|
| 13-16     | +36.3%  | 66%  | 3.10 | 3.2%  |
| 13-17     | +43.9%  | 62%  | 2.64 | 4.3%  |
| 13-18     | +38.5%  | 55%  | 2.06 | 5.4%  |
| 8-11      | -10.8%  | 22%  | 0.45 | 10.8% |

### ETH/1h vs ETH/15m (360 días, mejor config)
| TF  | Ret     | WR   | PF   | DD    | Trades |
|-----|---------|------|------|-------|--------|
| 15m | +51.1%  | 69%  | 2.78 | 5.1%  | 71     |
| 1h  | +43.8%  | 37%  | 1.68 | 10.9% | 86     |

## Bugs conocidos resueltos
- `.loc[idx]` con DatetimeIndex duplicados → cambiado a `.iloc` en todos lados
- `on_senal` vs `on_señal` typo en run_bot.py → corregido
- Dashboard VPS: filtros Wins/Losses sobreescribían allTrades → separado en _originalTrades
- Dockerfile multi-stage: dependencias no accesibles por usuario botuser → simplificado a single-stage
- nginx.conf: regex roto → reemplazado con config simple

## Features pendientes / ideas futuras
1. **Trailing multi-timeframe**: Chequear trailing en TF menor al del trade (ej: trail en 5m para trades de 15m)
2. **Sesiones como parámetro del systematic**: Probar combinaciones de ventanas automáticamente
3. **Cloudflare Pages**: Subir dashboard a URL pública
4. **Walk-forward optimization**: Después de 30 días de paper trading
5. **Trailing en vivo**: Gestión dinámica de órdenes SL en Binance (cancelar y recrear)

## Estructura de archivos de documentación
- `setup-guide.html` — Guía de instalación (GitHub → VPS → Docker)
- `operations-guide.html` — Operación diaria del VPS (SSH, Docker, Git, .env)
- `backtesting-guide.html` — Referencia completa CLI + plan de pruebas
- `README.md` — Overview del proyecto

## Notas técnicas
- Los datos de Binance se descargan frescos cada vez (no cache entre corridas de backtest)
- El backtest usa `datetime.now()` (no UTC) para logs y Telegram, con TZ del .env
- El engine de backtest soporta hasta N trades simultáneos con pyramiding (misma dirección)
- Riesgo adaptativo: después de 3 SL consecutivos reduce a 1 trade, se restaura con WIN
- Las señales fuera de ventana horaria se evalúan completas (con niveles) pero se marcan como fuera_de_horario
- En el bot live, fuera de horario se notifica por Telegram pero no se opera
- El dashboard del VPS se corre como container nginx separado (no en docker-compose por bug de read-only filesystem)
