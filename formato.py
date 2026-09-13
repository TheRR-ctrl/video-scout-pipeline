"""Cuándo el canal vuelve a hacer videos largos.

Los 18 videos largos del canal sumaban 43 vistas entre todos; ninguno pasó de
10. Los 48 shorts, 29.000. No es que gustaran menos: un canal sin base de
suscriptores no recibe tráfico de «sugeridos» ni de «inicio», que es de donde
vive el formato largo, mientras que el feed de Shorts empuja a desconocidos
sin que nadie te conozca. Renderizar cinco minutos en el teléfono para sacar
dos vistas es tirar el trabajo.

Así que los largos quedan bloqueados, pero no a mano y para siempre: eso se
olvida. Se bloquean con una condición, y cuando el canal la cumple se abren
solos. La condición por omisión son 500 suscriptores, que es cuando el canal
ya tiene a quién avisar de un video nuevo.

El recuento sale del propio canal (channels.list con mine=true), así que no
hace falta configurar ningún id: usa el youtube_token.json que ya existe para
subir. Se guarda en caché medio día porque esto se consulta en cada render y
la cuota de la API es limitada.

    python formato.py            # en qué estado está y por qué
    python formato.py --refrescar # vuelve a preguntarle a YouTube
"""
import os
import sys
import json
import logging
import argparse
from datetime import datetime, timezone

CARPETA_ESTADO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pipeline_state")
RUTA_CACHE = os.path.join(CARPETA_ESTADO, "suscriptores.json")

# Medio día. El umbral no se cruza en una tarde, y así un lote de veinte
# renders hace una sola llamada a la API en vez de veinte.
HORAS_CACHE = 12

DEFECTOS = {
    # Mientras esté en False, una historia que no quepa en un short no se
    # renderiza: se queda en la cola esperando a que los largos se abran.
    "largos_activos": False,
    "umbral_suscriptores_largos": 500,
    # Para saltarse la condición sin esperar: útil si quieres probar un largo
    # suelto, o si decides que el canal ya está listo antes del umbral.
    "forzar_largos": False,
}

logger = logging.getLogger("formato")


def _config():
    """La config del pipeline, con los valores de arriba como respaldo.

    Se importa aquí dentro y no arriba porque generar_video_maestro tarda en
    cargar (PIL, numpy) y este módulo lo importan también scripts que no
    renderizan nada.
    """
    try:
        import generar_video_maestro as gvm
        cfg = gvm.cargar_config(os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json"))
    except Exception:
        cfg = {}
    return {k: cfg.get(k, v) for k, v in DEFECTOS.items()}, cfg


def limite_short_sec():
    """El tope de duración de un Short, según la config del render."""
    _, cfg = _config()
    return float(cfg.get("duracion_max_short_sec", 180.0))


def _cache_valida():
    datos = _leer_cache()
    if not datos or "consultado_en" not in datos:
        return None
    try:
        cuando = datetime.strptime(datos["consultado_en"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    if (datetime.now(timezone.utc) - cuando).total_seconds() > HORAS_CACHE * 3600:
        return None
    return datos


def _leer_cache():
    try:
        with open(RUTA_CACHE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _escribir_cache(datos):
    os.makedirs(CARPETA_ESTADO, exist_ok=True)
    tmp = RUTA_CACHE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(datos, f, ensure_ascii=False, indent=2)
    os.replace(tmp, RUTA_CACHE)


def suscriptores(refrescar=False):
    """Cuántos suscriptores tiene el canal. None si no se pudo saber.

    Devuelve (numero, de_donde). "de_donde" distingue el dato fresco del
    guardado y de no haber podido preguntar, porque no es lo mismo: con un
    error de red conviene quedarse con el último número conocido, no tratar
    el canal como si tuviera cero suscriptores.
    """
    if not refrescar:
        datos = _cache_valida()
        if datos:
            return datos.get("suscriptores"), "caché"

    try:
        import publisher
        servicio = publisher.obtener_servicio_youtube()
        resp = servicio.channels().list(part="statistics", mine=True).execute()
        items = resp.get("items") or []
        if not items:
            return _ultimo_conocido("el canal no devolvió estadísticas")
        n = int(items[0]["statistics"].get("subscriberCount", 0))
    except Exception as exc:
        return _ultimo_conocido(str(exc))

    _escribir_cache({
        "suscriptores": n,
        "consultado_en": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    })
    return n, "YouTube"


def _ultimo_conocido(motivo):
    datos = _leer_cache()
    if datos and datos.get("suscriptores") is not None:
        logger.debug(f"No se pudo consultar YouTube ({motivo}); se usa el último número conocido.")
        return datos["suscriptores"], "caché vencida"
    return None, f"no se pudo consultar ({motivo})"


# La política dentro de una misma corrida no cambia, y se consulta una vez
# por historia: sin esto, un lote de veinte intentaría abrir el servicio de
# YouTube veinte veces, y cuando no hay token son veinte importaciones de
# publisher (google-api-python-client) para llegar al mismo "no se pudo".
_MEMO = {}


def politica(refrescar=False):
    """Si ahora mismo se pueden hacer largos, y por qué sí o por qué no."""
    if not refrescar and "p" in _MEMO:
        return _MEMO["p"]
    p = _politica(refrescar)
    _MEMO["p"] = p
    return p


def _politica(refrescar):
    aj, _ = _config()
    umbral = int(aj["umbral_suscriptores_largos"])

    if aj["forzar_largos"]:
        return {"permite_largos": True, "suscriptores": None, "umbral": umbral,
                "motivo": "forzado a mano en la config (forzar_largos)"}

    if aj["largos_activos"]:
        return {"permite_largos": True, "suscriptores": None, "umbral": umbral,
                "motivo": "activados a mano en la config (largos_activos)"}

    n, de_donde = suscriptores(refrescar)
    if n is None:
        return {"permite_largos": False, "suscriptores": None, "umbral": umbral,
                "motivo": f"siguen bloqueados: {de_donde}"}

    if n >= umbral:
        return {"permite_largos": True, "suscriptores": n, "umbral": umbral,
                "motivo": f"{n} suscriptores, el umbral era {umbral} ({de_donde})"}

    return {"permite_largos": False, "suscriptores": n, "umbral": umbral,
            "motivo": f"{n} de {umbral} suscriptores ({de_donde})"}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Estado del formato largo.")
    ap.add_argument("--refrescar", action="store_true",
                    help="Pregunta a YouTube en vez de usar la caché.")
    args = ap.parse_args(argv if argv is not None else sys.argv[1:])

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    p = politica(args.refrescar)
    tope = limite_short_sec()

    print()
    if p["permite_largos"]:
        print("  Videos largos: ABIERTOS")
        print(f"  {p['motivo']}")
        print("\n  Las historias que no quepan en un short se renderizan en")
        print("  horizontal, como antes.")
    else:
        print("  Videos largos: BLOQUEADOS")
        print(f"  {p['motivo']}")
        print(f"\n  Una historia que pase de {tope/60:.0f} minutos no se renderiza: se queda")
        print("  en la cola esperando. Nada se pierde.")
        if p["suscriptores"] is None:
            print("\n  Para poder comprobar el umbral solo, hace falta el token de")
            print("  YouTube (python generar_youtube_token.py). Mientras no lo haya,")
            print("  los largos siguen bloqueados.")
        print("\n  Para abrirlos sin esperar al umbral, en config.json:")
        print('    "forzar_largos": true')
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
