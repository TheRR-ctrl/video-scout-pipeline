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

# Todo lo hace revision_quincenal.py, que corre también en Windows. Este
# archivo se queda porque el cron del teléfono lo llama por este nombre
# (horario.TAREAS): cambiar la línea obligaría a reaplicar el horario.
exec "$PYTHON" revision_quincenal.py "$@"
