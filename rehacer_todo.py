"""
Rehace TODOS los videos (los ya publicados en YouTube y los que seguían
pendientes) para que se regeneren con el fix de sincronización de
subtítulos karaoke (timing real por palabra en vez de reparto por oración).

Qué hace, en orden:
  1. Borra de YouTube cada video que aparece en pipeline_state/publicados.json
     (incluye los que ya están públicos y los que seguían en la ventana de
     revisión privada/programada).
  2. Vacía publicados.json — así vuelven a contar como "pendientes" para
     publisher.py.
  3. Borra los archivos .mp4 locales en la carpeta de salida, para que
     generar_video_maestro.py los vuelva a renderizar desde cero (no solo
     reusar el archivo viejo).

Después de correr esto:
  python generar_video_maestro.py   # re-renderiza todo desde guion.txt
  python publisher.py               # re-sube todo (respetando el límite
                                     # diario real de YouTube; si no alcanza
                                     # en una corrida, sigue en la próxima)

guion.txt NO se toca — el texto de las historias no cambió, solo el
renderizado (audio/subtítulos/video), así que no hace falta volver a
llamar a Gemini ni a Reddit.

Uso:
  python rehacer_todo.py            # pide confirmación antes de borrar
  python rehacer_todo.py --si       # no pide confirmación (para correrlo
                                     # desatendido, p.ej. ya decidiste)
"""
import os
import sys
import json
import time
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("rehacer_todo")

import publisher


def main():
    confirmar = "--si" not in sys.argv

    publicados = publisher.cargar_json(publisher.RUTA_PUBLICADOS, [])
    if not publicados:
        logger.info("publicados.json está vacío — no hay nada que borrar de YouTube.")
    else:
        logger.info(f"Se van a borrar {len(publicados)} video(s) del canal de YouTube:")
        for p in publicados:
            logger.info(f"  - {p.get('titulo_youtube', '(sin título)')} ({p.get('video_id')})")

        if confirmar:
            resp = input("\n¿Confirmas que quieres borrarlos TODOS de YouTube y volver a subirlos? (escribe 'si'): ").strip().lower()
            if resp != "si":
                logger.info("Cancelado. No se borró nada.")
                return

        servicio_yt = publisher.obtener_servicio_youtube()
        publicados_restantes = []
        borrados = 0

        for p in publicados:
            video_id = p.get("video_id")
            if not video_id:
                publicados_restantes.append(p)
                continue
            try:
                servicio_yt.videos().delete(id=video_id).execute()
                borrados += 1
                logger.info(f"🗑️  Borrado de YouTube: {p.get('titulo_youtube', video_id)}")
            except Exception as exc:
                # Si ya no existe (borrado a mano antes) o falla la llamada,
                # lo dejamos registrado como estaba en vez de reintentar subirlo
                # a ciegas — mejor revisarlo a mano si esto pasa seguido.
                logger.warning(f"No se pudo borrar {video_id} ({exc}); se deja como estaba.")
                publicados_restantes.append(p)
            time.sleep(0.3)

        publisher.guardar_json(publisher.RUTA_PUBLICADOS, publicados_restantes)
        logger.info(f"{borrados} video(s) borrado(s) de YouTube. publicados.json actualizado.")

    cfg = publisher.cargar_config()
    carpeta_salida = cfg["carpeta_salida"]
    borrados_local = 0
    if os.path.isdir(carpeta_salida):
        for nombre in os.listdir(carpeta_salida):
            if nombre.lower().endswith(".mp4"):
                try:
                    os.remove(os.path.join(carpeta_salida, nombre))
                    borrados_local += 1
                except Exception as exc:
                    logger.warning(f"No se pudo borrar {nombre}: {exc}")
    logger.info(f"{borrados_local} archivo(s) .mp4 local(es) borrado(s) en {carpeta_salida}.")

    logger.info(
        "\nListo. Ahora corre:\n"
        "  python generar_video_maestro.py\n"
        "  python publisher.py\n"
        "(o 'python pipeline.py --desde video' para encadenar ambos)."
    )


if __name__ == "__main__":
    main()
