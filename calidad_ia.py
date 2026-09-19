"""
Calidad (IA) — le enseña un video a Gemini y le pregunta qué falla.

Esto es la segunda capa. La primera, calidad.py, mide lo que ffmpeg contesta
con un número y por eso se pasa a todos los videos: es gratis y no se discute.
Aquí ya no hay números, hay una opinión, cuesta cuota y datos, y por eso va
sobre una muestra: por omisión, un video por corrida.

El límite, dicho antes de empezar: que a Gemini le guste un video no predice
que el feed lo reparta. Eso no lo sabe nadie mirando el archivo. Lo que sí
puede ver es lo que un humano vería en tres segundos y tú ya no ves de tanto
mirarlo — que el primer plano no dice de qué va, que el subtítulo se lee mal
sobre ese fondo, que el título promete algo que la historia no cumple.

Dos formas de enseñárselo:

  · Por fotogramas (lo normal). Siete imágenes, cargadas a los segundos que
    importan —el arranque primero— más el guion y las medidas de calidad.py.
    Unos pocos miles de tokens y sube menos de un megabyte.

  · El video entero (--video-entero). Ve ritmo y audio, que en fotogramas no
    se ven. Pero un video de 60 s son unos 16.000 tokens de entrada y hay que
    subirlo: 15-30 MB. Solo con wifi y de vez en cuando.

Uso:
  python calidad_ia.py                  # el más reciente sin analizar
  python calidad_ia.py --cuantos 3      # los tres más recientes
  python calidad_ia.py --solo 4         # esa historia
  python calidad_ia.py --todos          # todos los que falten
  python calidad_ia.py --video-entero   # subiendo el mp4, no fotogramas
  python calidad_ia.py --con-datos      # sin esperar al wifi

Requiere GEMINI_API_KEY (ver secretos.py).
"""
import os
import json
import shutil
import logging
import argparse
import tempfile
import subprocess
from datetime import datetime, timezone

import almacen    # leer y escribir los .json de estado
import calidad    # las medidas objetivas, que van dentro del prompt
import secretos   # carga secretos.env si las claves no están en el entorno
import ruido      # calla los avisos del SDK de Google que aqui no dicen nada

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
ruido.callar_sdk_google()
logger = logging.getLogger("calidad_ia")

MODEL = "gemini-3.6-flash"

# Los segundos de los que se saca fotograma. No están repartidos por igual a
# propósito: cuatro de los siete caen en los primeros tres segundos, que es
# donde se decide si alguien se queda. El resto son porcentajes de la
# duración, para que sirvan igual en un short de 40 s que en uno de 90.
INSTANTES_FIJOS = [0.3, 1.0, 2.0, 3.0]
INSTANTES_RELATIVOS = [0.35, 0.65, 0.95]

# 540 px de ancho: el texto se lee de sobra para juzgar legibilidad y cada
# imagen baja de los 60 KB. A 1080 se pagarían el doble de tokens por ver lo
# mismo.
ANCHO_FOTOGRAMA = 540

ESPERA_SUBIDA = 120   # segundos máximos esperando a que Gemini procese el mp4


SCHEMA = {
    "type": "object",
    "properties": {
        "gancho": {"type": "integer", "description": "Del 1 al 5: ¿los tres primeros segundos hacen que alguien se quede? 1 = no dan ninguna razón para seguir."},
        "gancho_por_que": {"type": "string", "description": "Una frase. Qué se ve en esos segundos y por qué funciona o no."},
        "legibilidad": {"type": "integer", "description": "Del 1 al 5: ¿se leen los subtítulos sobre ese fondo, en un teléfono, de un vistazo?"},
        "legibilidad_por_que": {"type": "string", "description": "Una frase. Si baja de 4, di exactamente qué estorba."},
        "titulo_cumple": {"type": "boolean", "description": "¿La historia cumple lo que el título promete?"},
        "titulo_por_que": {"type": "string", "description": "Una frase."},
        "defectos": {"type": "array", "items": {"type": "string"}, "description": "Cosas concretas que se ven mal en las imágenes: texto cortado, fondo tapado, cuadro en negro, subtítulo fuera de sitio. Vacío si no ves ninguna. No inventes."},
        "lo_peor": {"type": "string", "description": "Si solo se pudiera cambiar UNA cosa del video, cuál. Concreta y accionable."},
    },
    "required": ["gancho", "gancho_por_que", "legibilidad", "legibilidad_por_que",
                 "titulo_cumple", "titulo_por_que", "defectos", "lo_peor"],
}

