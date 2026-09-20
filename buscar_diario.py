"""
Busca historias una vez al día, a una hora distinta cada día.

Por qué no basta una línea de cron: cron solo sabe de horas fijas. Una
búsqueda clavada a las 06:00 todos los días es un patrón, y un patrón es lo
que se ve desde fuera. Aquí cada día se sortea una franja dentro de una
ventana (por omisión, de 09:00 a 23:00 en saltos de media hora) y la búsqueda
sale a esa hora.

Lo que esto consigue y lo que no, para no confundirlo:

  - Sí: reparte las visitas y quita el sello de "esto lo lanza una máquina
    todos los días a la misma hora".
  - No: no es una forma de saltarse límites ni bloqueos. Se sigue leyendo el
    RSS público, con la misma pausa entre peticiones (RATE_LIMIT_SEG) y la
    misma identificación de siempre. Si Reddit devuelve 429, devuelve 429 a
    la hora que sea; el remedio es pedir menos, no esconderse mejor.

Cómo se engancha a cron: el crontab llama a este script cada media hora y casi
siempre no hace nada y se va. No se duerme esperando la franja a propósito —
un `sleep` de una hora en Android lo mata el sistema y la tanda no sale nunca.
El plan del día vive en pipeline_state/busqueda_diaria.json, así que si el
teléfono estaba apagado a la hora sorteada, la primera pasada al encenderlo
recupera la búsqueda del día en vez de perderla.

Uso a mano:

    python buscar_diario.py            lo que hace cron: mira si toca
    python buscar_diario.py --ver      enseña el plan de hoy y no busca
    python buscar_diario.py --ahora    busca ya, sin mirar franja ni red
"""
import os
import sys
import json
import random
import logging
import argparse
import subprocess
from datetime import datetime

import cola       # CARPETA_ESTADO y la cola de candidatos
import almacen    # escritura atómica: Android mata procesos a media escritura

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("buscar_diario")

RUTA_PLAN = os.path.join(cola.CARPETA_ESTADO, "busqueda_diaria.json")

# La ventana en la que puede caer la búsqueda. De madrugada no se busca: si
# algo sale mal, el error se ve a una hora en la que se puede mirar el móvil.
HORA_DESDE = 9
HORA_HASTA = 23          # exclusiva: la última franja posible es 22:30
PASO_MINUTOS = 30        # el mismo con el que cron llama a este script


def cargar_config():
    """La ventana se puede cambiar en config_trends.json, donde ya vive el
    resto de la configuración de la búsqueda."""
    cfg = {
        "busqueda_diaria_desde": HORA_DESDE,
        "busqueda_diaria_hasta": HORA_HASTA,
        # El dueño del proyecto paga los datos del móvil: la búsqueda entera
        # son decenas de peticiones seguidas, así que por omisión solo sale
        # con WiFi. Ponlo en false si prefieres que busque con datos.
        "busqueda_diaria_solo_wifi": True,
    }
    ruta = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config_trends.json")
    if os.path.exists(ruta):
        try:
            with open(ruta, "r", encoding="utf-8") as f:
                cfg.update(json.load(f))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"config_trends.json no se pudo leer ({e}); se usan los valores por omisión")
    return cfg


# ---------------------------------------------------------------- el plan --

def franjas(cfg):
    """Todas las horas posibles del día, como (hora, minuto)."""
    desde = int(cfg.get("busqueda_diaria_desde", HORA_DESDE))
    hasta = int(cfg.get("busqueda_diaria_hasta", HORA_HASTA))
    desde = max(0, min(23, desde))
    hasta = max(desde + 1, min(24, hasta))
    return [(h, m)
            for h in range(desde, hasta)
            for m in range(0, 60, PASO_MINUTOS)]


def elegir_franja(cfg, azar=None):
    return (azar or random).choice(franjas(cfg))


def plan_del_dia(hoy, cfg, azar=None, plan=None):
    """El plan de hoy: el que ya hubiera, o uno nuevo con franja sorteada."""
    if plan and plan.get("fecha") == hoy.isoformat():
        return plan, False
    hora, minuto = elegir_franja(cfg, azar)
    return {"fecha": hoy.isoformat(), "hora": hora, "minuto": minuto,
            "corrida_en": None, "agregados": None, "esperas_sin_wifi": 0}, True


def cargar_plan():
    """Un plan ilegible no bloquea nada: se sortea otro. Es el único archivo
    de pipeline_state/ del que se puede prescindir sin perder trabajo."""
    try:
        plan = almacen.cargar(RUTA_PLAN, None)
    except (json.JSONDecodeError, OSError):
        logger.warning("El plan del día estaba ilegible; se sortea otra franja.")
        return None
    return plan if isinstance(plan, dict) else None


def guardar_plan(plan):
    almacen.guardar(RUTA_PLAN, plan)


def toca_ya(plan, ahora):
    """Si la franja de hoy ya llegó (o se pasó, porque el móvil estaba
    apagado) y la búsqueda del día aún no salió."""
    if plan.get("corrida_en"):
        return False
    return (ahora.hour, ahora.minute) >= (plan["hora"], plan["minuto"])


# ----------------------------------------------------------------- la red --

