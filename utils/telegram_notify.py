"""
utils/telegram_notify.py
─────────────────────────
Notificaciones via Telegram Bot API.

Mensajes enviados:
  🔔 Nueva señal detectada  → al abrir trade
  ✅ Trade cerrado en TP    → con PnL y resumen
  🛑 Trade cerrado en SL    → con PnL y resumen
  📊 Resumen diario         → al detener el bot
  ⚠️  Error crítico          → si algo falla

Setup:
  1. Hablar con @BotFather en Telegram → /newbot → copiar TOKEN
  2. Enviar un mensaje al bot → abrir:
     https://api.telegram.org/bot<TOKEN>/getUpdates
     → copiar el "chat_id" del resultado
  3. Pegar en .env:
     TELEGRAM_TOKEN=xxxx
     TELEGRAM_CHAT_ID=xxxx
"""

from __future__ import annotations

import json
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime
from typing import Optional

from utils.logger import get_logger

logger = get_logger(__name__)


class TelegramNotifier:
    """
    Cliente liviano para Telegram Bot API.
    Usa solo urllib (sin dependencias extra).
    No bloquea el bot si Telegram no está disponible.
    """

    BASE_URL = "https://api.telegram.org/bot{token}/{method}"

    def __init__(self, token: str, chat_id: str):
        self.token   = token
        self.chat_id = chat_id
        self._habilitado = bool(token and chat_id)
        if not self._habilitado:
            logger.warning("Telegram no configurado (TOKEN o CHAT_ID vacíos). "
                           "Las notificaciones estarán desactivadas.")

    # ══════════════════════════════════════════════════════
    #  MENSAJES PRINCIPALES
    # ══════════════════════════════════════════════════════

    def señal_detectada(self, senal, capital: float,
                         tamanio: float, riesgo_usd: float,
                         modo: str = "PAPER") -> bool:
        """Notifica apertura de trade."""
        dir_emoji = "🟢 LONG" if senal.direccion == "ALCISTA" else "🔴 SHORT"
        modo_tag  = "📋 PAPER" if modo == "PAPER" else "⚡ LIVE"

        texto = (
            f"🔔 *Nueva Señal SMC*\n"
            f"{'─'*28}\n"
            f"*Par:*      `{senal.sesion.upper()} Session`\n"
            f"*Dirección:* {dir_emoji}\n"
            f"*Modo:*     {modo_tag}\n\n"
            f"💰 *Niveles*\n"
            f"Entrada:  `${senal.precio_entrada:,.2f}`\n"
            f"Stop Loss: `${senal.stop_loss:,.2f}`\n"
            f"Take Profit: `${senal.take_profit:,.2f}`\n\n"
            f"📐 *Riesgo*\n"
            f"Tamaño:  `{tamanio:.5f} BTC`\n"
            f"Riesgo:  `${riesgo_usd:.2f} USDT`\n"
            f"Capital: `${capital:,.2f} USDT`\n\n"
            f"📊 *Confluencias* (score: {senal.score}/100)\n"
        )
        for m in senal.motivos:
            texto += f"• {m}\n"

        texto += f"\n🕐 `{datetime.now().strftime('%Y-%m-%d %H:%M')} Local`"
        return self._enviar(texto)

    def trade_cerrado(self, trade, motivo: str) -> bool:
        """Notifica cierre de trade con resultado."""
        es_win    = trade.pnl_usd > 0
        emoji     = "✅" if es_win else "🛑"
        resultado = "WIN" if es_win else "LOSS"
        color_pnl = "+" if es_win else ""
        dir_str   = "LONG" if trade.direccion == "LONG" else "SHORT"

        texto = (
            f"{emoji} *Trade {resultado}*\n"
            f"{'─'*28}\n"
            f"*Dirección:* {dir_str}\n"
            f"*Motivo:*   {motivo}\n\n"
            f"💹 *Resultado*\n"
            f"Entrada:  `${trade.precio_entrada:,.2f}`\n"
            f"Salida:   `${trade.precio_salida:,.2f}`\n"
            f"PnL:      `{color_pnl}${trade.pnl_usd:.2f} USDT`\n"
            f"PnL %:    `{color_pnl}{trade.pnl_pct:.2f}%`\n"
            f"Capital:  `${trade.capital_out:,.2f} USDT`\n\n"
            f"⏱ Duración: `{trade.duracion_min} min`\n"
            f"🕐 `{datetime.now().strftime('%Y-%m-%d %H:%M')} Local`"
        )
        return self._enviar(texto)

    def resumen_diario(self, resumen: dict, modo: str = "PAPER") -> bool:
        """Notifica el resumen del día al detener el bot."""
        pnl       = resumen.get("pnl_usd", 0)
        color_pnl = "+" if pnl >= 0 else ""
        emoji_dia = "📈" if pnl >= 0 else "📉"

        texto = (
            f"{emoji_dia} *Resumen del Día*\n"
            f"{'─'*28}\n"
            f"*Modo:* {'📋 PAPER' if modo == 'PAPER' else '⚡ LIVE'}\n\n"
            f"*Trades:*  `{resumen.get('trades', 0)}`\n"
            f"*Wins:*    `{resumen.get('wins', 0)}`\n"
            f"*Losses:*  `{resumen.get('losses', 0)}`\n"
            f"*Win Rate:* `{resumen.get('win_rate', 0):.1f}%`\n\n"
            f"*PnL Total:* `{color_pnl}${pnl:.2f} USDT`\n\n"
            f"🕐 `{datetime.now().strftime('%Y-%m-%d %H:%M')} Local`"
        )
        return self._enviar(texto)

    def bot_iniciado(self, symbol: str, ltf: str, htf: str,
                      modo: str, capital: float) -> bool:
        """Notifica que el bot arrancó."""
        texto = (
            f"🚀 *SMC Bot Iniciado*\n"
            f"{'─'*28}\n"
            f"Par:     `{symbol}`\n"
            f"LTF:     `{ltf}` (entrada)\n"
            f"HTF:     `{htf}` (contexto)\n"
            f"Modo:    `{'PAPER' if modo == 'PAPER' else '⚡ LIVE'}`\n"
            f"Capital: `${capital:,.2f} USDT`\n"
            f"🕐 `{datetime.now().strftime('%Y-%m-%d %H:%M')} Local`"
        )
        return self._enviar(texto)

    def bot_detenido(self, motivo: str = "Manual") -> bool:
        """Notifica que el bot se detuvo."""
        texto = (
            f"🔴 *SMC Bot Detenido*\n"
            f"Motivo: {motivo}\n"
            f"🕐 `{datetime.now().strftime('%Y-%m-%d %H:%M')} Local`"
        )
        return self._enviar(texto)

    def error_critico(self, mensaje: str) -> bool:
        """Notifica un error crítico."""
        texto = (
            f"⚠️ *Error Crítico*\n"
            f"{'─'*28}\n"
            f"`{mensaje[:400]}`\n"
            f"🕐 `{datetime.now().strftime('%Y-%m-%d %H:%M')} Local`"
        )
        return self._enviar(texto)

    def sin_senal(self, precio: float, tendencia_ltf: str,
                   tendencia_htf: str, score: int,
                   motivos: list) -> bool:
        """Notifica ciclo sin señal (solo si score fue alto pero no suficiente)."""
        if score < 30:
            return True  # No notificar señales muy débiles
        texto = (
            f"⏳ *Sin señal* (score: {score}/100)\n"
            f"Precio: `${precio:,.2f}`\n"
            f"LTF: `{tendencia_ltf}` | HTF: `{tendencia_htf}`\n"
            f"• " + "\n• ".join(motivos[:3]) if motivos else ""
        )
        return self._enviar(texto)

    # ══════════════════════════════════════════════════════
    #  HTTP INTERNO
    # ══════════════════════════════════════════════════════

    def _enviar(self, texto: str, parse_mode: str = "Markdown") -> bool:
        """Envía un mensaje de texto. Retorna True si tuvo éxito."""
        if not self._habilitado:
            logger.debug("Telegram deshabilitado, mensaje no enviado.")
            return False

        url  = self.BASE_URL.format(token=self.token, method="sendMessage")

        for modo in [parse_mode, None]:
            payload = {
                "chat_id": self.chat_id,
                "text":    texto,
            }
            if modo:
                payload["parse_mode"] = modo

            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                url,
                data    = data,
                headers = {"Content-Type": "application/json"},
                method  = "POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    resultado = json.loads(resp.read().decode())
                    if resultado.get("ok"):
                        logger.debug("Telegram OK: %s", texto[:60])
                        return True
                    else:
                        logger.warning("Telegram error: %s", resultado)
            except urllib.error.URLError as e:
                if modo and "Bad Request" in str(e):
                    logger.debug("Markdown falló, reintentando sin parseo...")
                    continue
                logger.warning("Telegram no disponible: %s", e)
                return False

        return False

    def test_conexion(self) -> bool:
        """Verifica que el token y chat_id sean válidos."""
        if not self._habilitado:
            return False
        texto = "✅ SMC Bot conectado a Telegram correctamente."
        return self._enviar(texto)


# ══════════════════════════════════════════════════════════
#  FACTORY
# ══════════════════════════════════════════════════════════

def crear_notifier() -> TelegramNotifier:
    """Crea instancia usando credenciales del .env."""
    from config import creds
    return TelegramNotifier(
        token   = creds.TELEGRAM_TOKEN,
        chat_id = creds.TELEGRAM_CHAT_ID,
    )