SYSTEM = """Juzgas videos verticales de historias narradas en español (YouTube Shorts y
TikTok): fondo de gameplay, narración en off y subtítulos grandes palabra a palabra.

Miras con los ojos de alguien que pasa el dedo por el feed, no con los de un crítico de
cine. Lo único que importa es si se quedaría, si puede leer, y si lo que le prometieron
es lo que recibe.

Reglas:
- Sé concreto. «El gancho es flojo» no sirve; «el primer plano es gameplay sin texto,
  no se sabe de qué va hasta el segundo 4» sí.
- No inventes defectos para tener algo que decir. Si las imágenes se ven bien, dilo.
- No juzgues la historia por su tema: drama, venganza y conflicto familiar son el
  género del canal, no un defecto.
- No opines sobre si va a tener vistas. Eso no se sabe mirando el archivo."""


# ---------------------------------------------------------------------------
# Enseñarle el video
# ---------------------------------------------------------------------------

def instantes_de(duracion):
    """Los segundos de los que sacar fotograma, sin salirse del video."""
    puntos = [t for t in INSTANTES_FIJOS if t < duracion]
    puntos += [round(duracion * f, 1) for f in INSTANTES_RELATIVOS if duracion * f > 3.2]
    # Ordenados y sin repetidos: en un video muy corto los fijos y los
    # relativos caen encima.
    return sorted(set(puntos))


def sacar_fotogramas(ruta, duracion, carpeta):
    """Un jpg por instante. Devuelve [(segundo, ruta_jpg)].

    -ss va ANTES de -i: así ffmpeg salta directamente por los índices del
    archivo en vez de descodificar desde el principio hasta ese punto. Con
    siete instantes la diferencia en un teléfono es de minutos a segundos.
    """
    sacados = []
    for i, t in enumerate(instantes_de(duracion), 1):
        destino = os.path.join(carpeta, f"f{i:02d}.jpg")
        res = subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
             "-ss", str(t), "-i", ruta, "-frames:v", "1",
             "-vf", f"scale={ANCHO_FOTOGRAMA}:-2", "-q:v", "4", destino],
            capture_output=True, text=True, timeout=60,
        )
        if res.returncode == 0 and os.path.exists(destino) and os.path.getsize(destino) > 0:
            sacados.append((t, destino))
    return sacados


def _prompt(registro, medidas):
    """El texto que acompaña a las imágenes.

    Las medidas objetivas van dentro a propósito: si el audio ya sabemos que
    está bajo, que no lo cuente como hallazgo suyo, y si el video arranca en
    negro que sepa por qué el primer fotograma está oscuro en vez de
    achacárselo al diseño.
    """
    datos = []
    if medidas.get("duracion"):
        datos.append(f"dura {medidas['duracion']:.0f} s")
    if medidas.get("lufs") is not None:
        datos.append(f"volumen {medidas['lufs']:.1f} LUFS")
    medidos = "; ".join(datos) or "sin medir"

    ya_sabidos = [h["que"] for h in (registro.get("hallazgos") or [])]
    return (
        f"TÍTULO: {registro.get('titulo', '')}\n\n"
        f"NARRACIÓN (es lo que se oye, no está en pantalla):\n"
        f"{(registro.get('cuerpo') or '')[:2500]}\n\n"
        f"MEDIDO CON FFMPEG: {medidos}\n"
        + ("DEFECTOS YA DETECTADOS (no hace falta que los repitas):\n  - "
           + "\n  - ".join(ya_sabidos) + "\n" if ya_sabidos else "")
        + "\nLas imágenes son fotogramas del video, en orden, con su segundo."
    )


