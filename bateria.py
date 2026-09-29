"""
Grabar solo cuando la batería lo aguanta.

Renderizar es lo que más batería gasta de todo el pipeline. Lo que se graba
solo —detrás de «Escribir guiones» con la cadena encendida, o desde el cron
de pipeline.py— mira aquí antes de empezar: si la batería está por debajo
del umbral (60 % de fábrica) y el teléfono no está cargando, no graba y deja
apuntada una espera en pipeline_state/grabar_al_cargar.json. El panel la
enseña y, mientras la haya, mira la batería cada minuto: en cuanto
conectas el cargador (o se apaga el ajuste), graba solo.

Los botones de grabar a mano no pasan por aquí: si lo pulsas, es que
quieres grabar ahora.

Se apaga en Ajustes → Subida («Con poca batería, esperar al cargador»),
que escribe "cuidar_bateria" en config.json.

Lee la batería con termux-battery-status (paquete termux-api, que ya
instala instalar_panel.sh, más la app Termux:API). Si no se puede leer —fuera
de Android, sin la app, en la app nativa— no frena nada: mejor grabar de más
que quedarse esperando a un cargador que no se sabe ver.
"""
import os
import json
import subprocess
from datetime import datetime

import almacen

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RUTA_CONFIG = os.path.join(BASE_DIR, "config.json")
RUTA_ESPERA = os.path.join(BASE_DIR, "pipeline_state", "grabar_al_cargar.json")
UMBRAL_POR_OMISION = 60


def _config():
    datos = almacen.leer(RUTA_CONFIG, {}) or {}
    return datos if isinstance(datos, dict) else {}


def cuidar(cfg=None):
    """¿Está encendido «esperar al cargador con poca batería»?"""
    cfg = _config() if cfg is None else cfg
    return bool(cfg.get("cuidar_bateria", True))


def umbral(cfg=None):
    cfg = _config() if cfg is None else cfg
    try:
        return max(5, min(100, int(cfg.get("bateria_minima_grabar", UMBRAL_POR_OMISION))))
    except (TypeError, ValueError):
        return UMBRAL_POR_OMISION


def leer():
    """{"porcentaje": int, "cargando": bool}, o None si no se puede saber.

    Con timeout: sin la app Termux:API instalada, termux-battery-status se
    queda colgado esperando una respuesta que no llega.
    """
    try:
        res = subprocess.run(["termux-battery-status"], capture_output=True,
                             text=True, timeout=8)
        datos = json.loads(res.stdout) if res.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None
    if not isinstance(datos, dict) or "percentage" not in datos:
        return None
    enchufe = str(datos.get("plugged") or "").upper()
    estado = str(datos.get("status") or "").upper()
    return {
        "porcentaje": int(datos.get("percentage") or 0),
        "cargando": (enchufe.startswith("PLUGGED") or estado in ("CHARGING", "FULL")),
    }


_LEER = object()


def puede_grabar(cfg=None, lectura=_LEER):
    """(True, "") si se puede grabar ya, o (False, motivo) si toca esperar.

    "lectura" es lo que devolvió leer(), para no preguntar dos veces; sin
    pasarla, se lee aquí (y solo si el ajuste está encendido).
    """
    cfg = _config() if cfg is None else cfg
    if not cuidar(cfg):
        return True, ""
    b = leer() if lectura is _LEER else lectura
    if not b or b["cargando"]:
        return True, ""
    minimo = umbral(cfg)
    if b["porcentaje"] >= minimo:
        return True, ""
    return False, (f"Batería al {b['porcentaje']}% y sin cargador (el mínimo para grabar "
                   f"solo es {minimo}%). Grabación en espera: empieza sola al conectarlo.")


def antes_de_grabar_solo(cfg=None):
    """Lo que llaman el cron y la cadena justo antes de grabar sin que nadie
    lo pida: si toca esperar, deja la espera apuntada y devuelve
    (False, motivo); si no, quita la que hubiera y devuelve (True, "")."""
    cfg = _config() if cfg is None else cfg
    b = leer() if cuidar(cfg) else None
    ok, motivo = puede_grabar(cfg, b)
    if ok:
        quitar_espera()
    else:
        marcar_espera(motivo, b["porcentaje"] if b else None)
    return ok, motivo


def en_espera():
    datos = almacen.leer(RUTA_ESPERA, None)
    return datos if isinstance(datos, dict) else None


def marcar_espera(motivo, porcentaje=None):
    previa = en_espera() or {}
    almacen.guardar(RUTA_ESPERA, {
        "desde": previa.get("desde") or datetime.now().isoformat(timespec="seconds"),
        "motivo": motivo,
        "porcentaje": porcentaje,
    })


def quitar_espera():
    try:
        os.remove(RUTA_ESPERA)
    except FileNotFoundError:
        pass


if __name__ == "__main__":
    b = leer()
    print("Batería:", "no se puede leer (¿falta la app Termux:API?)" if not b
          else f"{b['porcentaje']}% {'cargando' if b['cargando'] else 'sin cargador'}")
    ok, motivo = puede_grabar(lectura=b)
    print("Grabar solo:", "sí" if ok else "no — " + motivo)
    print("En espera:", en_espera() or "no")
