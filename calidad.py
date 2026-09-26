"""
Calidad — revisa un video ya renderizado, antes de que se suba.

Por qué existe: hasta ahora la única forma de saber si un video salió bien era
mirarlo entero. Y los fallos que de verdad hacen daño no se ven mirando por
encima —el audio que se corta a mitad de frase, el volumen que quedó doce
decibelios por debajo de lo normal, el medio segundo en negro del principio—
así que se subían igual y se descubrían por las vistas, que para entonces ya
no explican nada.

Esto mide lo que ffmpeg puede contestar con un número: cuánto dura de verdad,
a qué volumen está, si hay silencios, negros o fotogramas congelados. Son
defectos, no opiniones, se arreglan, y salen gratis y sin red — así que se
revisan todos los videos y no una muestra. La opinión de Gemini es otra cosa
y vive en calidad_ia.py.

Lo que esto NO hace: predecir si el video va a funcionar. Que no tenga
defectos no hace que el feed lo reparta. Sirve para no subir algo roto.

Todo se mide en UNA sola pasada de ffmpeg. En un teléfono eso importa: cada
pasada es descodificar el video entero, y cuatro filtros en la misma cadena
cuestan lo mismo que uno.

Uso:
  python calidad.py                   # los que aún no se han revisado
  python calidad.py --todos           # otra vez, todos
  python calidad.py --solo 3          # solo la historia 3
  python calidad.py --archivo v.mp4   # un archivo suelto, sin registro
"""
import os
import re
import json
import logging
import argparse
import subprocess
from datetime import datetime, timezone

import almacen   # leer y escribir los .json de estado

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CARPETA_ESTADO = os.path.join(BASE_DIR, "pipeline_state")
RUTA_CALIDAD = os.path.join(CARPETA_ESTADO, "calidad.json")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("calidad")


# ---------------------------------------------------------------------------
# Los umbrales, en un solo sitio y con el motivo al lado. Un número suelto en
# mitad de una función no se puede discutir; aquí sí.
# ---------------------------------------------------------------------------

# YouTube y TikTok normalizan a unos -14 LUFS: si subes más bajo, te lo dejan
# como está y tu video suena flojo al lado del siguiente; si subes más alto,
# te lo bajan y solo has perdido rango dinámico.
LUFS_OBJETIVO = -14.0
LUFS_FLOJO = -20.0      # se nota en un teléfono en la calle
LUFS_MUY_FLOJO = -25.0  # no se oye
LUFS_PASADO = -9.0      # lo van a bajar de todas formas

# Por encima de -0.5 dBFS el recodificado de la plataforma satura: el pico
# verdadero (true peak) sube al reconstruir la onda, y lo que en tu archivo
# no recortaba, en YouTube sí.
PICO_MAXIMO = -0.5

# 2.6 palabras/segundo es el ritmo de la narración generada, medido sobre los
# videos ya hechos; es la misma constante que usa generar_video_maestro.
# Si el video dura menos del 70% de lo que ese ritmo predice, no es que se
# hable despacio: es que el TTS entregó menos texto del que se le dio. El
# render ya tiene una guarda propia, pero con un margen mucho más ancho
# (palabras/6.0), así que una narración cortada por la mitad la pasa entera.
PALABRAS_POR_SEGUNDO = 2.6
FRACCION_MINIMA = 0.70

SILENCIO_DB = -45        # por debajo de esto no hay voz, solo suelo de ruido
SILENCIO_LARGO = 2.5     # segundos de nada en medio; se nota como un fallo
SILENCIO_ARRANQUE = 0.8  # segundos mudos al principio; ahí está el gancho

NEGRO_MINIMO = 0.3       # segundos en negro que ya se ven
CONGELADO_MINIMO = 2.0   # segundos con la imagen parada

# La tarjeta de intro es una imagen fija, así que freezedetect la marca
# siempre. No es un fallo: es el diseño. Se ignora lo que esté congelado
# dentro de los primeros segundos, que es donde vive la tarjeta.
CONGELADO_PERDONADO_HASTA = 6.0

TIEMPO_MAXIMO = 600      # segundos; en un teléfono descodificar va lento


# ---------------------------------------------------------------------------
# Medir
# ---------------------------------------------------------------------------

