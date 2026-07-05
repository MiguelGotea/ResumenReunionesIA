#!/bin/bash
# =============================================================================
# setup_vps.sh — Setup inicial del VPS para resumen-reuniones-ia
# Idempotente: puede correrse múltiples veces sin romper nada.
#
# Uso (primera vez, desde el VPS via SSH):
#   cd /opt/resumen-reuniones-ia
#   bash install/setup_vps.sh
# =============================================================================
set -euo pipefail

APP_DIR="/opt/resumen-reuniones-ia"
AUDIO_DIR="$APP_DIR/audio"
VENV_DIR="$APP_DIR/venv"
SERVICE_NAME="resumen-reuniones-ia"
SERVICE_FILE="$APP_DIR/install/systemd/$SERVICE_NAME.service"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${YELLOW}  Setup VPS: resumen-reuniones-ia${NC}"
echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

# ─── Paso 1) Python 3.11+ ─────────────────────────────────────────────────────
echo "[1/6] Verificando Python 3.11+..."
if ! command -v python3.11 &>/dev/null && ! (python3 --version 2>&1 | grep -qE "3\.(11|12|13)"); then
    echo "      Python 3.11+ no encontrado — instalando via deadsnakes PPA..."
    apt-get update -qq
    apt-get install -y software-properties-common
    add-apt-repository -y ppa:deadsnakes/ppa
    apt-get update -qq
    apt-get install -y python3.11 python3.11-venv python3.11-distutils
    echo "      ✓ Python 3.11 instalado"
else
    PY_VER=$(python3 --version 2>&1)
    echo "      ✓ Python disponible: $PY_VER"
fi

# ─── Paso 2) ffmpeg ───────────────────────────────────────────────────────────
echo "[2/6] Verificando ffmpeg..."
if ! command -v ffmpeg &>/dev/null; then
    echo "      ffmpeg no encontrado — instalando..."
    apt-get update -qq
    apt-get install -y ffmpeg
    echo "      ✓ ffmpeg instalado ($(ffmpeg -version 2>&1 | head -1))"
else
    echo "      ✓ ffmpeg ya instalado ($(ffmpeg -version 2>&1 | head -1))"
fi

# ─── Paso 3) Carpeta de audio ─────────────────────────────────────────────────
echo "[3/6] Preparando directorios..."
mkdir -p "$AUDIO_DIR"
chmod 755 "$AUDIO_DIR"
echo "      ✓ Directorio de audio: $AUDIO_DIR"

# ─── Paso 4) Virtual environment ─────────────────────────────────────────────
echo "[4/6] Configurando virtualenv Python..."
if [ ! -d "$VENV_DIR" ]; then
    echo "      Creando venv en $VENV_DIR..."
    if command -v python3.11 &>/dev/null; then
        python3.11 -m venv "$VENV_DIR"
    else
        python3 -m venv "$VENV_DIR"
    fi
    echo "      ✓ venv creado"
else
    echo "      ✓ venv ya existe"
fi

echo "      Instalando/actualizando dependencias..."
"$VENV_DIR/bin/pip" install --upgrade pip --quiet
"$VENV_DIR/bin/pip" install -r "$APP_DIR/requirements.txt" --quiet
echo "      ✓ Dependencias instaladas"

# ─── Paso 5) .env ─────────────────────────────────────────────────────────────
echo "[5/6] Verificando .env..."
if [ ! -f "$APP_DIR/.env" ]; then
    if [ -f "$APP_DIR/.env.example" ]; then
        cp "$APP_DIR/.env.example" "$APP_DIR/.env"
        echo -e "      ${YELLOW}⚠️  Se creó .env desde .env.example${NC}"
        echo -e "      ${YELLOW}   Los valores ya están prellenados — verifica que son correctos.${NC}"
    else
        echo -e "      ${RED}❌ No se encontró .env.example. Crea el .env manualmente.${NC}"
        exit 1
    fi
else
    echo "      ✓ .env ya existe"
fi

# ─── Paso 6) systemd service ─────────────────────────────────────────────────
echo "[6/6] Configurando servicio systemd..."
if [ ! -f "$SERVICE_FILE" ]; then
    echo -e "      ${RED}❌ No se encontró: $SERVICE_FILE${NC}"
    exit 1
fi

cp "$SERVICE_FILE" "/etc/systemd/system/$SERVICE_NAME.service"
systemctl daemon-reload
systemctl enable "$SERVICE_NAME"

# Si el servicio no está corriendo, iniciarlo
if ! systemctl is-active --quiet "$SERVICE_NAME"; then
    systemctl start "$SERVICE_NAME"
    sleep 2
    if systemctl is-active --quiet "$SERVICE_NAME"; then
        echo "      ✓ Servicio iniciado"
    else
        echo -e "      ${RED}❌ El servicio no arrancó. Ver: journalctl -u $SERVICE_NAME -n 20${NC}"
    fi
else
    systemctl restart "$SERVICE_NAME"
    echo "      ✓ Servicio reiniciado"
fi

echo ""
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}  ✅ Setup VPS completado${NC}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo "  Próximos pasos:"
echo "    1. Editar .env si los valores no son correctos:"
echo "       nano $APP_DIR/.env"
echo ""
echo "    2. Configurar nginx + SSL:"
echo "       bash $APP_DIR/install/setup.sh"
echo ""
echo "  Comandos útiles:"
echo "    systemctl status $SERVICE_NAME"
echo "    journalctl -u $SERVICE_NAME -f"
echo "    systemctl restart $SERVICE_NAME"
