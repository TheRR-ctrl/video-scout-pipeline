"""
Pipeline — orquesta las 4 etapas en una sola corrida desatendida:

  trend_scout.py -> script_writer.py -> generar_video_maestro.py -> publisher.py

Pensado para lanzarse solo (cron, Termux:Boot + termux-job-scheduler, Tarea
Programada de Windows, GitHub Actions, etc.) sin intervención manual. Cada
etapa corre en su propio proceso: si una falla, se registra el error pero las
etapas ya completadas (candidatos, guion, videos) quedan guardadas en disco y
la siguiente corrida puede retomar desde ahí (todas las etapas son
incrementales/idempotentes).

Uso:
  python pipeline.py                # corre las 4 etapas
  python pipeline.py --hasta guion  # corre solo trend_scout + script_writer
  python pipeline.py --desde video  # corre solo video + publisher (asume
                                     # que guion.txt ya existe)
  python pipeline.py --forzar       # ignora el freno de colchón (ver abajo)

Salida: código de retorno != 0 si alguna etapa falló, para que cron/CI lo
reporte como corrida fallida.

Freno de sobreproducción: con publisher.py subiendo como mucho 1 video por
corrida (max_subidas_por_corrida), generar contenido nuevo 2x/semana sin
parar acumula un colchón sin fin (gastando batería/almacenamiento/llamadas a
Gemini para nada). Por eso, antes de las etapas "candidatos" y "guion", se
revisa cuántos videos ya renderizados siguen sin publicar; si hay
UMBRAL_BACKLOG_VIDEOS o más (suficiente colchón para varios días), esas dos
etapas se saltan solas y se loguea el motivo. --forzar la ignora.
"""
import os
import sys
import json
import argparse
import logging
import traceback

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("pipeline")

ETAPAS = ["candidatos", "guion", "video", "publicar", "tiktok"]
UMBRAL_BACKLOG_VIDEOS = 10


def videos_pendientes_de_publicar():
    """Cuenta los videos ya renderizados que todavía no se subieron ni
    rechazaron, para no generar más contenido del que se alcanza a publicar."""
    try:
        import publisher
        cfg = publisher.cargar_config()
        ruta_resultado = os.path.join(cfg["carpeta_salida"], "resultado_lote.json")
        if not os.path.exists(ruta_resultado):
            return 0
        with open(ruta_resultado, "r", encoding="utf-8") as f:
            completados = json.load(f).get("completados", [])
        publicados = publisher.cargar_json(publisher.RUTA_PUBLICADOS, [])
        rechazados = publisher.cargar_json(publisher.RUTA_RECHAZADOS, [])
        procesados = {p["ruta"] for p in publicados} | {r["ruta"] for r in rechazados}
        return len([v for v in completados if v["ruta"] not in procesados])
    except Exception as exc:
        logger.warning(f"No se pudo calcular el colchón pendiente ({exc}); no se frena la generación.")
        return 0


def correr_etapa(nombre, fn):
    logger.info(f"===== Etapa: {nombre} =====")
    try:
        fn()
        logger.info(f"Etapa '{nombre}' OK.")
        return True
    except SystemExit as exc:
        # Algunos scripts podrían llamar sys.exit(); solo lo tratamos como
        # fallo si el código de salida es distinto de 0/None.
        if exc.code not in (0, None):
            logger.error(f"Etapa '{nombre}' terminó con código {exc.code}.")
            return False
        return True
    except Exception:
        logger.error(f"Etapa '{nombre}' falló:\n{traceback.format_exc()}")
        return False


def main():
    parser = argparse.ArgumentParser(description="Orquesta el pipeline completo.")
    parser.add_argument("--desde", choices=ETAPAS, default=ETAPAS[0])
    parser.add_argument("--hasta", choices=ETAPAS, default=ETAPAS[-1])
    parser.add_argument("--forzar", action="store_true", help="Ignora el freno de colchón y genera igual.")
    args = parser.parse_args()

    i_desde, i_hasta = ETAPAS.index(args.desde), ETAPAS.index(args.hasta)
    if i_desde > i_hasta:
        parser.error("--desde no puede ir después de --hasta")

    resultados = {}

    frena_generacion = False
    if not args.forzar and (i_desde <= ETAPAS.index("candidatos") <= i_hasta or i_desde <= ETAPAS.index("guion") <= i_hasta):
        pendientes = videos_pendientes_de_publicar()
        if pendientes >= UMBRAL_BACKLOG_VIDEOS:
            frena_generacion = True
            logger.info(
                f"Colchón de {pendientes} video(s) sin publicar (>= {UMBRAL_BACKLOG_VIDEOS}) — "
                f"se saltan 'candidatos' y 'guion' esta corrida. Usa --forzar para generar igual."
            )

    if not frena_generacion and i_desde <= ETAPAS.index("candidatos") <= i_hasta:
        import trend_scout
        resultados["candidatos"] = correr_etapa("candidatos (trend_scout)", trend_scout.main)

        # YouTube es una segunda fuente para la MISMA cola, no una etapa
        # aparte: si Reddit está bloqueado o ya se agotó el /top/ del día, de
        # aquí siguen saliendo historias. Va después a propósito, para que un
        # fallo suyo (falta youtube-transcript-api, canal caído) no impida que
        # los candidatos de Reddit lleguen al escritor de guiones.
        import youtube_scout
        if youtube_scout.cargar_config().get("youtube_activo", True):
            resultados["candidatos_youtube"] = correr_etapa(
                "candidatos (youtube_scout)", youtube_scout.main
            )

    if not frena_generacion and i_desde <= ETAPAS.index("guion") <= i_hasta:
        import script_writer
        resultados["guion"] = correr_etapa("guion (script_writer)", script_writer.main)

    if i_desde <= ETAPAS.index("video") <= i_hasta:
        import generar_video_maestro
        resultados["video"] = correr_etapa("video (generar_video_maestro)", generar_video_maestro.renderizar_lote_historias)

    if i_desde <= ETAPAS.index("publicar") <= i_hasta:
        import publisher
        resultados["publicar"] = correr_etapa("publicar (publisher)", publisher.main)

    # TikTok va detrás de YouTube a propósito: reutiliza el veredicto de
    # calidad que dejó el publisher en metadata.json en vez de volver a
    # juzgar el mismo video. Si está apagado en config.json, la etapa
    # termina sola sin hacer nada, así que no estorba a quien no la use.
    if i_desde <= ETAPAS.index("tiktok") <= i_hasta:
        import tiktok_publisher
        resultados["tiktok"] = correr_etapa("tiktok (tiktok_publisher)", tiktok_publisher.main)

    logger.info("===== Resumen =====")
    for nombre, ok in resultados.items():
        logger.info(f"  {'✅' if ok else '❌'} {nombre}")

    if not all(resultados.values()):
        sys.exit(1)


if __name__ == "__main__":
    main()
