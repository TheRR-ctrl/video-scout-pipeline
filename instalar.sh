#!/data/data/com.termux/files/usr/bin/bash
# Instalación completa en un teléfono desde cero.
#
#   pkg install -y git && \
#   git clone -b claude/contenido-automatico-gemini-vhga6y \
#     https://github.com/TheRR-ctrl/video-scout-pipeline && \
#   bash video-scout-pipeline/instalar.sh
#
# Es idempotente: correrlo otra vez actualiza en vez de duplicar, así que
# sirve igual para instalar y para poner al día.
#
# Lo que git NO trae (y por qué): las credenciales y el material pesado están
# en .gitignore a propósito. Subir una clave de API a un repo es un error que
# no se deshace —queda en el historial—, y los videos de fondo son cientos de
# megas que no pintan nada en git. Al final este script te dice exactamente
# qué falta y cómo traerlo.

set -e
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO"

azul()  { printf '\033[36m%s\033[0m\n' "$1"; }
verde() { printf '\033[32m  ✓ %s\033[0m\n' "$1"; }
falta() { printf '\033[33m  ○ %s\033[0m\n' "$1"; }

echo
azul "  Instalando en:  $REPO"
echo

# ---- 1. Paquetes del sistema ---------------------------------------------
# ffmpeg hace el video; python lo orquesta; git trae las actualizaciones.
# termux-api es el que evita que Android congele el panel al cambiarte a
# Chrome, que es el fallo más molesto de todo esto.
azul "  1/5  Paquetes del sistema"
pkg update -y >/dev/null 2>&1 || true
for p in python ffmpeg git termux-api; do
  if pkg list-installed 2>/dev/null | grep -q "^$p/"; then
    verde "$p"
  else
    echo "       instalando $p…"
    pkg install -y "$p" >/dev/null
    verde "$p"
  fi
done

# ---- 2. Acceso al almacenamiento -----------------------------------------
# Sin esto no hay ~/storage, y ahí es donde acaban los videos para que los
# veas desde la galería y los puedas subir a TikTok a mano.
azul "  2/5  Acceso al almacenamiento del teléfono"
if [ -d "$HOME/storage" ]; then
  verde "ya concedido"
else
  echo "       Android va a pedirte permiso — dale a Permitir."
  termux-setup-storage
  sleep 2
  [ -d "$HOME/storage" ] && verde "concedido" || falta "sin permiso: los videos se quedarán solo dentro de Termux"
fi

# ---- 3. Librerías de Python ----------------------------------------------
# Pillow compila desde fuente en Android si no hay rueda, y sin estos dos
# paquetes de desarrollo falla a medias con un error que no dice nada.
azul "  3/5  Librerías de Python"
if ! python -c "import PIL" 2>/dev/null; then
  pkg install -y libjpeg-turbo zlib >/dev/null 2>&1 || true
fi
pip install --upgrade pip >/dev/null 2>&1 || true
pip install -r requirements.txt
verde "requirements.txt instalado"

# ---- 4. El panel ----------------------------------------------------------
azul "  4/5  Panel web"
bash instalar_panel.sh

# ---- 5. Qué falta ---------------------------------------------------------
# Esta es la parte que de verdad importa: el código ya está, pero sin esto
# el pipeline arranca y se cae en el primer paso.
echo
azul "  5/5  Lo que git no trae"
echo

pendientes=0

if [ -f secretos.env ]; then
  verde "secretos.env (claves de Gemini y Jamendo)"
else
  falta "secretos.env — sin esto no hay guiones ni metadata"
  echo "        Créalo con:"
  echo "          printf 'GEMINI_API_KEY=tu_clave\\n' > secretos.env && chmod 600 secretos.env"
  echo "        La clave es gratis en https://aistudio.google.com/apikey"
  echo "        (o desde el panel: Ajustes → Credenciales)"
  pendientes=$((pendientes+1))
fi

if [ -f client_secret.json ]; then
  verde "client_secret.json (OAuth de YouTube)"
else
  falta "client_secret.json — sin esto no se puede subir a YouTube"
  echo "        Descárgalo de Google Cloud Console y pásalo a esta carpeta."
  pendientes=$((pendientes+1))
fi

if [ -f youtube_token.json ]; then
  verde "youtube_token.json (sesión de YouTube)"
else
  falta "youtube_token.json — se genera una vez:  python generar_youtube_token.py"
  pendientes=$((pendientes+1))
fi

fondos=$(ls fondo_*.mp4 fondo_*.webm fondo_*.mkv 2>/dev/null | wc -l)
if [ "$fondos" -gt 0 ]; then
  verde "$fondos video(s) de fondo"
else
  falta "ningún video de fondo — el render no tiene qué poner detrás"
  echo "        Copia los tuyos aquí con nombre fondo_vertical*.mp4"
  echo "        Si están en Descargas:  cp ~/storage/downloads/fondo_*.mp4 ."
  pendientes=$((pendientes+1))
fi

musica=$(ls musica_*.mp3 musica_*.m4a 2>/dev/null | wc -l)
if [ "$musica" -gt 0 ]; then
  verde "$musica pista(s) de música"
else
  falta "sin música de fondo —  python actualizar_musica.py  descarga las de Jamendo"
  pendientes=$((pendientes+1))
fi

if [ -f config.json ]; then
  verde "config.json"
else
  # No cuenta como pendiente: el primer render lo escribe con los valores
  # por defecto. Se menciona solo para que no extrañe no verlo.
  printf '\033[90m  · config.json — aún no existe; se crea solo en el primer render\033[0m\n'
fi

echo
if [ "$pendientes" -eq 0 ]; then
  azul "  Todo listo. Escribe  panel  para abrirlo, o  python pipeline.py  para una tanda completa."
else
  azul "  Faltan $pendientes cosa(s) de arriba. El panel ya funciona: escribe  panel"
  echo  "  Las credenciales se pueden poner desde ahí, en Ajustes."
fi
echo