def _ffprobe(ruta):
    """Lo que se sabe sin descodificar: duración, tamaño, pistas, resolución."""
    res = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "format=duration,size:stream=codec_type,codec_name,width,height,r_frame_rate",
         "-of", "json", ruta],
        capture_output=True, text=True, timeout=30,
    )
    if res.returncode != 0:
        raise RuntimeError(f"ffprobe falló: {res.stderr.strip()[:200]}")
    datos = json.loads(res.stdout)

    video = next((s for s in datos.get("streams", []) if s.get("codec_type") == "video"), None)
    audio = next((s for s in datos.get("streams", []) if s.get("codec_type") == "audio"), None)

    fps = None
    if video and video.get("r_frame_rate", "0/0") != "0/0":
        try:
            num, _, den = video["r_frame_rate"].partition("/")
            fps = round(int(num) / int(den), 2)
        except (ValueError, ZeroDivisionError):
            fps = None

    return {
        "duracion": float(datos.get("format", {}).get("duration") or 0),
        "tamano_mb": round(int(datos.get("format", {}).get("size") or 0) / (1024 * 1024), 1),
        "ancho": video.get("width") if video else None,
        "alto": video.get("height") if video else None,
        "fps": fps,
        "codec_video": video.get("codec_name") if video else None,
        "tiene_audio": audio is not None,
    }


def _una_pasada(ruta):
    """Descodifica el video una vez y saca de ahí las cuatro medidas.

    silencedetect va ANTES de loudnorm a propósito: loudnorm en una sola
    pasada normaliza el audio que deja pasar, así que detrás de él los
    silencios ya no serían los del archivo, sino los de una versión
    corregida que nadie va a subir.
    """
    cmd = [
        "ffmpeg", "-hide_banner", "-nostats", "-i", ruta,
        "-af", f"silencedetect=n={SILENCIO_DB}dB:d=1.0,loudnorm=print_format=json",
        "-vf", f"blackdetect=d={NEGRO_MINIMO},freezedetect=n=-60dB:d={CONGELADO_MINIMO}",
        "-f", "null", "-",
    ]
    res = subprocess.run(cmd, capture_output=True, text=True,
                         encoding="utf-8", errors="replace", timeout=TIEMPO_MAXIMO)
    salida = res.stderr or ""

    medidas = {"lufs": None, "pico": None, "rango": None,
               "silencios": [], "negros": [], "congelados": []}

    # loudnorm imprime un bloque json al final. Se busca el último "{...}"
    # porque el resto de la salida también trae llaves sueltas.
    bloques = re.findall(r"\{[^{}]*\"input_i\"[^{}]*\}", salida, re.DOTALL)
    if bloques:
        try:
            ln = json.loads(bloques[-1])
            medidas["lufs"] = float(ln["input_i"])
            medidas["pico"] = float(ln["input_tp"])
            medidas["rango"] = float(ln["input_lra"])
        except (ValueError, KeyError):
            pass

    # Los silencios llegan en dos líneas (empieza / termina). El último puede
    # quedarse sin cerrar si el video acaba en silencio, y ese caso importa:
    # es el final tranquilo que debe haber después de la última palabra.
    inicio = None
    for m in re.finditer(r"silence_(start|end): (-?[\d.]+)", salida):
        if m.group(1) == "start":
            inicio = float(m.group(2))
        elif inicio is not None:
            medidas["silencios"].append([round(inicio, 2), round(float(m.group(2)), 2)])
            inicio = None
    if inicio is not None:
        medidas["silencios"].append([round(inicio, 2), None])   # sigue hasta el final

    for m in re.finditer(r"black_start:([\d.]+) black_end:([\d.]+)", salida):
        medidas["negros"].append([round(float(m.group(1)), 2), round(float(m.group(2)), 2)])

    for m in re.finditer(r"freezedetect\.freeze_start: ([\d.]+)", salida):
        medidas["congelados"].append(round(float(m.group(1)), 2))

    return medidas


def medir(ruta):
    """Todo lo medible de un archivo. Levanta si el archivo no se puede leer."""
    if not os.path.isfile(ruta):
        raise FileNotFoundError(ruta)
    medidas = _ffprobe(ruta)
    medidas.update(_una_pasada(ruta))
    return medidas


# ---------------------------------------------------------------------------
# Juzgar
# ---------------------------------------------------------------------------

def _hallazgo(clave, nivel, que, arreglo):
    return {"clave": clave, "nivel": nivel, "que": que, "arreglo": arreglo}


