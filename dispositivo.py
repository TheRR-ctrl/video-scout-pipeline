"""
Desactivar este aparato (teléfono o PC) para que no trabaje a la vez que el
otro.

El pipeline puede correr en el teléfono y en Windows, pero cada uno lleva
su propio registro de lo subido: si los dos publican, el mismo video puede
acabar dos veces en el canal. Al pasarse de uno a otro, el que se deja se
desactiva desde el panel (botón ☢️ → «Desactivar») y, hasta que se
reactive:

  - el panel no arranca ningún trabajo, ni con botones ni solo (cadena,
    espera del cargador);
  - en Android, horario.py quita del crontab las tareas automáticas;
  - y por si el cron no se pudo tocar, los scripts que corre el cron
    (pipeline.py, buscar_diario.py, revision_quincenal.py) y los que suben
    (publisher.py, tiktok_publisher.py) se niegan a arrancar.

La marca es desactivado.json en la carpeta del proyecto, fuera de
pipeline_state/ a propósito: respaldo.py no la guarda. Si fuera dentro,
exportar los ajustes del teléfono ya desactivado y traerlos al PC dejaría
desactivado también el PC.
"""
import os
import sys
from datetime import datetime

import almacen

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RUTA = os.path.join(BASE_DIR, "desactivado.json")


def desactivado():
    """{"desde": ISO, "donde": str} si está desactivado, o None."""
    datos = almacen.leer(RUTA, None)
    if datos is None and os.path.exists(RUTA):
        # Existe pero no se lee (cortado a media escritura): mejor pasarse
        # de prudente que publicar desde los dos a la vez.
        return {"desde": None, "donde": ""}
    return datos if isinstance(datos, dict) else None


def desactivar(donde=""):
    almacen.guardar(RUTA, {"desde": datetime.now().isoformat(timespec="seconds"),
                           "donde": donde})


def reactivar():
    try:
        os.remove(RUTA)
    except FileNotFoundError:
        pass


def salir_si_desactivado():
    """Para los scripts que corre el cron o que suben: si este aparato está
    desactivado, lo dice y termina sin hacer nada (código 0, para que el
    cron no lo cuente como fallo)."""
    d = desactivado()
    if d is None:
        return
    desde = (d.get("desde") or "").replace("T", " ")[:16]
    print(f"⏸ Este aparato está desactivado{' desde ' + desde if desde else ''}: no se hace nada.")
    print("  Se reactiva en el panel (el aviso de arriba → «Reactivar»).")
    sys.exit(0)
