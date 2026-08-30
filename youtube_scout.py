"""
YouTube Scout — busca historias y confesiones virales en YouTube y las deja en
la misma cola que trend_scout.py (pipeline_state/candidatos.json).

Salida: candidatos con "fuente": "youtube" y "tipo": "transcripcion".
script_writer.py los reconoce, parte la transcripción en las anécdotas que
contiene y reescribe cada una como historia independiente.

Requiere: pip install requests youtube-transcript-api

Cómo obtiene el material (sin API key de Google):
  - Descubrimiento: el feed RSS público de cada canal
    (youtube.com/feeds/videos.xml?channel_id=UC...), que trae los últimos ~15
    videos con título, fecha y número de vistas. Las vistas son el filtro de
    "viral": no hay que adivinar, YouTube las publica en el propio feed.
  - Contenido: los subtítulos públicos del video, vía youtube-transcript-api.

Sobre el uso del material ajeno:
  Un post de Reddit lo escribe el propio usuario; un episodio de podcast es la
  grabación editada de un creador, así que aquí se es MÁS estricto, no menos:
    - Nunca se descarga, corta ni reusa el audio ni el video originales. Solo
      el texto de los subtítulos, y solo como materia prima.
    - La historia se reescribe entera, en primera persona y con otras palabras
      (eso lo hace script_writer.py); no se lee la transcripción tal cual.
    - Cada candidato conserva canal y URL, que terminan en "# Fuente:"/"# Autor:"
      del guion y de ahí en la descripción del video, para dar crédito en vez
      de presentar la anécdota como propia.
  Los canales por defecto son de anécdotas mandadas por la audiencia, que es el
  material con la procedencia más clara. Si agregas canales, conviene mantener
  ese criterio.
"""
import os
import sys
import re
import json
import time
import logging
import argparse
from xml.etree import ElementTree as ET

import cola  # cola de candidatos e historial compartidos con script_writer.py

try:
    import requests
except ImportError:
    requests = None

try:
    from youtube_transcript_api import YouTubeTranscriptApi
except ImportError:
    YouTubeTranscriptApi = None

RUTA_CONFIG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config_trends.json")
RUTA_CANALES = os.path.join(cola.CARPETA_ESTADO, "canales_youtube.json")  # caché de @handle → channel_id

RATE_LIMIT_SEG = 3.0  # pausa entre canales; el RSS de YouTube es tolerante, pero no hay por qué abusar