def revisar(medidas, registro=None):
    """De los números a la lista de lo que está mal.

    `registro` es la entrada de resultado_lote.json, si la hay: de ahí salen
    el texto narrado y el formato, que permiten dos comprobaciones más. Sin
    él se revisa igual, solo que con menos cosas.
    """
    hallazgos = []
    reg = registro or {}
    dur = medidas.get("duracion") or 0

    if not medidas.get("tiene_audio"):
        hallazgos.append(_hallazgo(
            "sin_audio", "fallo",
            "El archivo no tiene pista de audio.",
            "Vuelve a renderizarlo: el TTS no llegó a pegarse al video."))

    # --- la narración entera, o solo un trozo -------------------------------
    palabras = len(f"{reg.get('titulo', '')} {reg.get('cuerpo', '')}".split())
    if palabras >= 40 and dur > 0:
        esperado = palabras / PALABRAS_POR_SEGUNDO
        if dur < esperado * FRACCION_MINIMA:
            faltan = int(esperado - dur)
            hallazgos.append(_hallazgo(
                "narracion_corta", "fallo",
                f"Dura {dur:.0f}s y el guion tiene {palabras} palabras, que son "
                f"unos {esperado:.0f}s: faltan como {faltan}s de narración.",
                "El TTS entregó menos texto del que se le dio. Rehazlo con "
                f"python generar_video_maestro.py --historias {reg.get('numero', '?')}"))

    # --- volumen ------------------------------------------------------------
    lufs = medidas.get("lufs")
    if lufs is not None:
        if lufs <= LUFS_MUY_FLOJO:
            hallazgos.append(_hallazgo(
                "volumen", "fallo",
                f"Suena a {lufs:.1f} LUFS, {abs(lufs - LUFS_OBJETIVO):.0f} dB por debajo "
                f"de lo normal ({LUFS_OBJETIVO:.0f}). Con ruido alrededor no se oye.",
                "Sube volumen_narracion en config.json y vuelve a renderizar."))
        elif lufs <= LUFS_FLOJO:
            hallazgos.append(_hallazgo(
                "volumen", "aviso",
                f"Suena a {lufs:.1f} LUFS; lo normal son {LUFS_OBJETIVO:.0f}. "
                f"Se oye más flojo que el video de al lado.",
                "Sube volumen_narracion en config.json."))
        elif lufs >= LUFS_PASADO:
            hallazgos.append(_hallazgo(
                "volumen", "aviso",
                f"Suena a {lufs:.1f} LUFS, por encima de los {LUFS_OBJETIVO:.0f} que "
                f"dejan las plataformas: te lo van a bajar ellas.",
                "Baja volumen_narracion; así lo bajas tú y conservas el contraste."))

    pico = medidas.get("pico")
    if pico is not None and pico > PICO_MAXIMO:
        hallazgos.append(_hallazgo(
            "pico", "aviso",
            f"El pico llega a {pico:.1f} dBFS. Al recodificar, la plataforma lo satura.",
            "Deja al menos medio decibelio de aire: baja un poco la narración."))

    # --- silencios ----------------------------------------------------------
    for desde, hasta in medidas.get("silencios", []):
        if desde < 0.05 and (hasta or dur) >= SILENCIO_ARRANQUE:
            hallazgos.append(_hallazgo(
                "silencio_inicio", "fallo",
                f"Empieza con {(hasta or dur):.1f}s sin sonido. Ahí es donde se "
                f"decide si alguien se queda.",
                "Recorta el arranque o empieza el audio antes."))
            continue
        # El silencio final no se cuenta: después de la última palabra tiene
        # que haber uno, y el pie de música se va bajando hasta cero.
        if hasta is None:
            continue
        if hasta - desde >= SILENCIO_LARGO:
            hallazgos.append(_hallazgo(
                "silencio_largo", "aviso",
                f"{hasta - desde:.1f}s sin nada en el segundo {desde:.0f}.",
                "Mira el guion en ese punto: suele ser un párrafo que el TTS saltó."))

    # --- imagen -------------------------------------------------------------
    for desde, hasta in medidas.get("negros", []):
        if desde < 0.05:
            hallazgos.append(_hallazgo(
                "negro_inicio", "fallo",
                f"Arranca con {hasta:.1f}s en negro. Ese es además el fotograma "
                f"que YouTube ofrece como miniatura.",
                "Vuelve a renderizarlo; si se repite, el fondo empieza en negro."))
        elif hasta - desde >= 0.5:
            hallazgos.append(_hallazgo(
                "negro", "aviso",
                f"{hasta - desde:.1f}s en negro en el segundo {desde:.0f}.",
                "Suele ser un corte del video de fondo."))

    for desde in medidas.get("congelados", []):
        if desde >= CONGELADO_PERDONADO_HASTA:
            hallazgos.append(_hallazgo(
                "congelado", "aviso",
                f"La imagen se queda parada a partir del segundo {desde:.0f}.",
                "El fondo se acabó antes que la narración y se quedó en el último cuadro."))

    # --- formato ------------------------------------------------------------
    ancho, alto = medidas.get("ancho"), medidas.get("alto")
    if ancho and alto and "es_short" in reg:
        quiere = (1080, 1920) if reg["es_short"] else (1920, 1080)
        if (ancho, alto) != quiere:
            hallazgos.append(_hallazgo(
                "formato", "fallo",
                f"Mide {ancho}x{alto} y debería medir {quiere[0]}x{quiere[1]}.",
                "Se subiría con bandas negras a los lados."))

    return hallazgos


