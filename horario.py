"""
El horario de las tandas automáticas, editable desde el panel.

Antes las cinco tareas estaban escritas a mano en instalar_cron.sh, y cambiar
una hora era editar el script en el teléfono. Ahora viven aquí, con días y
hora en pipeline_state/horario.json, y esto es lo que escribe el crontab:

    python horario.py           # qué está programado y cuándo
    python horario.py --aplicar # escribe el crontab con lo guardado

Desde el panel solo se cambian días, hora y si está activa. Los comandos
son fijos, en TAREAS: así, desde una página web no se puede meter en el
crontab ninguna orden que no esté ya aquí.

Solo se tocan las líneas marcadas con MARCA; lo demás que tengas en el
crontab se queda como está (igual que hacía instalar_cron.sh).
"""
import os
import re
import sys
import shutil
import argparse
import subprocess

import almacen

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RUTA_HORARIO = os.path.join(BASE_DIR, "pipeline_state", "horario.json")
MARCA = "# video-scout-pipeline"
DIAS = ["domingo", "lunes", "martes", "miércoles", "jueves", "viernes", "sábado"]

# Lo de fábrica es lo que instalar_cron.sh programaba antes, tal cual.
# "semana": días 0-6 (0 = domingo, como cron). "mes": días del mes.
# "franja": cada media hora en esos días; la hora exacta la sortea
# buscar_diario.py, así que aquí no hay hora que elegir.
TAREAS = {
    "buscar": {
        "nombre": "Buscar historias nuevas",
        "que": "A una hora sorteada dentro de la franja del día, para no parecer un robot.",
        "tipo": "franja", "dias": [0, 1, 2, 3, 4, 5, 6],
        "orden": "{py} buscar_diario.py", "log": "buscar.log",
    },
    "guiones_video": {
        "nombre": "Escribir guiones y grabar",
        "que": "Convierte en video lo que haya en la cola.",
        "tipo": "semana", "dias": [1, 4], "hora": "06:00",
        "orden": "{py} pipeline.py --desde guion --hasta video", "log": "pipeline.log",
    },
    "publicar": {
        "nombre": "Publicar",
        "que": "Sube a YouTube lo que ya esté revisado.",
        "tipo": "semana", "dias": [0, 1, 2, 3, 4, 5, 6], "hora": "09:00",
        "orden": "{py} pipeline.py --desde publicar", "log": "pipeline.log",
    },
    "musica": {
        "nombre": "Refrescar la música",
        "que": "Baja pistas nuevas de Jamendo.",
        "tipo": "mes", "dias_mes": [1], "hora": "08:00",
        "orden": "{py} actualizar_musica.py", "log": "musica.log",
    },
    "revision": {
        "nombre": "Revisión del canal",
        "que": "Borra copias repetidas y rehace lo que no tuvo vistas.",
        "tipo": "mes", "dias_mes": [1, 15], "hora": "07:30",
        "orden": "bash revision_quincenal.sh", "log": "revision.log",
    },
}

_HORA = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def cargar():
    """Las tareas con lo guardado encima de lo de fábrica.

    Lo guardado pasa otra vez por validar: si alguien edita horario.json a
    mano y lo deja mal, esa tarea vuelve a lo de fábrica en vez de escribir
    en el crontab una línea rota (que cron ignora sin decir nada).
    """
    guardado = almacen.leer(RUTA_HORARIO, {}) or {}
    tareas = {}
    for id_, base in TAREAS.items():
        t = {k: v for k, v in base.items() if k not in ("orden", "log")}
        t["activa"] = True
        try:
            t.update(validar({id_: guardado.get(id_) or {}})[id_])
        except (ValueError, TypeError, AttributeError):
            pass
        tareas[id_] = t
    return tareas


