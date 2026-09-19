#!/data/data/com.termux/files/usr/bin/bash
# Revisión quincenal: mira qué funcionó en el canal y rehace lo que no.
#
#   bash revision_quincenal.sh           lo hace
#   bash revision_quincenal.sh --ver     solo lista, no borra nada
#
# Lo dispara el cron los días 1 y 15 (ver instalar_cron.sh) y escribe en
# revision.log. Existe como script y no como línea de cron para que lo que
# corre solo sea exactamente lo que puedes correr tú a mano, y para poder
# fechar cada pasada en el log: sin la fecha, dos revisiones seguidas en el
# mismo archivo no se distinguen.

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="$(command -v python)"
cd "$REPO" || exit 1

if [ "$1" = "--ver" ]; then
  HACERLO=""
else
  HACERLO="--si"
fi

echo
echo "=============================================================="
echo "  Revisión del canal — $(date '+%Y-%m-%d %H:%M')"
echo "=============================================================="

# Primero las repetidas: se borra el refrito y no se rehace, que la historia
# ya está contada en la copia que sí funcionó. Si se hiciera al revés, la
# historia repetida entraría por --sin-vistas y volvería a grabarse.
echo
echo "-- Copias repetidas ------------------------------------------"
"$PYTHON" relanzar.py --duplicados $HACERLO

# Después los que nadie vio. Las guardas van por omisión: no toca nada de
# menos de 14 días ni rehace una historia que ya se intentó dos veces.
echo
echo "-- Los que no vio nadie --------------------------------------"
"$PYTHON" relanzar.py --sin-vistas $HACERLO

# No se llama al render aquí: el cron de lunes y jueves ya corre
# pipeline.py --hasta video, y encuentra en la cola lo que esto acabe de
# devolver. Grabar dos tandas el mismo día llenaría el teléfono.
echo
echo "  Las historias que volvieron a la cola las graba el cron de"
echo "  lunes/jueves. Para no esperar:  python generar_video_maestro.py"
echo