# ---------------------------------------------------------------------------
# Registro y CLI
# ---------------------------------------------------------------------------

def clave(ruta):
    """El nombre del archivo, como en la metadata: la carpeta de salida puede
    cambiar y la revisión sigue siendo la del mismo video."""
    return os.path.basename(ruta)


def guardadas():
    return almacen.leer(RUTA_CALIDAD, {})


def revisar_archivo(ruta, registro=None):
    """Mide y juzga un archivo. Devuelve la entrada lista para guardar."""
    medidas = medir(ruta)
    hallazgos = revisar(medidas, registro)
    return {
        "cuando": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "numero": (registro or {}).get("numero"),
        "titulo": (registro or {}).get("titulo") or clave(ruta),
        "medidas": medidas,
        "hallazgos": hallazgos,
        "fallos": sum(1 for h in hallazgos if h["nivel"] == "fallo"),
        "avisos": sum(1 for h in hallazgos if h["nivel"] == "aviso"),
    }


def revisar_pendientes(todos=False, solo=None, al_terminar=None):
    """Revisa lo que aún no se ha revisado y lo deja guardado.

    Es lo que usan tanto la línea de comandos como el pipeline, para que los
    dos midan exactamente lo mismo. Cada video se guarda en cuanto termina:
    medir es lento y en un teléfono la corrida se puede cortar a la mitad.

    Devuelve la lista de entradas nuevas; vacía si no había nada que hacer,
    que no es un error.
    """
    videos = videos_renderizados()
    if solo is not None:
        videos = [v for v in videos if v.get("numero") == solo]

    hechas = guardadas()
    pendientes = [v for v in videos if todos or clave(v["ruta"]) not in hechas]

    nuevas = []
    for i, v in enumerate(pendientes, 1):
        logger.info(f"[{i}/{len(pendientes)}] {clave(v['ruta'])}")
        try:
            entrada = revisar_archivo(v["ruta"], v)
        except Exception as exc:
            logger.warning(f"  No se pudo revisar ({exc}).")
            continue
        hechas[clave(v["ruta"])] = entrada
        almacen.guardar(RUTA_CALIDAD, hechas)
        nuevas.append(entrada)
        if al_terminar:
            al_terminar(entrada)
    return nuevas


def videos_renderizados():
    """Los del registro cuyo archivo sigue en disco."""
    import generar_video_maestro as gvm
    cfg = gvm.cargar_config()
    lote = almacen.leer(os.path.join(cfg["carpeta_salida"], "resultado_lote.json"), {})
    return [v for v in lote.get("completados", []) if os.path.exists(v.get("ruta", ""))]


def _pintar(entrada):
    marca = "❌" if entrada["fallos"] else ("⚠️ " if entrada["avisos"] else "✅")
    m = entrada["medidas"]
    print(f"\n{marca} {entrada['titulo'][:58]}")
    print(f"   {m['duracion']:.0f}s · {m['ancho']}x{m['alto']} · {m['tamano_mb']} MB"
          + (f" · {m['lufs']:.1f} LUFS" if m.get("lufs") is not None else ""))
    for h in entrada["hallazgos"]:
        print(f"   {'❌' if h['nivel'] == 'fallo' else '⚠️ '} {h['que']}")
        print(f"      → {h['arreglo']}")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Revisa los videos renderizados.")
    parser.add_argument("--todos", action="store_true", help="Revisar también los ya revisados.")
    parser.add_argument("--solo", type=int, metavar="N", help="Solo esa historia.")
    parser.add_argument("--archivo", metavar="MP4", help="Un archivo suelto, sin registro.")
    args = parser.parse_args(argv)

    if args.archivo:
        _pintar(revisar_archivo(args.archivo))
        return

    if not videos_renderizados():
        # No es un fallo: sale igual que «ya está todo revisado». Con un
        # código de error, la tanda de mantenimiento lo pintaba en rojo.
        print("\n  Todavía no hay videos grabados que revisar.\n")
        return

    nuevas = revisar_pendientes(todos=args.todos, solo=args.solo, al_terminar=_pintar)
    if not nuevas:
        print("\n  Ya está todo revisado. --todos para repetirlo.\n")
        return

    con_fallo = sum(1 for e in nuevas if e["fallos"])
    print(f"\n  {len(nuevas)} revisado(s), {con_fallo} con algo que arreglar.\n")


if __name__ == "__main__":
    main()
