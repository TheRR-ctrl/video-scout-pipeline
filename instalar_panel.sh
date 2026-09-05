#!/data/data/com.termux/files/usr/bin/bash
# Deja el panel a un comando de distancia, y opcionalmente a un toque desde
# la pantalla de inicio.
#
#   bash instalar_panel.sh
#
# Después basta con escribir  panel  en Termux. Es idempotente: correrlo dos
# veces no duplica nada.

set -e
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ATAJOS="$HOME/.shortcuts"

echo
echo "  Panel: $REPO"
echo

# ---- 1. Flask -------------------------------------------------------------
if ! python -c "import flask" 2>/dev/null; then
  echo "  Instalando Flask..."
  pip install flask
else
  echo "  ✓ Flask ya está"
fi

# ---- 2. termux-api --------------------------------------------------------
# Sin esto Android congela el servidor al cambiarte a Chrome, que es el fallo
# más molesto de este panel.
if ! command -v termux-wake-lock >/dev/null 2>&1; then
  echo "  Instalando termux-api (evita que Android duerma el servidor)..."
  # Sin || true, un fallo aquí abortaría el instalador por el set -e y te
  # quedarías sin el comando 'panel'. El wake lock es deseable, no
  # imprescindible: el panel funciona igual, solo hay que no dejar Termux
  # en segundo plano mucho rato.
  pkg install -y termux-api || echo "  ⚠️  No se pudo instalar termux-api; el panel funciona igual."
else
  echo "  ✓ termux-api ya está"
fi

# ---- 3. el comando 'panel' ------------------------------------------------
# Como ejecutable en el PATH, no como función en .bashrc: así funciona en la
# misma terminal donde corres esto, sin abrir una nueva ni recargar nada, y
# no depende de que .bashrc se lea.
if [ -n "$PREFIX" ] && [ -d "$PREFIX/bin" ]; then
  DESTINO="$PREFIX/bin/panel"
else
  mkdir -p "$HOME/.local/bin"
  DESTINO="$HOME/.local/bin/panel"
fi

cat > "$DESTINO" <<EOF
#!/usr/bin/env bash
cd "$REPO" || exit 1
exec python servidor.py --abrir "\$@"
EOF
chmod +x "$DESTINO"
echo "  ✓ Comando 'panel' listo  ($DESTINO)"

case ":$PATH:" in
  *":$(dirname "$DESTINO"):"*) ;;
  *) echo "  ⚠️  $(dirname "$DESTINO") no está en tu PATH."
     echo "      Agrega esto a ~/.bashrc:  export PATH=\"$(dirname "$DESTINO"):\$PATH\"" ;;
esac

# ---- 3b. arranque automático (opcional) -----------------------------------
# Útil si Termux:Widget no funciona en tu teléfono: al abrir Termux el panel
# ya queda corriendo, y solo hay que pasarse a Chrome.
INI="# >>> autoarranque panel >>>"
FIN="# <<< autoarranque panel <<<"
BASHRC="$HOME/.bashrc"

if [ -f "$BASHRC" ] && grep -qF "$INI" "$BASHRC"; then
  sed -i "/$INI/,/$FIN/d" "$BASHRC"
fi

if [ "$1" = "--autoarranque" ]; then
  cat >> "$BASHRC" <<EOF
$INI
# Arranca el panel al abrir Termux, salvo que ya esté escuchando.
# Se comprueba el PUERTO y no el nombre del proceso: "pgrep -f servidor.py"
# coincide también con el propio shell que lo busca, y daría falsos
# positivos que impedirían arrancarlo.
if ! (exec 3<>/dev/tcp/127.0.0.1/8770) 2>/dev/null; then
  # setsid y redirecciones completas: sin esto el proceso queda atado a
  # la terminal y abrir Termux se quedaría esperándolo.
  (cd "$REPO" && setsid nohup python servidor.py </dev/null >/dev/null 2>&1 &)
  echo "  Panel arrancando en http://127.0.0.1:8770"
else
  echo "  Panel ya corriendo en http://127.0.0.1:8770"
fi
$FIN
EOF
  echo "  ✓ Arranque automático activado (se apaga solo al cerrar el panel)"
else
  echo "  · Arranque automático: desactivado"
  echo "    (actívalo con: bash instalar_panel.sh --autoarranque)"
fi

# ---- 4. atajo en la pantalla de inicio (Termux:Widget) --------------------
mkdir -p "$ATAJOS"
cat > "$ATAJOS/🎬 Panel de videos" <<EOF
#!/usr/bin/env bash
cd "$REPO"
python servidor.py --abrir
EOF
chmod +x "$ATAJOS/🎬 Panel de videos"
echo "  ✓ Atajo creado en ~/.shortcuts"

# ---- 5. puerta de entrada portátil ---------------------------------------
# Un HTML suelto que detecta el panel y entra solo. Se copia a Descargas
# porque desde ahí se abre con dos toques y se puede arrastrar a la pantalla
# de inicio; el original se queda en el repo de todas formas.
if [ -d "$HOME/storage/downloads" ]; then
  cp "$REPO/abrir_panel.html" "$HOME/storage/downloads/abrir_panel.html" 2>/dev/null \
    && echo "  ✓ abrir_panel.html copiado a Descargas" \
    || echo "  · No se pudo copiar abrir_panel.html a Descargas"
else
  echo "  · Descargas no accesible (corre 'termux-setup-storage' para copiar ahí"
  echo "    abrir_panel.html). Está en: $REPO/abrir_panel.html"
fi

echo
echo "  Listo. Para abrir el panel:"
echo
echo "      panel"
echo
echo "  (abre Chrome solo; se apaga cuando cierras la pestaña)"
echo
echo "  Para tenerlo en la pantalla de inicio, tres formas que se complementan:"
echo
echo "   1. abrir_panel.html (en Descargas): ábrelo desde donde quieras."
echo "      Si el panel está vivo entra solo; si no, te da el comando."
echo
echo "   2. Termux:Widget (F-Droid): un toque arranca el servidor y abre"
echo "      Chrome. El atajo '🎬 Panel de videos' ya quedó creado."
echo
echo "   3. Con el panel abierto: menú ⋮ > Agregar a pantalla de inicio."
echo "      Queda con icono propio y sin barra de navegador."
echo
echo "  Funciona ya, en esta misma terminal."
echo