CONFIG_DEFAULT = {
    # Canales de anécdotas/confesiones mandadas por la audiencia. Se aceptan
    # @handles, URLs completas o channel_id (UC...). Los handles se resuelven
    # una vez y se guardan en pipeline_state/canales_youtube.json.
    "youtube_canales": [
        "@LaCotorrisa",
        "@RelatosdelaNoche",
        "@Sobrenatural",
        "@RelatosdeHorror",
    ],
    # Un video entra como candidato solo si pasa este número de vistas. Es el
    # filtro de viralidad: el RSS de YouTube publica las vistas directamente.
    "youtube_min_vistas": 50000,
    "youtube_max_videos_por_canal": 5,
    # Transcripciones más cortas que esto casi nunca traen una anécdota
    # completa (son shorts, avances o intros).
    "youtube_min_palabras": 300,
    # Tope de seguridad: un episodio de tres horas son ~30.000 palabras y no
    # tiene sentido mandárselas enteras a Gemini. Se recorta al principio, que
    # es donde estos programas ponen las anécdotas.
    "youtube_max_palabras": 12000,
    "youtube_idiomas": ["es", "es-MX", "es-419", "es-ES"],
    # Palabras que descartan un video por el título: en vivo, reacciones,
    # entrevistas y demás formatos que no traen anécdotas narrables.
    "youtube_titulos_excluidos": [
        "en vivo", "live", "trailer", "tráiler", "reacciona", "reaccion",
        "reacción", "unboxing", "podcast completo", "resumen semanal",
    ],
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("youtube_scout")

_UA_NAVEGADOR = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_NS = {
    "a": "http://www.w3.org/2005/Atom",
    "yt": "http://www.youtube.com/xml/schemas/2015",
    "media": "http://search.yahoo.com/mrss/",
}


def cargar_config():
    cfg = dict(CONFIG_DEFAULT)
    if os.path.exists(RUTA_CONFIG):
        with open(RUTA_CONFIG, "r", encoding="utf-8") as f:
            cfg.update(json.load(f))
    return cfg


def _cache_canales():
    if os.path.exists(RUTA_CANALES):
        try:
            with open(RUTA_CANALES, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _guardar_cache_canales(datos):
    os.makedirs(cola.CARPETA_ESTADO, exist_ok=True)
    tmp = RUTA_CANALES + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(datos, f, ensure_ascii=False, indent=2)
    os.replace(tmp, RUTA_CANALES)


def resolver_channel_id(referencia, cache=None):
    """Convierte @handle o URL de canal en un channel_id (UC...).

    El feed RSS solo acepta channel_id, y el handle no se puede derivar sin
    pedirle la página al canal. Se resuelve una vez y se cachea, porque un
    handle no cambia y no vale la pena una petición extra por corrida.
    """
    referencia = (referencia or "").strip()
    if not referencia:
        return None
    if re.fullmatch(r"UC[A-Za-z0-9_-]{22}", referencia):
        return referencia

    cache = _cache_canales() if cache is None else cache
    if referencia in cache:
        return cache[referencia]

    if referencia.startswith("http"):
        url = referencia
    elif referencia.startswith("@"):
        url = f"https://www.youtube.com/{referencia}"
    else:
        url = f"https://www.youtube.com/@{referencia}"

    resp = requests.get(url, headers={"User-Agent": _UA_NAVEGADOR}, timeout=15)
    resp.raise_for_status()
    m = (re.search(r'"channelId":"(UC[A-Za-z0-9_-]{22})"', resp.text)
         or re.search(r'channel/(UC[A-Za-z0-9_-]{22})', resp.text))
    if not m:
        raise RuntimeError(f"No se pudo averiguar el channel_id de {referencia}")

    cache[referencia] = m.group(1)
    _guardar_cache_canales(cache)
    return m.group(1)


def videos_del_canal(channel_id):
    """Últimos videos del canal, desde su feed RSS público."""
    url = "https://www.youtube.com/feeds/videos.xml"
    resp = requests.get(url, params={"channel_id": channel_id},
                        headers={"User-Agent": _UA_NAVEGADOR}, timeout=15)
    if resp.status_code == 404:
        raise RuntimeError(f"El canal {channel_id} no tiene feed público.")
    resp.raise_for_status()

    root = ET.fromstring(resp.text)
    canal = root.findtext("a:title", default="", namespaces=_NS) or channel_id
    videos = []
    for entry in root.findall("a:entry", _NS):
        vid = entry.findtext("yt:videoId", default="", namespaces=_NS) or ""
        if not vid:
            continue
        grupo = entry.find("media:group", _NS)
        stats = grupo.find("media:community/media:statistics", _NS) if grupo is not None else None
        try:
            vistas = int(stats.get("views")) if stats is not None and stats.get("views") else 0
        except ValueError:
            vistas = 0
        videos.append({
            "id": vid,
            "titulo": entry.findtext("a:title", default="", namespaces=_NS) or "",
            "canal": canal,
            "publicado": entry.findtext("a:published", default="", namespaces=_NS) or "",
            "vistas": vistas,
            "url": f"https://www.youtube.com/watch?v={vid}",
        })
    return canal, videos


def obtener_transcripcion(video_id, idiomas):
    """Texto de los subtítulos públicos del video, en el primer idioma que haya.

    Se soportan las dos APIs de youtube-transcript-api porque la 1.x cambió a
    métodos de instancia y en Termux puede quedar instalada cualquiera de las
    dos: fallar por la versión de una dependencia sería un error tonto y difícil
    de leer desde el teléfono.
    """
    if YouTubeTranscriptApi is None:
        raise RuntimeError("Falta 'youtube-transcript-api'. Instálalo con: pip install youtube-transcript-api")

    if hasattr(YouTubeTranscriptApi, "get_transcript"):  # 0.6.x
        trozos = YouTubeTranscriptApi.get_transcript(video_id, languages=idiomas)
        textos = [t.get("text", "") for t in trozos]
    else:  # 1.x
        fetched = YouTubeTranscriptApi().fetch(video_id, languages=idiomas)
        textos = [getattr(t, "text", "") for t in fetched]

    # Los subtítulos automáticos vienen troceados por tiempo, con marcas como
    # [Música] o [Aplausos] que no son parte de la historia.
    texto = " ".join(t.strip() for t in textos if t and t.strip())
    texto = re.sub(r"\[[^\]]{0,30}\]", " ", texto)
    return re.sub(r"\s+", " ", texto).strip()


def _titulo_excluido(titulo, cfg):
    t = titulo.lower()
    return any(mal in t for mal in cfg["youtube_titulos_excluidos"])


def escanear(cfg, contar=None):
    """Candidatos NUEVOS de YouTube. No toca el historial: igual que el scout de
    Reddit, un video se marca como visto cuando script_writer consigue sacarle
    guion, no cuando se ve."""
    if requests is None:
        raise RuntimeError("Falta el paquete 'requests'. Instálalo con: pip install requests")

    if contar is None:
        contar = {}
    for k in ("canales_ok", "canales_fallidos", "videos", "ya_vistos", "ya_en_cola",
              "titulo_excluido", "pocas_vistas", "sin_subtitulos", "muy_corto", "nuevos"):
        contar.setdefault(k, 0)

    vistos = cola.cargar_historial()
    en_cola = cola.ids_en_cola()
    cache = _cache_canales()
    candidatos = []

    for i, referencia in enumerate(cfg["youtube_canales"]):
        if i > 0:
            time.sleep(RATE_LIMIT_SEG)

        logger.info(f"Escaneando {referencia}...")
        try:
            channel_id = resolver_channel_id(referencia, cache)
            nombre_canal, videos = videos_del_canal(channel_id)
        except Exception as exc:
            logger.warning(f"No se pudo leer {referencia}: {exc}")
            contar["canales_fallidos"] += 1
            continue
        contar["canales_ok"] += 1

        # Del feed (que viene por fecha) nos quedamos con los más vistos.
        videos.sort(key=lambda v: v["vistas"], reverse=True)
        tomados = 0

        for video in videos:
            if tomados >= cfg["youtube_max_videos_por_canal"]:
                break
            contar["videos"] += 1
            cand_id = f"yt_{video['id']}"

            if cand_id in vistos:
                contar["ya_vistos"] += 1
                continue
            if cand_id in en_cola:
                contar["ya_en_cola"] += 1
                continue
            if _titulo_excluido(video["titulo"], cfg):
                contar["titulo_excluido"] += 1
                continue
            if video["vistas"] < cfg["youtube_min_vistas"]:
                contar["pocas_vistas"] += 1
                continue

            try:
                texto = obtener_transcripcion(video["id"], cfg["youtube_idiomas"])
            except Exception as exc:
                logger.warning(f"Sin subtítulos usables en {video['id']}: {type(exc).__name__}")
                contar["sin_subtitulos"] += 1
                continue

            palabras = texto.split()
            if len(palabras) < cfg["youtube_min_palabras"]:
                contar["muy_corto"] += 1
                continue
            if len(palabras) > cfg["youtube_max_palabras"]:
                texto = " ".join(palabras[:cfg["youtube_max_palabras"]])

            candidatos.append({
                "id": cand_id,
                "fuente": "youtube",
                # script_writer trata distinto una transcripción que un post:
                # la parte en las anécdotas que contiene antes de reescribir.
                "tipo": "transcripcion",
                "subreddit": nombre_canal,  # el resto del pipeline lee este campo como "de dónde salió"
                "canal": nombre_canal,
                "titulo_original": video["titulo"],
                "texto_original": texto,
                "vistas": video["vistas"],
                "rank_en_subreddit": tomados,
                "url": video["url"],
                "autor": nombre_canal,
                "intentos": 0,
            })
            en_cola.add(cand_id)
            contar["nuevos"] += 1
            tomados += 1

    return candidatos


def explicar(cfg, contar, pendientes_antes, agregados, total_cola):
    """Informe legible de por qué salieron (o no salieron) videos nuevos."""
    # Se lee con .get porque este informe existe justo para cuando algo salió
    # mal: si el escaneo abortó antes de llenar los contadores, un KeyError
    # aquí taparía el error de verdad con uno mío.
    print("")
    print("─── Diagnóstico de la búsqueda en YouTube ───")
    print(f" Canales leídos ok    : {contar.get('canales_ok', 0)} de {len(cfg['youtube_canales'])}"
          + (f"  ({contar.get('canales_fallidos', 0)} fallaron)" if contar.get('canales_fallidos', 0) else ""))
    print(f" Videos revisados     : {contar.get('videos', 0)}")
    print(f"   • ya usados antes  : {contar.get('ya_vistos', 0)}")
    print(f"   • ya en la cola    : {contar.get('ya_en_cola', 0)}")
    print(f"   • título excluido  : {contar.get('titulo_excluido', 0)}")
    print(f"   • pocas vistas (<{cfg['youtube_min_vistas']:,}) : {contar.get('pocas_vistas', 0)}")
    print(f"   • sin subtítulos   : {contar.get('sin_subtitulos', 0)}")
    print(f"   • muy cortos (<{cfg['youtube_min_palabras']} palabras) : {contar.get('muy_corto', 0)}")
    print(f"   • NUEVOS           : {contar.get('nuevos', 0)}")
    print("")
    print(f" Cola antes           : {pendientes_antes}")
    print(f" Agregados ahora      : {agregados}")
    print(f" Cola ahora           : {total_cola}")
    print("")

    if contar.get('canales_ok', 0) == 0:
        print(" ⛔ Ningún canal respondió. Revisa la conexión, o que los @handles de")
        print("    \"youtube_canales\" en config_trends.json estén bien escritos.")
    elif contar.get('nuevos', 0) == 0 and contar.get('sin_subtitulos', 0) >= max(1, contar.get('videos', 0) // 3):
        print(" ℹ️  Muchos videos sin subtítulos públicos. Sin subtítulos no hay texto")
        print("    que reescribir; busca canales que sí los tengan activados.")
    elif contar.get('nuevos', 0) == 0 and contar.get('pocas_vistas', 0):
        print(" ℹ️  Los videos recientes no llegan al mínimo de vistas. Baja")
        print("    \"youtube_min_vistas\" en config_trends.json si quieres más material.")
    elif contar.get('nuevos', 0) == 0:
        print(" ℹ️  No hay videos nuevos que cumplan los filtros. Agrega más canales")
        print("    en \"youtube_canales\" o espera a que publiquen.")


def main(argv=None):
    # argv explícito, y sin caer a sys.argv por omisión: pipeline.py llama a
    # main() sin argumentos, y argparse se comería los flags del pipeline
    # (--hasta, --forzar…) abortando la etapa con un error de uso absurdo.
    ap = argparse.ArgumentParser(description="Busca historias virales en YouTube y las deja en la cola.")
    ap.add_argument("--diagnostico", action="store_true",
                    help="Escanea y explica por qué salieron (o no) historias nuevas.")
    ap.add_argument("--canal", action="append", metavar="@CANAL",
                    help="Escanear solo este canal (se puede repetir). Ignora la lista de config.")
    args = ap.parse_args(argv or [])

    cfg = cargar_config()
    if args.canal:
        cfg["youtube_canales"] = args.canal

    pendientes_antes = len(cola.cargar_pendientes())
    contar = {}
    nuevos = escanear(cfg, contar)
    total_cola, agregados = cola.agregar_candidatos(nuevos)

    logger.info(f"{agregados} video(s) nuevo(s); {total_cola} en cola en {cola.RUTA_CANDIDATOS}")
    for c in nuevos:
        print(f" • [{c['canal']}] {c['titulo_original']} ({c['vistas']:,} vistas)")

    if args.diagnostico or agregados == 0:
        explicar(cfg, contar, pendientes_antes, agregados, total_cola)


if __name__ == "__main__":
    main(sys.argv[1:])