def validar(cambios):
    """Lo que manda el panel, limpio, o ValueError con qué está mal."""
    limpio = {}
    for id_, c in (cambios or {}).items():
        if id_ not in TAREAS:
            raise ValueError(f"Tarea desconocida: {id_}")
        if not isinstance(c, dict):
            raise ValueError(f"Tarea mal formada: {id_}")
        base = TAREAS[id_]
        t = {"activa": bool(c.get("activa", True))}
        if base["tipo"] in ("semana", "franja"):
            dias = sorted({int(d) for d in c.get("dias", base["dias"])})
            if not dias or any(d < 0 or d > 6 for d in dias):
                raise ValueError(f"{base['nombre']}: elige al menos un día.")
            t["dias"] = dias
        if base["tipo"] == "mes":
            # Hasta el 28: un 30 o un 31 se saltaría en silencio los meses
            # que no lo tienen, y febrero se quedaría sin música o revisión.
            dias = sorted({int(d) for d in c.get("dias_mes", base["dias_mes"])})
            if not dias:
                raise ValueError(f"{base['nombre']}: pon al menos un día del mes.")
            if any(d < 1 or d > 28 for d in dias):
                raise ValueError(f"{base['nombre']}: los días del mes van del 1 al 28, "
                                 "para que toque todos los meses.")
            t["dias_mes"] = dias
        if base["tipo"] != "franja":
            hora = str(c.get("hora", base["hora"]))
            if not _HORA.match(hora):
                raise ValueError(f"{base['nombre']}: la hora tiene que ser HH:MM.")
            t["hora"] = hora
        limpio[id_] = t
    return limpio


def guardar(cambios):
    limpio = validar(cambios)
    actual = almacen.leer(RUTA_HORARIO, {}) or {}
    actual.update(limpio)
    almacen.guardar(RUTA_HORARIO, actual)
    return cargar()


def _lista(nums):
    return ",".join(str(n) for n in nums)


def _semana(dias):
    # Los siete días se escriben "*", como estaba antes en instalar_cron.sh:
    # así un crontab puesto con la versión anterior cuenta como al día.
    return "*" if len(set(dias)) == 7 else _lista(dias)


def _python():
    """El `python` de la misma carpeta que el que corre esto, sin resolver.

    No sys.executable a secas: según se arranque (python, python3) cambia el
    nombre, y el panel diría que el crontab no está al día sin que nada haya
    cambiado. Y nunca la ruta resuelta (python3.12): Termux sube de versión
    con cualquier `pkg upgrade`, ese archivo desaparece, y cron seguiría
    llamándolo sin avisar a nadie.
    """
    carpeta = os.path.dirname(sys.executable)
    for nombre in ("python", "python3"):
        ruta = os.path.join(carpeta, nombre)
        if os.path.exists(ruta):
            return ruta
    return sys.executable


def lineas_cron(tareas=None, py=None):
    """Las líneas de crontab de las tareas activas. Ninguna si este aparato
    está desactivado (dispositivo.py): así aplicar() las quita del crontab
    sin tocar lo elegido en el panel, y al reactivar vuelven tal cual."""
    import dispositivo
    if dispositivo.desactivado():
        return []
    tareas = tareas or cargar()
    py = py or _python()
    out = []
    for id_, t in tareas.items():
        if not t["activa"]:
            continue
        base = TAREAS[id_]
        if t["tipo"] == "franja":
            cuando = f"*/30 * * * {_semana(t['dias'])}"
        else:
            h, m = (int(x) for x in t["hora"].split(":"))
            if t["tipo"] == "semana":
                cuando = f"{m} {h} * * {_semana(t['dias'])}"
            else:
                cuando = f"{m} {h} {_lista(t['dias_mes'])} * *"
        orden = base["orden"].format(py=py)
        out.append(f"{cuando} cd {BASE_DIR} && {orden} >> {BASE_DIR}/{base['log']} 2>&1 {MARCA}")
    return out


def _y(cosas):
    cosas = [str(c) for c in cosas]
    return cosas[0] if len(cosas) == 1 else ", ".join(cosas[:-1]) + " y " + cosas[-1]


