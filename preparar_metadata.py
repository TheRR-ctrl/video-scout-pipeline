"""
Preparar metadata — escribe con antelación el título, la descripción y los
hashtags con los que se subirá cada video renderizado.

Por qué existe: esa metadata la generaba publisher.py justo antes de subir,
así que no había ningún momento en el que se pudiera mirar. Si Gemini elegía
un título flojo, te enterabas cuando ya estaba en YouTube. Adelantando el
paso, el panel puede enseñarla y dejarte corregirla, y publisher.py sube
exactamente lo que quedó guardado.

    python preparar_metadata.py              los que aún no la tienen
    python preparar_metadata.py --rehacer    todos, descartando lo guardado
    python preparar_metadata.py --solo 3     solo la historia 3

Lo editado a mano se respeta: --rehacer avisa y pide confirmación antes de
pisar algo con origen "manual", porque perder una corrección tuya por un
comando escrito de más sería el peor resultado posible aquí.

Requiere GEMINI_API_KEY (ver secretos.py). Sin ella cae a la metadata de
respaldo, que es genérica pero funcional.
"""
import os
import sys
import json
import logging
import argparse

import secretos  # carga secretos.env si las claves no están en el entorno
from titulos import recortar_titulo, limpiar_titulo, largo_youtube

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CARPETA_ESTADO = os.path.join(BASE_DIR, "pipeline_state")
RUTA_METADATA = os.path.join(CARPETA_ESTADO, "metadata.json")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("preparar_metadata")


def cargar_json(ruta, default):
    try:
        with open(ruta, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def guardar_json(ruta, datos):
    os.makedirs(os.path.dirname(ruta), exist_ok=True)
    with open(ruta, "w", encoding="utf-8") as f:
        json.dump(datos, f, ensure_ascii=False, indent=2)


def videos_renderizados():
    """Los del registro cuyo archivo sigue en disco."""
    import generar_video_maestro as gvm
    cfg = gvm.cargar_config()
    lote = cargar_json(os.path.join(cfg["carpeta_salida"], "resultado_lote.json"), {})
    return [v for v in lote.get("completados", []) if os.path.exists(v.get("ruta", ""))]


def main():
    parser = argparse.ArgumentParser(description="Genera la metadata de publicación por adelantado.")
    parser.add_argument("--rehacer", action="store_true",
                        help="Regenerar también la que ya existe.")
    parser.add_argument("--solo", type=int, metavar="N",
                        help="Solo el video con ese número de historia.")
    parser.add_argument("--forzar", action="store_true",
                        help="Con --rehacer, pisar también lo editado a mano sin preguntar.")
    args = parser.parse_args()

    videos = videos_renderizados()
    if args.solo is not None:
        videos = [v for v in videos if v.get("numero") == args.solo]
    if not videos:
        raise SystemExit("No hay videos renderizados que preparar.")

    almacen = cargar_json(RUTA_METADATA, {})

    import publisher
    pendientes = []
    for v in videos:
        clave = publisher.clave_metadata(v["ruta"])
        guardada = almacen.get(clave)
        if guardada and guardada.get("titulo_youtube") and not args.rehacer:
            continue
        if guardada and guardada.get("origen") == "manual" and not args.forzar:
            logger.warning(
                f"⏭️  {clave}: la editaste a mano, se conserva. "
                f"Usa --forzar si de verdad quieres regenerarla."
            )
            continue
        pendientes.append((clave, v))

    if not pendientes:
        logger.info("Todo tiene metadata al día. Nada que hacer.")
        return

    # El cliente se crea solo si hay trabajo: sin GEMINI_API_KEY su
    # construcción falla, y no tiene sentido caerse cuando no había nada
    # que generar.
    client = None
    try:
        from google import genai
        client = genai.Client()
    except Exception as exc:
        logger.warning(f"Sin Gemini ({exc}); se usará la metadata de respaldo.")

    for i, (clave, v) in enumerate(pendientes, 1):
        logger.info(f"[{i}/{len(pendientes)}] {clave}")
        if client is not None:
            try:
                m = publisher.revisar_y_generar_metadata(client, v["titulo"], v.get("cuerpo", ""))
                m["origen"] = "gemini"
            except Exception as exc:
                logger.warning(f"  Falló Gemini ({exc}); metadata de respaldo.")
                m = publisher.metadata_de_respaldo(v)
                m["origen"] = "respaldo"
        else:
            m = publisher.metadata_de_respaldo(v)
            m["origen"] = "respaldo"

        # Se recorta al guardar, no al subir: así lo que enseña el panel es
        # exactamente lo que irá a YouTube. Gemini se pasa del límite a
        # menudo aunque se le pida que no.
        crudo = m["titulo_youtube"]
        m["titulo_youtube"] = recortar_titulo(crudo)
        if m["titulo_youtube"] != limpiar_titulo(crudo):
            logger.info(f"  ✂️  Título recortado de {largo_youtube(crudo)} a "
                        f"{largo_youtube(m['titulo_youtube'])} caracteres.")

        almacen[clave] = m
        guardar_json(RUTA_METADATA, almacen)   # se guarda sobre la marcha
        estado = "✅" if m.get("aprobado") else f"⛔ {m.get('motivo_rechazo', '')[:60]}"
        logger.info(f"  {estado} «{m['titulo_youtube']}»  #{' #'.join(m['hashtags'][:6])}")

    logger.info(f"Listo: {len(pendientes)} preparada(s). Revísalas en el panel antes de publicar.")


if __name__ == "__main__":
    main()
