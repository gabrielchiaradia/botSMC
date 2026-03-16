# 🤖 SMC Futures Bot

Bot de trading algorítmico para **Binance Futures** basado en **Smart Money Concepts (SMC)**.
Detecta Fair Value Gaps, Order Blocks y BOS/CHoCH en tiempo real y en backtesting.
Sistema de **perfiles de estrategia** intercambiables y **trailing dinámico** con 4 modos.

---

## 📁 Estructura del proyecto

```
smc-bot/
├── config/
│   ├── __init__.py
│   └── settings.py              # Toda la configuración (lee desde .env)
│
├── data/
│   ├── __init__.py
│   ├── fetcher.py               # Descarga de velas OHLCV y balance
│   └── cache/                   # Caché local (ignorado por git)
│
├── strategy/
│   ├── __init__.py
│   ├── indicators.py            # ATR, swings, sesiones (funciones puras)
│   ├── smc_signals.py           # FVG, Order Blocks, BOS, evaluador de señal
│   ├── mtf_analysis.py          # Análisis multi-timeframe HTF + LTF
│   ├── risk.py                  # Tamaño de posición, gestión de riesgo
│   └── profiles/
│       ├── base_profile.py      # Clase base abstracta para perfiles
│       ├── profile_base.py      # Perfil base (sin filtros extra)
│       ├── profile_ob_bos.py    # OB validado por BOS previo
│       └── profile_ema_filter.py # EMA 50/200 + distancia + confirmación
│
├── backtest/
│   ├── __init__.py
│   ├── engine.py                # Motor barra-a-barra con trailing dinámico
│   └── results/                 # JSONs exportados (ignorados por git)
│
├── bot/
│   ├── __init__.py
│   └── websocket_stream.py      # Stream en tiempo real via WebSocket
│
├── utils/
│   ├── __init__.py
│   ├── logger.py                # Logger centralizado con color + archivo
│   ├── trade_journal.py         # Registro de señales y trades en JSON
│   └── telegram_notify.py       # Notificaciones Telegram
│
├── scripts/
│   ├── run_backtest.py          # Entrada: backtest + comparación de perfiles/trailing
│   └── run_bot.py               # Entrada: bot en tiempo real
│
├── deploy/
│   ├── setup_vps.sh             # Setup completo del VPS desde cero
│   ├── setup_dashboard.sh       # Configura nginx + contraseña del dashboard
│   ├── cloudflare_upload.sh     # Sube dashboard a Cloudflare Pages
│   ├── monitor.sh               # Monitoreo + alertas Telegram (cron)
│   ├── update.sh                # Git pull + rebuild + restart
│   └── README_DEPLOY.md         # Guía de deploy paso a paso
│
├── dashboard/
│   ├── index.html               # Dashboard web (carga JSONs automáticamente)
│   └── nginx.conf               # Config nginx para el VPS
│
├── tests/
│   └── test_strategy.py         # Tests unitarios (pytest)
│
├── Dockerfile                   # Imagen Docker multi-stage
├── docker-compose.yml           # Bot + nginx dashboard + Watchtower
├── backtest_dashboard.html      # Dashboard standalone (arrastrar JSON)
├── setup-guide.html             # Guía interactiva de instalación y uso
├── .env.example                 # Plantilla de credenciales → copiar a .env
├── .gitignore
└── requirements.txt
```

---

## ⚙️ Setup

### 1. Clonar e instalar dependencias

```bash
git clone https://github.com/tu-usuario/smc-bot.git
cd smc-bot
python -m venv venv
source venv/bin/activate        # Linux/Mac
# venv\Scripts\activate         # Windows
pip install -r requirements.txt
```

### 2. Configurar credenciales

```bash
cp .env.example .env
# Editar .env con tu editor preferido
```

