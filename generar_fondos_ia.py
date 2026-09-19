"""
Generar Fondos IA — fabrica videos de fondo con el motor de HyperFrames
(`hyperframes_broll.py`) y los deja con el mismo nombre que los de Pexels,
para que la FASE 2 del render los use sin enterarse de dónde salieron.

## Por qué existe esto y no se llama al motor durante el render

HyperFrames renderiza pidiéndole frames sueltos a Chrome headless. En Termux
sobre Android no hay Chrome que valga: el binario que se baja el CLI es de
glibc y Android es bionic. Así que el motor **no corre en el teléfono**, que
es el único sitio donde este pipeline se ejecuta de verdad.

La salida es la misma que la de `descargar_fondos.py`: en vez de traer los
clips de Pexels, los fabrica. Se corre en el runner de GitHub Actions
(.github/workflows/fondos_ia.yml), se bajan los archivos por wifi y a partir
de ahí son fondos normales. Nada del render diario cambia.

## Por qué las ideas son fijas y no salen del título de la historia

`generar_clip_cacheado` cachea por el texto de la idea. Si la idea lleva el
título de cada historia, la clave es única siempre y la caché no acierta
nunca: una llamada a Gemini y un render entero por video. Aquí las ideas son
un catálogo cerrado, así que el material se genera una vez y se reutiliza,
igual que el gameplay de la SD.

Requiere: Node.js >= 22, ffmpeg/ffprobe, GEMINI_API_KEY, y que
hyperframes_broll.py (y su hyperframes_nucleo.py) estén en el repo;
llegan con el PR del motor de fondo.

Uso:
  python generar_fondos_ia.py                    # 1 clip de cada emoción
  python generar_fondos_ia.py --ver              # qué haría, sin gastar nada
  python generar_fondos_ia.py --emocion tension  # solo una
  python generar_fondos_ia.py --cuantos 2        # cuántos por emoción
  python generar_fondos_ia.py --horizontal       # para los videos largos
"""
import os
import sys
import shutil
import argparse
import logging

import almacen   # leer y escribir los .json de estado
import secretos  # carga secretos.env si las claves no están en el entorno

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CARPETA_ESTADO = os.path.join(BASE_DIR, "pipeline_state")
RUTA_MANIFIESTO = os.path.join(CARPETA_ESTADO, "fondos_ia_manifiesto.json")

# Segundos de cada composición. La FASE 2 corta trozos de 6-12 s, así que con
# 20 s hay de donde elegir sin que el render cueste una eternidad: HyperFrames
# va a ~3x tiempo real, o sea un minuto por clip.
SEGUNDOS = 20.0

# Catálogo cerrado, a propósito (ver cabecera). Cada entrada es una idea
# visual abstracta; el perfil visual del módulo ya impone la paleta, las zonas
# libres para los subtítulos y que el bucle cierre.
IDEAS = {
    "tension": [
        "rejilla en perspectiva que se desplaza hacia el horizonte, sin fin",
        "ondas concéntricas lentas sobre un fondo casi negro",
        "franjas diagonales que recorren el cuadro a ritmo constante",
    ],
    "melancolia": [
        "partículas grandes a la deriva, como polvo en un haz de luz",
        "degradado que respira despacio entre dos azules fríos",
        "líneas finas que se dibujan y se borran sin prisa",
    ],
    "giro": [
        "formas geométricas que rotan despacio y se encajan entre sí",
        "bloques que se desplazan un paso completo y vuelven a empezar",
        "espiral lenta que gira sobre su centro",
    ],
}

guardar_json = almacen.guardar
cargar_json = almacen.cargar

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("generar_fondos_ia")


def cargar_motor():
    """Importa el motor con un mensaje claro si no está.

    Se importa aquí y no arriba: sin el módulo, `--ver` y `--ayuda` tienen que
    seguir funcionando para poder mirar qué haría esto."""
    try:
        import hyperframes_broll
    except ImportError:
        raise SystemExit(
            "Falta el motor: hacen falta hyperframes_broll.py y su "
            "hyperframes_nucleo.py. Llegan con el PR del motor de fondo con "
            "HyperFrames; hasta que esté fusionado, este script no tiene motor "
            "que usar."
        )
    return hyperframes_broll


