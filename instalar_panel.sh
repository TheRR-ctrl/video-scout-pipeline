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

# ---- 4. atajo en la pantalla de inicio (Termux:Widget) --------------------
mkdir -p "$ATAJOS"
cat > "$ATAJOS/🎬 Panel de videos" <<EOF
#!/usr/bin/env bash
cd "$REPO"
python servidor.py --abrir
EOF
chmod +x "$ATAJOS/🎬 Panel de videos"
echo "  ✓ Atajo creado en ~/.shortcuts"

echo
echo "  Listo. Para abrir el panel:"
echo
echo "      panel"
echo
echo "  (abre Chrome solo; se apaga cuando cierras la pestaña)"
echo
echo "  Para tenerlo en la pantalla de inicio, dos formas que se complementan:"
echo
echo "   1. Termux:Widget (F-Droid): un toque arranca el servidor y abre"
echo "      Chrome. El atajo '🎬 Panel de videos' ya quedó creado."
echo
echo "   2. Con el panel abierto: menú ⋮ > Agregar a pantalla de inicio."
echo "      Queda con icono propio y sin barra de navegador."
echo
echo "  Funciona ya, en esta misma terminal."
echo