def describir(t):
    """Cuándo corre, dicho como lo diría una persona."""
    if not t["activa"]:
        return "en pausa"
    if t["tipo"] == "mes":
        dias = _y(t["dias_mes"])
        return f"día{'s' if len(t['dias_mes']) > 1 else ''} {dias} de cada mes, a las {t['hora']}"
    if len(t["dias"]) == 7:
        dias = "todos los días"
    else:
        # Lunes primero, que es como se piensa la semana.
        dias = _y(DIAS[d] for d in sorted(t["dias"], key=lambda d: (d + 6) % 7))
    if t["tipo"] == "franja":
        return f"{dias}, a una hora sorteada"
    return f"{dias}, a las {t['hora']}"


class CrontabIlegible(Exception):
    """`crontab -l` falló por algo que no es "todavía no hay crontab"."""


def _crontab_actual():
    """El crontab tal cual, "" si todavía no hay, None si falta cron.

    Un fallo de `crontab -l` solo cuenta como vacío cuando dice que no hay
    crontab. Con cualquier otro (permisos, archivo roto, timeout) se para:
    tomarlo por vacío haría que aplicar() escribiese solo nuestras líneas y
    borrase sin avisar las demás tareas que tengas, y eso ahora pasa con
    un toque en el panel, no a mano en Termux.
    """
    if not shutil.which("crontab"):
        return None
    try:
        r = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CrontabIlegible(f"no pude leer el crontab ({exc})") from exc
    if r.returncode == 0:
        return r.stdout
    error = (r.stderr or "").strip()
    if "no crontab for" in error.lower():
        return ""
    raise CrontabIlegible(f"no pude leer el crontab: {error or 'código ' + str(r.returncode)}")


def aplicar():
    """Escribe en el crontab las tareas activas. Devuelve (ok, mensaje)."""
    try:
        actual = _crontab_actual()
    except CrontabIlegible as exc:
        return False, f"No toqué nada: {exc}. Mira `crontab -l` en Termux."
    if actual is None:
        return False, ("Falta cron en el teléfono. Instálalo una vez en Termux con: "
                       "bash instalar_cron.sh")
    ajenas = [l for l in actual.splitlines() if l.strip() and MARCA not in l]
    nuevo = "\n".join(ajenas + lineas_cron()) + "\n"
    r = subprocess.run(["crontab", "-"], input=nuevo, capture_output=True, text=True, timeout=10)
    if r.returncode != 0:
        return False, f"crontab no aceptó el horario: {(r.stderr or '').strip()}"
    return True, "Horario aplicado."


def estado():
    """Para el panel: las tareas, si cron está y si lo instalado coincide."""
    ilegible = None
    try:
        actual = _crontab_actual()
    except CrontabIlegible as exc:
        actual, ilegible = "", str(exc)
    instaladas = [l for l in (actual or "").splitlines() if MARCA in l]
    try:
        vivo = subprocess.run(["pgrep", "-x", "crond"], capture_output=True, timeout=5).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        vivo = None
    tareas = cargar()
    return {
        "tareas": [{"id": i, **t, "cuando": describir(t)} for i, t in tareas.items()],
        "hay_cron": actual is not None,
        "crond_vivo": vivo,
        "al_dia": ilegible is None and sorted(instaladas) == sorted(lineas_cron(tareas)),
        "ilegible": ilegible,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description="Horario de las tandas automáticas.")
    ap.add_argument("--aplicar", action="store_true", help="Escribir el crontab.")
    args = ap.parse_args(argv if argv is not None else sys.argv[1:])
    if args.aplicar:
        ok, msg = aplicar()
        print(("  ✓ " if ok else "  ✗ ") + msg)
        if not ok:
            return 1
    for t in estado()["tareas"]:
        print(f"  {t['nombre']:<28} {t['cuando']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
