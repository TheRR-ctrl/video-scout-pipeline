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
BASHRC="$HOME/.bashrc"
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
  pkg install -y termux-api
else
  echo "  ✓ termux-api ya está"
fi

# ---- 3. el comando 'panel' ------------------------------------------------
# Se marca con un delimitador para poder reemplazar el bloque en vez de
# apilar copias cada vez que se corre esto.
INI="# >>> panel video-scout >>>"
FIN="# <<< panel video-scout <<<"

if [ -f "$BASHRC" ] && grep -qF "$INI" "$BASHRC"; then
  sed -i "/$INI/,/$FIN/d" "$BASHRC"
fi

cat >> "$BASHRC" <<EOF
$INI
panel() { cd "$REPO" && python servidor.py --abrir "\$@"; }
$FIN
EOF
echo "  ✓ Comando 'panel' listo"

# ---- 4. atajo en la pantalla de inicio (Termux:Widget) --------------------
mkdir -p "$ATAJOS"
cat > "$ATAJOS/🎬 Panel de videos" <<EOF
#!/data/data/com.termux/files/usr/bin/bash
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
echo "  Para tenerlo en la pantalla de inicio: instala Termux:Widget desde"
echo "  F-Droid y agrega su widget; ahí aparecerá '🎬 Panel de videos'."
echo
echo "  Abre una terminal nueva —o corre  source ~/.bashrc—  para que el"
echo "  comando 'panel' quede disponible."
echo