def _wifi_por_termux():
    """termux-wifi-connectioninfo, si está Termux:API instalada."""
    try:
        salida = subprocess.run(["termux-wifi-connectioninfo"],
                                capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return None
    if salida.returncode != 0 or not salida.stdout.strip():
        return None
    try:
        info = json.loads(salida.stdout)
    except json.JSONDecodeError:
        return None
    if not isinstance(info, dict):
        return None
    estado = str(info.get("supplicant_state", "")).upper()
    ip = str(info.get("ip", ""))
    return estado == "COMPLETED" and ip not in ("", "0.0.0.0", "<unknown>")


def _wifi_por_ruta():
    """Sin Termux:API: por dónde sale el tráfico. wlan… es WiFi; rmnet, ccmni
    y compañía son la radio del operador."""
    try:
        salida = subprocess.run(["ip", "route", "get", "1.1.1.1"],
                                capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    if salida.returncode != 0:
        return None
    partes = salida.stdout.split()
    if "dev" not in partes:
        return None
    interfaz = partes[partes.index("dev") + 1] if partes.index("dev") + 1 < len(partes) else ""
    return interfaz.startswith(("wlan", "ap", "eth"))


def hay_wifi():
    """True, False, o None cuando no hay forma de saberlo desde aquí."""
    for sonda in (_wifi_por_termux, _wifi_por_ruta):
        respuesta = sonda()
        if respuesta is not None:
            return respuesta
    return None


# -------------------------------------------------------------- la tanda --

def buscar():
    """Las dos fuentes que llenan la misma cola. Que falle una no impide que
    la otra deje candidatos — es lo mismo que hace pipeline.py."""
    antes = len(cola.cargar_pendientes())

    import trend_scout
    try:
        trend_scout.main([])
    except Exception as e:                       # noqa: BLE001 — la otra fuente sigue
        logger.error(f"Reddit falló: {e}")

    import youtube_scout
    try:
        if youtube_scout.cargar_config().get("youtube_activo", True):
            youtube_scout.main([])
    except Exception as e:                       # noqa: BLE001
        logger.error(f"YouTube falló: {e}")

    return len(cola.cargar_pendientes()) - antes


def decidir(plan, ahora, cfg, wifi):
    """Qué toca hacer en esta pasada: correr, esperar o nada."""
    if plan.get("corrida_en"):
        return "ya_corrio"
    if not toca_ya(plan, ahora):
        return "todavia_no"
    if cfg.get("busqueda_diaria_solo_wifi", True) and wifi is False:
        return "sin_wifi"
    return "correr"


def main(argv=None):
    ap = argparse.ArgumentParser(description="Busca historias una vez al día, a hora sorteada.")
    ap.add_argument("--ver", action="store_true", help="Enseña el plan de hoy y no busca.")
    ap.add_argument("--ahora", action="store_true", help="Busca ya, sin mirar la franja ni la red.")
    args = ap.parse_args(argv or [])

    cfg = cargar_config()
    ahora = datetime.now()
    plan, es_nuevo = plan_del_dia(ahora.date(), cfg, plan=cargar_plan())
    if es_nuevo:
        guardar_plan(plan)
        logger.info(f"Plan de hoy: búsqueda a las {plan['hora']:02d}:{plan['minuto']:02d}")

    if args.ver:
        hecha = plan.get("corrida_en")
        print(f" Hoy {plan['fecha']} → búsqueda a las {plan['hora']:02d}:{plan['minuto']:02d}")
        print(f" Estado    : {'ya hecha (' + hecha + ')' if hecha else 'pendiente'}")
        if plan.get("agregados") is not None:
            print(f" Candidatos: {plan['agregados']} nuevo(s)")
        red = hay_wifi()
        dice = {True: "sí", False: "no", None: "no se puede saber desde aquí"}[red]
        print(f" WiFi      : {dice}")
        print(f" Cola      : {len(cola.cargar_pendientes())} candidato(s) esperando guion")
        return

    if args.ahora:
        qué = "correr"
        wifi = None
    else:
        wifi = hay_wifi() if cfg.get("busqueda_diaria_solo_wifi", True) else None
        qué = decidir(plan, ahora, cfg, wifi)

    if qué == "ya_corrio":
        return
    if qué == "todavia_no":
        return
    if qué == "sin_wifi":
        # No se marca como hecha: en cuanto vuelva el WiFi, la siguiente
        # pasada de cron la recupera. Si el día acaba sin WiFi, se pierde esa
        # búsqueda y mañana se sortea otra franja.
        plan["esperas_sin_wifi"] = int(plan.get("esperas_sin_wifi") or 0) + 1
        guardar_plan(plan)
        if plan["esperas_sin_wifi"] in (1, 6, 12):
            logger.info("Tocaba buscar, pero no hay WiFi. Se reintenta en la siguiente pasada. "
                        "(Para buscar también con datos: \"busqueda_diaria_solo_wifi\": false "
                        "en config_trends.json)")
        return

    logger.info("Toca la búsqueda del día.")
    agregados = buscar()
    plan["corrida_en"] = datetime.now().isoformat(timespec="seconds")
    plan["agregados"] = agregados
    guardar_plan(plan)
    logger.info(f"Búsqueda del día hecha: {agregados} candidato(s) nuevo(s); "
                f"{len(cola.cargar_pendientes())} en cola.")


if __name__ == "__main__":
    # argv explícito: main() sin argumentos es como lo llamaría otro módulo
    # (igual que trend_scout), así que los flags de la consola se pasan a mano.
    main(sys.argv[1:])
