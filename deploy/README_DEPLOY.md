# 🚀 Guía de Deploy — VPS (Vultr Tokyo)

Guía completa para poner el SMC Bot en producción en un VPS Linux.

---

## 1. Crear el VPS en Vultr

1. Entrar a [vultr.com](https://vultr.com) → **Deploy New Server**
2. Elegir:
   - **Type**: Cloud Compute — High Frequency
   - **Location**: 🇯🇵 Tokyo, Japan *(más cercano a los servidores de Binance)*
   - **OS**: Ubuntu 22.04 LTS x64
   - **Plan**: 1 vCPU / 1GB RAM / 32GB NVMe → **~$6/mes**
3. Agregar tu **SSH key** pública (recomendado, más seguro que contraseña)
4. Hacer clic en **Deploy Now**

> Esperar ~60 segundos hasta que aparezca como **Running**.

---

## 2. Conectarse al VPS

```bash
ssh root@TU_IP_VPS
```

---

## 3. Setup automático del servidor

```bash
# Opción A: Ejecutar directamente desde GitHub
curl -fsSL https://raw.githubusercontent.com/TU_USUARIO/smc-bot/main/deploy/setup_vps.sh | bash

# Opción B: Subir y ejecutar manualmente
scp deploy/setup_vps.sh root@TU_IP:/root/
ssh root@TU_IP "bash /root/setup_vps.sh"
```

El script hace todo esto automáticamente:
- Actualiza el sistema operativo
- Crea un usuario `botuser` sin privilegios de root
- Configura el firewall (solo puerto SSH abierto)
- Instala Docker y Docker Compose
- Clona el repositorio
- Habilita el servicio para que arranque al reboot

---

## 4. Configurar credenciales

```bash
# Conectarse como botuser
ssh botuser@TU_IP_VPS

# Editar el .env con las credenciales reales
nano ~/smc-bot/.env
```

Completar obligatoriamente:
```env
BINANCE_API_KEY=tu_key_real
BINANCE_API_SECRET=tu_secret_real
TESTNET=false           # Cambiar a false para dinero real
TELEGRAM_TOKEN=tu_token
TELEGRAM_CHAT_ID=tu_chat_id
```

> ⚠️ **Importante:** En Binance, al crear la API key para el VPS,
> agregar la IP del VPS en la whitelist de IPs permitidas.
> Esto evita que la key funcione desde otro lugar si alguien la roba.

---

## 5. Iniciar el bot

```bash
cd ~/smc-bot

# Build y arranque
docker compose up -d --build

# Verificar que está corriendo
docker compose ps

# Ver logs en tiempo real
docker compose logs -f smc-bot
```

---

## 6. Configurar monitoreo automático (cron)

```bash
crontab -e
```

Agregar estas líneas:
```cron
# Monitoreo cada 5 minutos: reinicia el bot si se cae y alerta por Telegram
*/5 * * * * /home/botuser/smc-bot/deploy/monitor.sh >> /home/botuser/smc-bot/logs/monitor.log 2>&1

# Actualización automática desde Git todos los días a las 3am UTC
0 3 * * * /home/botuser/smc-bot/deploy/update.sh --no-build >> /home/botuser/smc-bot/logs/update.log 2>&1
```

---

## 7. Comandos del día a día

```bash
# Ver logs en tiempo real
docker compose -f ~/smc-bot/docker-compose.yml logs -f smc-bot

# Ver último resumen del bot
tail -50 ~/smc-bot/logs/smc_bot.log

# Ver trades del día en el dashboard
# → Descargar logs/dashboard_trades_YYYYMMDD.json
# → Abrir backtest_dashboard.html y arrastrar el archivo

# Detener el bot
docker compose -f ~/smc-bot/docker-compose.yml down

# Reiniciar el bot
docker compose -f ~/smc-bot/docker-compose.yml restart smc-bot

# Actualizar código desde Git y reiniciar
~/smc-bot/deploy/update.sh

# Ver uso de recursos del contenedor
docker stats smc-bot
```

---

## 8. Actualizar el código

Cada vez que hagas cambios y los subas a Git:

```bash
# Desde el VPS
~/smc-bot/deploy/update.sh

# O si querés solo pull sin rebuild (cambios que no tocan requirements.txt)
~/smc-bot/deploy/update.sh --no-build
```

Watchtower también lo hace automáticamente si pusheás a un registry (GHCR/DockerHub).

---

## 9. Copiar archivos entre VPS y tu máquina

```bash
# Descargar logs del día desde tu PC local
scp botuser@TU_IP:~/smc-bot/logs/dashboard_trades_$(date +%Y%m%d).json ./

# Subir un .env actualizado
scp .env botuser@TU_IP:~/smc-bot/.env
```

---

## 10. Seguridad recomendada

```bash
# Deshabilitar login con contraseña (solo SSH key)
sudo sed -i 's/#PasswordAuthentication yes/PasswordAuthentication no/' /etc/ssh/sshd_config
sudo systemctl restart sshd

# Cambiar puerto SSH (opcional, reduce intentos de fuerza bruta)
# sudo sed -i 's/#Port 22/Port 2222/' /etc/ssh/sshd_config
# sudo ufw allow 2222/tcp && sudo ufw delete allow 22/tcp
```

---

## Arquitectura del deploy

```
Vultr VPS (Tokyo)
├── Ubuntu 22.04 LTS
├── Docker Engine
│   ├── smc-bot (container)      ← el bot, restart: unless-stopped
│   │   ├── /app/logs            ← montado como volumen
│   │   └── /app/backtest/results
│   └── watchtower (container)   ← auto-update desde registry
├── cron
│   ├── */5min → monitor.sh     ← reinicia si cae, alerta Telegram
│   └── 3am    → update.sh      ← git pull + restart
└── systemd smc-bot.service      ← docker compose up al reboot
```