def comprobar_entorno(motor):
    """Falla ahora y no a mitad del lote."""
    motor.comprobar_dependencias()
    if not os.environ.get("GEMINI_API_KEY"):
        raise SystemExit(
            "Falta GEMINI_API_KEY. Es gratis en https://aistudio.google.com/apikey. "
            "En el teléfono va en secretos.env; en el runner, como secret del repo."
        )


def nombre_destino(prefijo, emocion, indice):
    return f"{prefijo}_ia_{emocion}{indice}.mp4"


def main(argv=None):
    p = argparse.ArgumentParser(description="Fabricar videos de fondo con HyperFrames.")
    p.add_argument("--emocion", help="solo esta (" + ", ".join(IDEAS) + ")")
    p.add_argument("--cuantos", type=int, default=1,
                   help="cuántos clips por emoción (por defecto 1)")
    p.add_argument("--horizontal", action="store_true",
                   help="16:9 para los videos largos en vez de 9:16")
    p.add_argument("--ver", action="store_true", help="enseñar qué haría, sin generar nada")
    args = p.parse_args(argv)

    ideas = IDEAS
    if args.emocion:
        if args.emocion not in IDEAS:
            raise SystemExit(f"Emoción desconocida: {args.emocion}. Hay: {', '.join(IDEAS)}")
        ideas = {args.emocion: IDEAS[args.emocion]}

    vertical = not args.horizontal
    prefijo = "fondo_vertical" if vertical else "fondo_horizontal"
    aspecto = "9:16" if vertical else "16:9"

    motor = None
    if not args.ver:
        motor = cargar_motor()
        comprobar_entorno(motor)

    manifiesto = cargar_json(RUTA_MANIFIESTO, {})
    hechos = 0

    for emocion, lista in ideas.items():
        for indice in range(1, min(args.cuantos, len(lista)) + 1):
            idea = lista[indice - 1]
            nombre = nombre_destino(prefijo, emocion, indice)
            destino = os.path.join(BASE_DIR, nombre)

            if os.path.isfile(destino) and os.path.getsize(destino) > 0:
                logger.info(f"{nombre}: ya está, no se regenera.")
                continue
            if args.ver:
                logger.info(f"  · generaría {nombre} — «{idea}»")
                continue

            logger.info(f"{nombre}: componiendo {SEGUNDOS:.0f}s ({aspecto})...")
            clip = motor.generar_clip_cacheado(
                idea, aspecto=aspecto, duracion_seg=SEGUNDOS,
                # El horizontal, no el reflexivo: estos fondos se loopean y
                # se cortan como metraje de archivo, así que la composición
                # tiene que cerrar el bucle. El reflexivo no lo cierra.
                perfil=motor.PERFIL_HISTORIA_VERTICAL if vertical
                else motor.PERFIL_HISTORIA_HORIZONTAL,
            )
            if not clip:
                logger.warning(f"  ⚠️ {nombre}: el motor no devolvió nada, se salta.")
                continue

            # El clip vive en la caché del motor; aquí se copia con el nombre
            # que busca el render. A un temporal oculto primero: si el proceso
            # muere copiando, lo que quede no debe parecer un fondo bueno.
            parcial = os.path.join(BASE_DIR, f".{nombre}.parcial")
            try:
                shutil.copyfile(clip, parcial)
                os.replace(parcial, destino)
            except OSError as exc:
                if os.path.exists(parcial):
                    os.remove(parcial)
                logger.warning(f"  ⚠️ {nombre}: no se pudo copiar ({exc}).")
                continue

            mb = os.path.getsize(destino) / 1024 / 1024
            logger.info(f"  ✅ {nombre} — {mb:.1f} MB")
            hechos += 1
            manifiesto[nombre] = {
                "idea": idea,
                "emocion": emocion,
                "aspecto": aspecto,
                "segundos": SEGUNDOS,
                "motor": "hyperframes",
            }
            # Después de cada clip y no al final: un render de estos tarda un
            # minuto largo y no hay por qué perder los anteriores si se corta.
            guardar_json(RUTA_MANIFIESTO, manifiesto)

    if args.ver:
        logger.info("Listo (simulación).")
    else:
        logger.info(f"Listo: {hechos} clip(s) nuevo(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
