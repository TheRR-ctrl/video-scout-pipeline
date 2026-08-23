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

Salida: código de retorno != 0 si alguna etapa falló, para que cron/CI lo
reporte como corrida fallida.
"""
import sys
import argparse
import logging
import traceback

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("pipeline")

ETAPAS = ["candidatos", "guion", "video", "publicar"]


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
    args = parser.parse_args()

    i_desde, i_hasta = ETAPAS.index(args.desde), ETAPAS.index(args.hasta)
    if i_desde > i_hasta:
        parser.error("--desde no puede ir después de --hasta")

    resultados = {}

    if i_desde <= ETAPAS.index("candidatos") <= i_hasta:
        import trend_scout
        resultados["candidatos"] = correr_etapa("candidatos (trend_scout)", trend_scout.main)

    if i_desde <= ETAPAS.index("guion") <= i_hasta:
        import script_writer
        resultados["guion"] = correr_etapa("guion (script_writer)", script_writer.main)

    if i_desde <= ETAPAS.index("video") <= i_hasta:
        import generar_video_maestro
        resultados["video"] = correr_etapa("video (generar_video_maestro)", generar_video_maestro.renderizar_lote_historias)

    if i_desde <= ETAPAS.index("publicar") <= i_hasta:
        import publisher
        resultados["publicar"] = correr_etapa("publicar (publisher)", publisher.main)

    logger.info("===== Resumen =====")
    for nombre, ok in resultados.items():
        logger.info(f"  {'✅' if ok else '❌'} {nombre}")

    if not all(resultados.values()):
        sys.exit(1)


if __name__ == "__main__":
    main()
