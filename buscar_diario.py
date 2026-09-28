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

Cómo se sabe si hay WiFi, que en Android es más difícil de lo que parece: en
un Termux instalado desde Google Play, Termux:API no existe (la propia orden
contesta que no está disponible ahí), y Android 11 cerró el netlink que usa
`ip route` y la lectura de /sys/class/net. Las tres están comprobadas en el
teléfono. Lo que queda es abrir un socket UDP, que no envía nada, y mirar qué
IP local eligió el sistema: 192.168.x es el router de casa y 100.64-127.x es
el CGNAT del operador. Para lo que no se puede deducir del rango —el 10.x lo
usan los dos— están `--soy-wifi` y `--soy-datos`, que apuntan la red de una
vez para siempre en pipeline_state/redes_conocidas.json.

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
import socket
import logging
import argparse
import subprocess
from datetime import datetime

import cola       # CARPETA_ESTADO y la cola de candidatos
import almacen    # escritura atómica: Android mata procesos a media escritura

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("buscar_diario")

RUTA_PLAN = os.path.join(cola.CARPETA_ESTADO, "busqueda_diaria.json")
# Las redes que el dueño del proyecto marcó a mano con --soy-wifi / --soy-datos.
# Solo guarda los tres primeros octetos de la IP local (192.168.1, por ejemplo):
# no sale del teléfono, no dice dónde está nadie, y es lo único que hace falta
# para distinguir el router de casa de la radio del operador.
RUTA_REDES = os.path.join(cola.CARPETA_ESTADO, "redes_conocidas.json")

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


def ip_de_salida():
    """La IP local por la que saldría el tráfico, sin enviar nada ni pedir
    permisos: un socket UDP «conectado» no manda un solo paquete, pero obliga
    al sistema a elegir interfaz y eso ya se puede leer.

    Es lo único que queda en un Termux de Google Play: ahí Termux:API no
    existe, y Android 11 cerró tanto el netlink de `ip route` como
    /sys/class/net. Comprobado en el teléfono, no deducido de la
    documentación."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("1.1.1.1", 80))
            return s.getsockname()[0]
        finally:
            s.close()
    except OSError:
        return None


def prefijo_de(ip):
    """192.168.1.34 → '192.168.1'. La parte que identifica a la red."""
    trozos = (ip or "").split(".")
    return ".".join(trozos[:3]) if len(trozos) == 4 else ""


def cargar_redes():
    try:
        redes = almacen.cargar(RUTA_REDES, {})
    except (json.JSONDecodeError, OSError):
        return {}
    return redes if isinstance(redes, dict) else {}


def marcar_red(tipo):
    """Apunta la red en la que se está ahora mismo como wifi o como datos."""
    ip = ip_de_salida()
    prefijo = prefijo_de(ip)
    if not prefijo:
        return None, None
    redes = cargar_redes()
    redes[prefijo] = tipo
    almacen.guardar(RUTA_REDES, redes)
    return prefijo, ip


def _wifi_por_ip(ip=None, redes=None):
    """Sin Termux:API y sin netlink, la IP local es lo que queda.

    Lo aprendido manda: una red marcada con --soy-wifi o --soy-datos decide
    sin discusión. Si no se conoce, se mira el rango:

      192.168.x / 172.16-31.x  → red doméstica, es WiFi
      100.64-127.x             → el CGNAT de un operador, son datos
      10.x                     → lo usan los dos; no se decide

    Adivinar mal aquí tiene dos precios distintos: decir «datos» cuando hay
    WiFi solo retrasa la búsqueda media hora, decir «WiFi» cuando hay datos
    gasta el plan. Por eso el 10.x se queda sin respuesta en vez de apostar.
    """
    ip = ip if ip is not None else ip_de_salida()
    prefijo = prefijo_de(ip)
    if not prefijo:
        return None
    redes = cargar_redes() if redes is None else redes
    conocida = redes.get(prefijo)
    if conocida:
        return conocida == "wifi"
    octetos = [int(o) for o in prefijo.split(".")]
    if octetos[0] == 192 and octetos[1] == 168:
        return True
    if octetos[0] == 172 and 16 <= octetos[1] <= 31:
        return True
    if octetos[0] == 100 and 64 <= octetos[1] <= 127:
        return False
    return None


def hay_wifi():
    """True, False, o None cuando no hay forma de saberlo desde aquí."""
    for sonda in (_wifi_por_termux, _wifi_por_ruta, _wifi_por_ip):
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
    ap.add_argument("--soy-wifi", action="store_true",
                    help="Apunta la red en la que estás ahora como WiFi (hazlo una vez, en casa).")
    ap.add_argument("--soy-datos", action="store_true",
                    help="Apunta la red en la que estás ahora como datos móviles.")
    args = ap.parse_args(argv or [])

    if args.soy_wifi or args.soy_datos:
        tipo = "wifi" if args.soy_wifi else "datos"
        prefijo, ip = marcar_red(tipo)
        if not prefijo:
            print(" No hay red ahora mismo: no se puede apuntar nada.")
            return
        print(f" Apuntado: la red {prefijo}.x es {tipo} (IP de este móvil: {ip})")
        print(" A partir de ahora la búsqueda diaria lo sabrá sin preguntarle a nadie.")
        return

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
        ip = ip_de_salida()
        prefijo = prefijo_de(ip)
        conocida = cargar_redes().get(prefijo)
        dice = {True: "sí", False: "no (datos móviles)", None: "no se puede saber desde aquí"}[red]
        porque = (f"red {prefijo}.x apuntada como {conocida}" if conocida
                  else f"por el rango de la IP ({ip})" if ip
                  else "sin red")
        print(f" WiFi      : {dice} — {porque}")
        if red is None and ip:
            print("             Si estás en WiFi ahora: python buscar_diario.py --soy-wifi")
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
                        "(Si esta red sí es WiFi: python buscar_diario.py --soy-wifi. Para "
                        "buscar también con datos: \"busqueda_diaria_solo_wifi\": false en "
                        "config_trends.json)")
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
