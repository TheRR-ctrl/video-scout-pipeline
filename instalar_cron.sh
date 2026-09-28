#!/data/data/com.termux/files/usr/bin/bash
# Programa las tandas automáticas con cron.
#
#   bash instalar_cron.sh            instala/actualiza las tareas
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
# La primera línea se dispara cada media hora y casi siempre no hace nada: es
# buscar_diario.py mirando si ya llegó la franja que sorteó para hoy. Así la
# búsqueda sale a una hora distinta cada día sin que cron sepa de sorteos, y
# sin dejar un `sleep` de horas colgando que Android acabaría matando.
# Se quitan primero las anteriores nuestras, para no duplicarlas al reinstalar.
# Si `crontab -l` falla por otra cosa que "no hay crontab", se para: tomarlo
# por vacío y escribir encima borraría las demás tareas que tengas.
if ! LEIDO="$(crontab -l 2>&1)"; then
  case "$LEIDO" in
    *"no crontab for"*) LEIDO="" ;;
    *) echo "  ✗ No pude leer el crontab, así que no lo toco:"
       echo "    $LEIDO"
       exit 1 ;;
  esac
fi
ACTUAL="$(printf '%s\n' "$LEIDO" | grep -v "$MARCA" || true)"

if [ "$1" = "--quitar" ]; then
  printf '%s\n' "$ACTUAL" | crontab -
  echo "  ✓ Tareas quitadas. Lo demás del crontab sigue igual."
  exit 0
fi

# Las tareas y sus horas las escribe horario.py, con lo que hayas elegido en
# el panel (Ajustes → Horario automático) o, si no has tocado nada, con el
# horario de siempre. Solo toca las líneas marcadas con MARCA.
# Si falla, se sigue igual: arrancar crond y el arranque tras reiniciar hacen
# falta de todas formas, y el panel puede volver a aplicar el horario luego.
echo "  Programado:"
"$PYTHON" "$REPO/horario.py" --aplicar || echo "  ⚠️ No se pudo escribir el crontab; repite esta orden."
echo "     (cámbialo cuando quieras en el panel: Ajustes → Horario automático)"
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

# Termux:Boot no arranca nada por su cuenta: al encender el telefono ejecuta
# lo que haya en ~/.termux/boot/ y nada mas. Sin este script, la app esta
# instalada y el cron sigue sin volver despues de un reinicio.
ARRANQUE="$HOME/.termux/boot/00-crond"
mkdir -p "$HOME/.termux/boot"
cat > "$ARRANQUE" <<'EOF'
#!/data/data/com.termux/files/usr/bin/sh
# Lo pone instalar_cron.sh. Se ejecuta al encender el teléfono.

# Sin el wake-lock, Android duerme el proceso y cron se salta las horas.
termux-wake-lock 2>/dev/null

# El guardia evita dos crond a la vez (uno de termux-services y otro de aquí),
# que dispararía cada tarea por duplicado.
pgrep -x crond >/dev/null 2>&1 || crond
EOF
chmod +x "$ARRANQUE"
echo "  ✓ Arranque tras reiniciar: $ARRANQUE"

# Si la app esta instalada no hay forma fiable de saberlo desde aqui: Termux no
# puede leer /data/data de otro paquete, y `pm list packages` no siempre esta
# disponible. Preguntarlo mal daba un "falta Termux:Boot" a quien ya la tenia,
# asi que se dice como recordatorio y decide quien lo lee.
echo "     Para que se ejecute hace falta la app Termux:Boot, instalada y"
echo "     abierta una vez: https://f-droid.org/packages/com.termux.boot/"
echo "  ⚠️ Quita a Termux la optimización de batería, o Android lo matará"
echo "     al cabo de unas horas y las tandas no saldrán."
echo "     Ajustes → Apps → Termux → Batería → Sin restricciones."
echo
echo "  Para comprobar que corre de verdad:  tail -f $REPO/pipeline.log"
echo