def analizar_por_fotogramas(client, ruta, registro, medidas):
    from google.genai import types

    carpeta = tempfile.mkdtemp(prefix="calidad_ia_")
    try:
        fotos = sacar_fotogramas(ruta, medidas.get("duracion") or 0, carpeta)
        if not fotos:
            raise RuntimeError("no se pudo sacar ningún fotograma")

        contenido = [_prompt(registro, medidas)]
        for t, jpg in fotos:
            contenido.append(f"segundo {t}:")
            with open(jpg, "rb") as f:
                contenido.append(types.Part.from_bytes(data=f.read(), mime_type="image/jpeg"))

        respuesta = client.models.generate_content(
            model=MODEL, contents=contenido,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM,
                response_mime_type="application/json",
                response_schema=SCHEMA,
            ),
        )
        veredicto = json.loads(respuesta.text)
        veredicto["como"] = f"{len(fotos)} fotogramas"
        return veredicto
    finally:
        shutil.rmtree(carpeta, ignore_errors=True)


def analizar_video_entero(client, ruta, registro, medidas):
    """Sube el mp4 y se lo da tal cual. Ve el ritmo y oye el audio."""
    import time
    from google.genai import types

    logger.info(f"  Subiendo {medidas.get('tamano_mb')} MB a Gemini…")
    archivo = client.files.upload(file=ruta)

    # Un video recién subido tarda en quedar utilizable; usarlo antes falla
    # con un error que no dice eso.
    esperado = 0
    while getattr(archivo.state, "name", str(archivo.state)) == "PROCESSING":
        if esperado >= ESPERA_SUBIDA:
            raise RuntimeError(f"Gemini sigue procesando el video tras {ESPERA_SUBIDA}s")
        time.sleep(4)
        esperado += 4
        archivo = client.files.get(name=archivo.name)

    if getattr(archivo.state, "name", str(archivo.state)) == "FAILED":
        raise RuntimeError("Gemini no pudo procesar el video")

    try:
        respuesta = client.models.generate_content(
            model=MODEL, contents=[_prompt(registro, medidas), archivo],
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM,
                response_mime_type="application/json",
                response_schema=SCHEMA,
            ),
        )
        veredicto = json.loads(respuesta.text)
        veredicto["como"] = "video entero"
        return veredicto
    finally:
        # El archivo subido ocupa cuota aunque ya no se use.
        try:
            client.files.delete(name=archivo.name)
        except Exception as exc:
            logger.warning(f"  No se pudo borrar el video subido a Gemini ({exc}).")


# ---------------------------------------------------------------------------
# Elegir a quién le toca, y guardarlo
# ---------------------------------------------------------------------------

def pendientes_de_ia(videos, revisiones, solo=None):
    """Los que aún no tienen opinión, del más reciente al más antiguo.

    Del más reciente porque es el que todavía puedes rehacer sin que se note:
    una opinión sobre un video de hace tres semanas, ya publicado y ya visto,
    no cambia nada. Cuántos de esos se analizan lo decide quien llama.
    """
    if solo is not None:
        return [v for v in videos if v.get("numero") == solo]
    falta = [v for v in videos
             if not (revisiones.get(calidad.clave(v["ruta"])) or {}).get("ia")]
    falta.sort(key=lambda v: os.path.getmtime(v["ruta"]), reverse=True)
    return falta


