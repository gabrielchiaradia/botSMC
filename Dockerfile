# ══════════════════════════════════════════════════════════
#  Dockerfile — SMC Futures Bot
# ══════════════════════════════════════════════════════════

FROM python:3.11-slim

LABEL maintainer="smc-bot"
LABEL description="SMC Futures Bot — Binance Futures"
LABEL version="4.0"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app \
    TZ=UTC

WORKDIR /app

# Instalar dependencias
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar código fuente
COPY config/     ./config/
COPY data/       ./data/
COPY strategy/   ./strategy/
COPY backtest/   ./backtest/
COPY bot/        ./bot/
COPY utils/      ./utils/
COPY scripts/    ./scripts/

# Crear directorios de runtime
RUN mkdir -p /app/logs /app/backtest/results /app/data/cache

# Punto de entrada
ENTRYPOINT ["python", "scripts/run_bot.py"]
CMD []
