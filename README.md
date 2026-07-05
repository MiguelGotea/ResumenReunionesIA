# resumen-reuniones-ia

Herramienta de grabación de reuniones corporativas con transcripción y resumen ejecutivo generado por IA (Gemini).

> **Estado**: 🔧 En implementación

---

## Arquitectura

```
ERP (Hostinger)                   API (Hostinger)                  VPS (DigitalOcean)
──────────────────────            ──────────────────────           ────────────────────────────
modulos/sistemas/                 api/resumen_reuniones_ia/        /opt/resumen-reuniones-ia/
resumen_reuniones.php  ────────▶  crear.php          ◀──────────▶  app/main.py (FastAPI)
(genera token, abre VPS)          obtener_por_token.php              app/audio.py (fragmentos)
                                  actualizar_estado.php              app/gemini_client.py
                                  guardar_resultado.php              app/static/ (UI grabación)
                                  obtener_key_gemini.php
                                  aprobar.php

BD MySQL (Hostinger)
  └── resumen_reuniones_ia
```

---

## Configuración

| Parámetro | Valor |
|-----------|-------|
| **Puerto FastAPI** | `8888` |
| **Ruta VPS** | `/opt/resumen-reuniones-ia/` |
| **Audio** | `/opt/resumen-reuniones-ia/audio/<id>/` |
| **Nombre servicio systemd** | `resumen-reuniones-ia` |
| **Dominio** | `reuniones.batidospitaya.com` |

---

## Variables de entorno requeridas

| Variable | Descripción |
|----------|-------------|
| `REUNIONES_API_TOKEN` | Token VPS → API (= `RESUMEN_TOKEN_VPS` en auth.php) |
| `REUNIONES_API_BASE_URL` | `https://api.batidospitaya.com/api/resumen_reuniones_ia` |
| `RESUMEN_TOKEN_ERP` | Token para verificar llamadas de borrado desde el ERP |
| `AUDIO_DIR` | `/opt/resumen-reuniones-ia/audio` |
| `PORT` | `8888` |

---

## Deploy — Primer setup (solo una vez)

```bash
# 1. Conectar al VPS
ssh root@198.211.97.243

# 2. Clonar el repo (o esperar al primer push de GitHub Actions)
mkdir -p /opt/resumen-reuniones-ia
cd /opt/resumen-reuniones-ia
# GitHub Actions hace el rsync en el primer push a main

# 3. Setup inicial (Python, ffmpeg, venv, systemd)
bash install/setup_vps.sh

# 4. Verificar/editar .env (los valores ya están en .env.example)
cat .env

# 5. Configurar nginx + SSL
bash install/setup.sh
```

## Deploy — Siguientes versiones

```bash
# Solo push a main — GitHub Actions despliega automáticamente
.\.scripts\gitpush.ps1 "descripción del cambio"
```

## Secrets de GitHub requeridos

| Secret | Valor |
|--------|-------|
| `DO_SSH_KEY` | Llave privada SSH (`id_ed25519`) |
| `DO_HOST` | `198.211.97.243` |
| `DO_USER` | `root` |
| `DO_PATH` | `/opt/resumen-reuniones-ia` |

---

## Comandos del VPS

```bash
# Estado del servicio
systemctl status resumen-reuniones-ia

# Logs en tiempo real
journalctl -u resumen-reuniones-ia -f

# Reiniciar
systemctl restart resumen-reuniones-ia

# Ver audios pendientes
ls -lh /opt/resumen-reuniones-ia/audio/

# Health check
curl https://reuniones.batidospitaya.com/health
```

---

## Flujo de una reunión

```
1. Usuario en ERP crea reunión → API genera token (6h) → retorna link
2. Browser abre reuniones.batidospitaya.com/?token=XYZ
3. Página valida token via GET /api/info/{token}
4. Usuario presiona Empezar → getUserMedia → MediaRecorder (60s chunks)
5. Cada 60s: POST /api/fragmento/{token} → chunk guardado en /audio/{id}/
6. Usuario presiona Finalizar → POST /api/finalizar/{token}
7. Background task: ffmpeg concatena → Gemini Files API → guardar_resultado.php
8. Estado pasa a 'completada' → resumen visible en el ERP
9. Usuario aprueba en ERP → aprobar.php → DELETE /api/audio/{id} → audio borrado
```

---

## Tokens de comunicación

Los tokens están documentados en `api/resumen_reuniones_ia/auth.php`.
- **RESUMEN_TOKEN_VPS** → va en `.env` como `REUNIONES_API_TOKEN`
- **RESUMEN_TOKEN_ERP** → va en `.env` como `RESUMEN_TOKEN_ERP`
