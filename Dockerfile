# ══════════════════════════════════════════════════════════
#  Dockerfile — SMC Futures Bot
#  Build multi-stage: dependencias separadas del código
#  para imagen final liviana (~200MB)
# ══════════════════════════════════════════════════════════

# ── Stage 1: build de dependencias ───────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build

# Instalar dependencias de compilación
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --upgrade pip && \
    pip install --user --no-cache-dir -r requirements.txt


# ── Stage 2: imagen final ─────────────────────────────────
FROM python:3.11-slim

# Metadata
LABEL maintainer="smc-bot"
LABEL description="SMC Futures Bot — Binance Futures"
LABEL version="3.0"

# Variables de entorno base
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app \
    TZ=UTC

WORKDIR /app

# Copiar dependencias instaladas desde el builder
COPY --from=builder /root/.local /root/.local
ENV PATH=/root/.local/bin:$PATH

# Copiar código fuente (sin credenciales — vienen de secrets/env)
COPY config/     ./config/
COPY data/       ./data/
COPY strategy/   ./strategy/
COPY backtest/   ./backtest/
COPY bot/        ./bot/
COPY utils/      ./utils/
COPY scripts/    ./scripts/

# Crear directorios de runtime (logs y resultados se montan como volúmenes)
RUN mkdir -p /app/logs /app/backtest/results /app/data/cache

# Usuario no-root para seguridad
RUN useradd -m -u 1000 botuser && \
    chown -R botuser:botuser /app
USER botuser

# Healthcheck — verifica que el proceso Python siga corriendo
HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import sys; sys.exit(0)" || exit 1

# Punto de entrada
ENTRYPOINT ["python", "scripts/run_bot.py"]
CMD []
