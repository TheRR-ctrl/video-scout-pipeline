#!/data/data/com.termux/files/usr/bin/bash
# Vuelve a poner todo tal cual estaba, desde el archivo de respaldo.py.
#
# En el teléfono recién reiniciado, con Termux instalado desde F-Droid y el
# archivo video-scout-respaldo-FECHA.tar ya bajado a Descargas:
#
#   pkg install -y git && \
#   git clone https://github.com/TheRR-ctrl/video-scout-pipeline && \
#   bash video-scout-pipeline/restaurar.sh
#
# Busca solo el respaldo más reciente en Descargas (o pásale la ruta:
# bash restaurar.sh /ruta/al/archivo.tar). Luego instala todo con
# instalar.sh y, si tenías horario automático, vuelve a ponerlo.
#
# No pisa nada del código: el respaldo solo lleva lo que git no sigue.

set -e
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO"

azul()  { printf '\033[36m%s\033[0m\n' "$1"; }
verde() { printf '\033[32m  ✓ %s\033[0m\n' "$1"; }
aviso() { printf '\033[33m  ○ %s\033[0m\n' "$1"; }

echo
azul "  Restaurando en:  $REPO"
echo

# ---- 1. Permiso para leer Descargas ---------------------------------------
if [ ! -d "$HOME/storage" ]; then
  echo "  Android va a pedirte permiso de almacenamiento — dale a Permitir."
  termux-setup-storage || true
  for _ in 1 2 3 4 5 6 7 8 9 10; do [ -d "$HOME/storage/downloads" ] && break; sleep 1; done
fi

# ---- 2. El respaldo ------------------------------------------------------
ARCHIVO="$1"
if [ -z "$ARCHIVO" ]; then
  ARCHIVO="$(ls -t "$HOME"/storage/downloads/video-scout-respaldo-*.tar \
                   /sdcard/Download/video-scout-respaldo-*.tar 2>/dev/null | head -n 1 || true)"
fi
if [ -z "$ARCHIVO" ] || [ ! -f "$ARCHIVO" ]; then
  aviso "No encontré ningún video-scout-respaldo-*.tar en Descargas."
  echo "     Bájalo de donde lo guardaste (Google Drive, el PC…) a la carpeta"
  echo "     Descargas del teléfono y vuelve a correr:  bash $REPO/restaurar.sh"
  exit 1
fi
verde "respaldo: $(basename "$ARCHIVO")"

TMP="$REPO/.restaurando"
rm -rf "$TMP"; mkdir -p "$TMP"
tar -xf "$ARCHIVO" -C "$TMP"

# ---- 3. Archivos del proyecto --------------------------------------------
if [ -d "$TMP/proyecto" ]; then
  cp -a "$TMP/proyecto/." "$REPO/"
  verde "$(find "$TMP/proyecto" -type f | wc -l) archivo(s) del proyecto (claves, estado, guiones, material)"
fi
for f in secretos.env youtube_token.json tiktok_token.json client_secret.json; do
  if [ -f "$REPO/$f" ]; then chmod 600 "$REPO/$f"; fi
done

# ---- 4. Videos y resultado_lote.json, a la carpeta de salida -------------
SALIDA="$(sed -n 's/.*"carpeta_salida": *"\([^"]*\)".*/\1/p' "$TMP/respaldo.json" 2>/dev/null || true)"
if [ -z "$SALIDA" ]; then SALIDA="/sdcard/DCIM/Videos creados"; fi
if [ -d "$TMP/salida" ] && [ -n "$SALIDA" ]; then
  if mkdir -p "$SALIDA" 2>/dev/null && cp -a "$TMP/salida/." "$SALIDA/"; then
    n=$(find "$TMP/salida" -name '*.mp4' | wc -l)
    verde "carpeta de salida: $SALIDA ($n video(s))"
  else
    aviso "No pude escribir en $SALIDA (¿falta el permiso de almacenamiento?)."
    echo "     Los videos siguen en $TMP/salida: cópialos a mano."
    SALIDA_FALLO=1
  fi
fi
if [ -z "$SALIDA_FALLO" ]; then rm -rf "$TMP"; fi

# ---- 5. Instalar todo -----------------------------------------------------
echo
azul "  Instalando (tarda unos minutos)…"
bash "$REPO/instalar.sh"

# ---- 6. El horario automático, si lo tenías -------------------------------
if [ -f "$REPO/pipeline_state/horario.json" ]; then
  echo
  azul "  Volviendo a poner el horario automático"
  bash "$REPO/instalar_cron.sh" || aviso "No se pudo: repítelo con  bash instalar_cron.sh"
fi

echo
azul "  Listo. Falta lo que no está en el respaldo porque es de Android:"
echo "   • Termux:API (F-Droid): batería, notificaciones y que el panel no se congele."
echo "   • Termux:Boot (F-Droid), si usabas el horario: que arranque tras reiniciar."
echo "   • Quitar a Termux la optimización de batería (Ajustes de Android → Apps → Termux)."
echo "   • La app del panel (APK), si la usabas: se vuelve a instalar igual que la primera vez."
echo
echo "  Para abrir el panel:  panel"
echo