Obtén tus API keys en [testnet.binancefuture.com](https://testnet.binancefuture.com) (gratuito).

> ⚠️ **Nunca subas `.env` a Git.** Ya está en `.gitignore`.

### 3. Configurar Telegram (opcional)

1. Hablar con [@BotFather](https://t.me/BotFather) → `/newbot` → copiar el TOKEN
2. Enviar un mensaje al bot, luego abrir:
   `https://api.telegram.org/bot<TOKEN>/getUpdates` y copiar el `chat_id`
3. Pegar en `.env`: `TELEGRAM_TOKEN` y `TELEGRAM_CHAT_ID`

---

## 🚀 Uso local

### Backtest

```bash
# Básico (perfil base, sin trailing)
python scripts/run_backtest.py
python scripts/run_backtest.py --dias 60 --capital 500
python scripts/run_backtest.py --symbol ETHUSDT --timeframe 1h

# Con perfil de estrategia
python scripts/run_backtest.py --strategy ob_bos
python scripts/run_backtest.py --strategy ema_filter

# Con trailing dinámico
python scripts/run_backtest.py --trailing escalones
python scripts/run_backtest.py --trailing atr --atr-mult 2.5
python scripts/run_backtest.py --trailing hibrido

# Combinar perfil + trailing
python scripts/run_backtest.py --strategy ob_bos --trailing hibrido

# Comparar perfiles (mismos datos, tabla lado a lado)
python scripts/run_backtest.py --compare
python scripts/run_backtest.py --compare --profiles base ob_bos

# Comparar los 4 modos de trailing
python scripts/run_backtest.py --compare-trailing
python scripts/run_backtest.py --compare-trailing --strategy ob_bos

# Listar perfiles disponibles
python scripts/run_backtest.py --list-profiles
```

Genera JSONs en `backtest/results/`.
Abrí `backtest_dashboard.html` en el navegador y arrastrá el JSON.

### Bot en vivo

```bash
python scripts/run_bot.py           # Paper trading + WebSocket + MTF
python scripts/run_bot.py --poll    # Paper trading con polling REST
python scripts/run_bot.py --live    # Órdenes reales en Testnet
```

> **Nota:** El trailing dinámico funciona solo en backtest por ahora.
> En vivo, Binance ejecuta las órdenes TP/SL como OCO estáticas.

### Tests

```bash
pytest tests/ -v
pytest tests/ -v --cov=strategy --cov-report=term-missing
```

---

## 🐳 Deploy en VPS (Vultr Tokyo)

### Setup completo en un comando

```bash
# Conectarse al VPS recién creado
ssh root@TU_IP_VPS

# Setup automático (instala Docker, configura firewall, clona el repo)
REPO_URL=https://github.com/TU_USUARIO/smc-bot.git \
  bash <(curl -fsSL https://raw.githubusercontent.com/TU_USUARIO/smc-bot/main/deploy/setup_vps.sh)
```

### Iniciar el bot

```bash
ssh botuser@TU_IP_VPS
nano ~/smc-bot/.env          # Completar credenciales
cd ~/smc-bot
docker compose up -d --build
docker compose logs -f smc-bot
```

### Dashboard web

```bash
# Opción A: nginx en el VPS (http://TU_IP:8080, siempre actualizado)
bash ~/smc-bot/deploy/setup_dashboard.sh

# Opción B: Cloudflare Pages (URL pública .pages.dev)
# Configurar CF_API_TOKEN y CF_ACCOUNT_ID en .env, luego:
bash ~/smc-bot/deploy/cloudflare_upload.sh
```

### Comandos del día a día

```bash
docker compose logs -f smc-bot              # Logs en tiempo real
docker compose restart smc-bot              # Reiniciar
~/smc-bot/deploy/update.sh                  # Actualizar desde Git
tail -50 ~/smc-bot/logs/smc_bot.log         # Últimas líneas del log
docker stats smc-bot                         # Uso de recursos
```

### Monitoreo automático (cron)

```bash
crontab -e
# Agregar:
*/5 * * * * ~/smc-bot/deploy/monitor.sh >> ~/smc-bot/logs/monitor.log 2>&1
0 * * * *   ~/smc-bot/deploy/cloudflare_upload.sh >> ~/smc-bot/logs/cf_upload.log 2>&1
0 3 * * *   ~/smc-bot/deploy/update.sh --no-build >> ~/smc-bot/logs/update.log 2>&1
```

---

## 🧠 Lógica de la estrategia

### Single timeframe (STF)

```
Score base (0–100):
  Precio en FVG en dirección de tendencia   → +35 pts
  Precio en Order Block en dirección        → +40 pts
  BOS confirmado en dirección               → +25 pts
  Score ≥ 50 → señal válida

Niveles:
  SL  = precio ± ATR × ATR_MULTIPLIER (default 1.5)
  TP  = SL × TP_RR_RATIO (default 1:2)
  Tam = (capital × RISK_PER_TRADE) / distancia_SL
```

### Multi-timeframe (MTF)

```
HTF (1h) ajusta el score del LTF (5m):
  HTF alineado con LTF          → +20 pts
  HTF opuesto a LTF             → -30 pts (cancela la señal)
  Precio en zona descuento/premium HTF → +10 pts
  FVG del HTF cerca del precio  → +15 pts
  OB  del HTF cerca del precio  → +15 pts
  TP ajustado al swing más cercano del HTF
```

---

## 🎯 Perfiles de estrategia

Los perfiles son filtros adicionales que se aplican después de la señal SMC base.
Permiten testear distintas combinaciones sin tocar el código core.

| Perfil | Qué hace |
|---|---|
| `base` | SMC puro: FVG + OB + BOS. Sin filtros extra. Baseline. |
| `ob_bos` | Solo acepta OBs precedidos por un BOS. Menos señales, más calidad. |
| `ema_filter` | EMA 50/200 en HTF + distancia al precio + vela de confirmación. |
| `ob_bos_ema` | Combina OB+BOS con filtros EMA. El más restrictivo. |

Para crear un perfil nuevo: heredar de `BaseProfile`, implementar `filtros()`, registrar en `profiles/__init__.py`.

---

## 🔄 Trailing dinámico

En lugar de TP fijo en 2R, el sistema adapta SL y TP en tiempo real según el movimiento del precio.

| Modo | Comportamiento | Ideal para |
|---|---|---|
| `none` | TP/SL fijos (RR 1:2 estático) | Baseline, mercados laterales |
| `escalones` | Cada 1R a favor, SL sube 1R. Sin TP fijo. Captura 3R, 4R, 5R+ | Tendencias fuertes |
| `atr` | SL sigue al precio a N×ATR. Sin TP fijo. Se adapta a la volatilidad | Movimientos explosivos |
| `hibrido` | Cierra 50% en TP original (2R) + 50% corre con trailing escalones | Uso general recomendado |

### Ejemplo: modo escalones (LONG)

```
Entrada $100, SL original $98, distancia 1R = $2

Precio → $102  →  SL sube a $100 (breakeven)
Precio → $104  →  SL sube a $102 (+1R asegurado)
Precio → $106  →  SL sube a $104 (+2R asegurado)
Precio revierte a $104  →  cierra = ganancia +2R

Si el precio seguía a $110 → capturaba +4R en vez del 2R fijo.
```

### Métricas nuevas

| Métrica | Qué mide |
|---|---|
| `Avg RR Real` | RR promedio real logrado (no el teórico) |
| `Max R Capturado` | Máximo R que alcanzó un trade individual |

> **Nota:** El trailing funciona en backtest. En vivo, las órdenes son TP/SL fijos por OCO de Binance.

---

## 🛡️ Mejoras v2 del motor de backtest

### Correcciones

| Fix | Detalle |
|---|---|
| BOS por posición | Corregido slicing que usaba labels en vez de `iloc` |
| Resolución TP/SL | Si ambos se tocan en la misma vela, asume SL primero (conservador) |
| Comisiones por tramo | Entrada sobre `precio_entrada`, salida sobre `precio_cierre` |
| Sharpe por timeframe | Anualización correcta según barras/año del timeframe real |
| Mitigación de FVGs | FVGs ya tocados se excluyen de señales futuras |

### Nuevas funcionalidades

| Feature | Detalle | Default |
|---|---|---|
| Cooldown post-SL | Espera N velas después de un SL antes de operar | `--cooldown 3` |
| Límite pérdida diaria | Si la pérdida del día supera el umbral, para hasta mañana | `MAX_DAILY_LOSS=0.03` |
| Volatilidad mínima | Descarta señales si ATR < 0.1% del precio | Siempre activo |
| Trailing dinámico | 4 modos de trailing TP/SL adaptativo | `--trailing none` |

---

## 📊 Dashboard

El dashboard HTML es compatible con tres fuentes:

| Archivo | Origen | Acceso |
|---|---|---|
| `backtest/results/backtest_results.json` | `run_backtest.py` | Arrastrar al dashboard standalone |
| `logs/dashboard_trades_YYYYMMDD.json` | `run_bot.py` | Arrastrar o carga automática en VPS |
| Servidor (nginx/Cloudflare) | Ambos | Carga automática con click |

---

## 📁 Archivos generados por el bot

Todos en `logs/`, rotados por día:

| Archivo | Contenido |
|---|---|
| `signals_YYYYMMDD.json` | Cada señal evaluada (entre o no) |
| `trades_YYYYMMDD.json` | Trades con todos los campos |
| `dashboard_trades_YYYYMMDD.json` | Formato dashboard |
| `smc_bot.log` | Log de texto completo |
| `monitor.log` | Log del script de monitoreo |

---

## 🗺️ Roadmap

- [x] **Fase 1** — Bot básico: Testnet, FVG/OB/BOS, paper trading
- [x] **Fase 2** — Backtesting con métricas + dashboard interactivo
- [x] **Refactor** — Modularización, .env, estructura lista para Git
- [x] **Fase 3** — Multi-timeframe + Telegram + WebSocket
- [x] **Fase 4** — Deploy VPS con Docker + dashboard web
- [x] **Fase 4.5** — Perfiles de estrategia + trailing dinámico + mejoras v2 del engine
- [ ] **Fase 5** — Trailing dinámico en bot en vivo (gestión dinámica de órdenes en Binance)
- [ ] **Fase 6** — Optimización walk-forward (después de 30 días paper trading)

---

## ⚠️ Disclaimer

Este proyecto es educativo.
El trading con futuros y apalancamiento conlleva **riesgo de pérdida total del capital**.
Realizá backtesting extenso y paper trading antes de usar dinero real.
