#!/data/data/com.termux/files/usr/bin/bash
# Programa las tandas automáticas con cron.
#
#   bash instalar_cron.sh            instala/actualiza las tres tareas
#   bash instalar_cron.sh --quitar   las borra
#
# Es idempotente: solo toca las líneas marcadas con MARCA, así que cualquier
# otra tarea tuya en el crontab se queda como está.
#
# Existe porque las rutas escritas a mano en el móvil se equivocan, y cuando
# se equivocan no avisan: cron ejecuta la línea, el `cd` falla, el `&&` corta
# la cadena, y no queda ni log ni error. Parece que el cron "no se ejecuta"
# cuando en realidad corre a su hora y no llega a nada.

set -e
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="$(command -v python)"
MARCA="# video-scout-pipeline"

echo
echo "  Proyecto: $REPO"
echo "  Python:   $PYTHON"
echo

# ---- 1. cronie ------------------------------------------------------------
if ! command -v crontab >/dev/null 2>&1; then
  echo "  Instalando cronie y termux-services..."
  pkg install -y cronie termux-services
  echo "  ⚠️ Cierra Termux del todo y vuelve a abrirlo, luego repite esta orden."
  echo "     (termux-services solo se engancha al arrancar la sesión)"
  exit 0
fi

# ---- 2. las tareas --------------------------------------------------------
# Se quitan primero las anteriores nuestras, para no duplicarlas al reinstalar.
ACTUAL="$(crontab -l 2>/dev/null | grep -v "$MARCA" || true)"

if [ "$1" = "--quitar" ]; then
  printf '%s\n' "$ACTUAL" | crontab -
  echo "  ✓ Tareas quitadas. Lo demás del crontab sigue igual."
  exit 0
fi

NUEVAS="$(cat <<EOF
0 6 * * 1,4 cd $REPO && $PYTHON pipeline.py --hasta video >> $REPO/pipeline.log 2>&1 $MARCA
0 9 * * * cd $REPO && $PYTHON pipeline.py --desde publicar >> $REPO/pipeline.log 2>&1 $MARCA
0 8 1 * * cd $REPO && $PYTHON actualizar_musica.py >> $REPO/musica.log 2>&1 $MARCA
EOF
)"

printf '%s\n%s\n' "$ACTUAL" "$NUEVAS" | grep -v '^$' | crontab -
echo "  ✓ Programado:"
echo "      lunes y jueves 06:00  → buscar historias, guiones y video"
echo "      todos los días 09:00  → publicar lo que haya en la cola"
echo "      día 1 de cada mes     → refrescar la música"
echo

# ---- 3. crond vivo --------------------------------------------------------
if pgrep -f crond >/dev/null 2>&1; then
  echo "  ✓ crond está corriendo"
else
  echo "  Arrancando crond..."
  sv-enable crond 2>/dev/null || echo "  ⚠️ No pude arrancarlo: prueba  sv-enable crond"
fi

# ---- 4. lo que cron no puede arreglar solo --------------------------------
# Estas dos son de Android, no del proyecto, y son la causa habitual de que
# un cron bien puesto deje de dispararse a los pocos días.
echo
if [ ! -d "$HOME/.termux/boot" ]; then
  echo "  ⚠️ Falta Termux:Boot. Sin él, cron muere en cada reinicio del teléfono."
  echo "     Instala la app desde F-Droid y ábrela una vez:"
  echo "     https://f-droid.org/packages/com.termux.boot/"
fi
echo "  ⚠️ Quita a Termux la optimización de batería, o Android lo matará"
echo "     al cabo de unas horas y las tandas no saldrán."
echo "     Ajustes → Apps → Termux → Batería → Sin restricciones."
echo
echo "  Para comprobar que corre de verdad:  tail -f $REPO/pipeline.log"
echo