def analizar(client, video, revisiones, video_entero=False):
    """Analiza uno y devuelve la entrada de calidad.json ya actualizada."""
    ruta = video["ruta"]
    entrada = revisiones.get(calidad.clave(ruta))
    if not entrada:
        # Sin medidas no hay contexto que darle al modelo, así que se miden
        # ahora: es la capa de abajo y cuesta una pasada de ffmpeg.
        logger.info("  Sin medir todavía; se mide primero.")
        entrada = calidad.revisar_archivo(ruta, video)

    medidas = entrada.get("medidas", {})
    contexto = dict(video, hallazgos=entrada.get("hallazgos"))

    if video_entero:
        veredicto = analizar_video_entero(client, ruta, contexto, medidas)
    else:
        veredicto = analizar_por_fotogramas(client, ruta, contexto, medidas)

    veredicto["cuando"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    veredicto["modelo"] = MODEL
    entrada["ia"] = veredicto
    return entrada


def _pintar(video, entrada):
    ia = entrada["ia"]
    print(f"\n  {video.get('titulo', '')[:58]}")
    print(f"    gancho {ia['gancho']}/5 · legibilidad {ia['legibilidad']}/5 "
          f"· título {'cumple' if ia['titulo_cumple'] else 'NO cumple'}  ({ia['como']})")
    print(f"    gancho: {ia['gancho_por_que']}")
    print(f"    texto:  {ia['legibilidad_por_que']}")
    if not ia["titulo_cumple"]:
        print(f"    título: {ia['titulo_por_que']}")
    for d in ia["defectos"]:
        print(f"    · {d}")
    print(f"    → lo que cambiaría: {ia['lo_peor']}")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Le pregunta a Gemini qué falla en un video.")
    ap.add_argument("--cuantos", type=int, default=1, metavar="N",
                    help="Cuántos analizar en esta corrida (por omisión 1).")
    ap.add_argument("--solo", type=int, metavar="N", help="Solo esa historia.")
    ap.add_argument("--todos", action="store_true", help="Todos los que falten.")
    ap.add_argument("--video-entero", action="store_true",
                    help="Subir el mp4 en vez de mandar fotogramas.")
    ap.add_argument("--con-datos", action="store_true",
                    help="No esperar al wifi.")
    args = ap.parse_args(argv)

    import publisher
    permitir_datos = (
        args.con_datos
        or os.environ.get("SUBIR_CON_DATOS") == "1"
        or not publisher.cargar_config().get("solo_wifi", True)
    )
    if not permitir_datos and not publisher.conectado_a_wifi():
        raise SystemExit(
            "Sin WiFi — se aplaza para no gastar datos móviles.\n"
            "   Para hacerlo ahora igual: python calidad_ia.py --con-datos")

    videos = calidad.videos_renderizados()
    if not videos:
        raise SystemExit("No hay videos renderizados que analizar.")

    revisiones = calidad.guardadas()
    cola = pendientes_de_ia(videos, revisiones, solo=args.solo)
    if not args.todos and args.solo is None:
        cola = cola[:max(1, args.cuantos)]
    if not cola:
        raise SystemExit("Todos los videos ya tienen la opinión de Gemini. "
                         "Usa --solo N para repetir uno.")

    from google import genai
    try:
        client = genai.Client()
    except Exception as exc:
        raise SystemExit(f"Sin Gemini: {exc}")

    hechos = 0
    for i, v in enumerate(cola, 1):
        logger.info(f"[{i}/{len(cola)}] {calidad.clave(v['ruta'])}")
        try:
            entrada = analizar(client, v, revisiones, video_entero=args.video_entero)
        except Exception as exc:
            logger.warning(f"  No se pudo analizar ({exc}).")
            continue
        revisiones[calidad.clave(v["ruta"])] = entrada
        almacen.guardar(calidad.RUTA_CALIDAD, revisiones)
        hechos += 1
        _pintar(v, entrada)

    print(f"\n  {hechos} analizado(s). Salen en el panel, en Revisar.\n")


if __name__ == "__main__":
    main()
