"""
YouTube Scout — busca historias y confesiones virales en YouTube y las deja en
la misma cola que trend_scout.py (pipeline_state/candidatos.json).

Salida: candidatos con "fuente": "youtube" y "tipo": "transcripcion".
script_writer.py los reconoce, parte la transcripción en las anécdotas que
contiene y reescribe cada una como historia independiente.

Requiere: pip install requests youtube-transcript-api
Opcional pero muy recomendable: YOUTUBE_API_KEY en secretos.env
(gratis en https://console.cloud.google.com — habilitar "YouTube Data API v3").

Cómo encuentra los videos (dos vías, según haya API key o no):

  CON YOUTUBE_API_KEY — la buena:
    search.list con order=viewCount devuelve los videos MÁS VISTOS del canal
    o de una búsqueda, sin importar la antigüedad. Un video de hace tres años
    con dos millones de vistas es justo el material que se quiere, y por aquí
    sí aparece. Además permite buscar por tema en todo YouTube
    ("youtube_busquedas"), no solo en una lista fija de canales.

  SIN API key — el respaldo:
    El feed RSS público del canal. Solo trae los ~15 videos MÁS RECIENTES, y
    esos casi nunca han acumulado vistas todavía: es normal que un escaneo
    entero se descarte por "pocas vistas". Sirve para no depender de nada,
    pero para encontrar virales viejos hace falta la API key.

  El texto sale siempre igual: de los subtítulos públicos del video, vía
  youtube-transcript-api.

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

import cola      # cola de candidatos e historial compartidos con script_writer.py
import secretos  # carga secretos.env si YOUTUBE_API_KEY no está en el entorno

try:
    import requests
except ImportError:
    requests = None

try:
    from youtube_transcript_api import YouTubeTranscriptApi
except ImportError:
    YouTubeTranscriptApi = None

try:
    from googleapiclient.discovery import build as construir_servicio
except ImportError:
    construir_servicio = None

RUTA_CONFIG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config_trends.json")
RUTA_CANALES = os.path.join(cola.CARPETA_ESTADO, "canales_youtube.json")  # caché de @handle → channel_id

RATE_LIMIT_SEG = 3.0  # pausa entre canales; el RSS de YouTube es tolerante, pero no hay por qué abusar

CONFIG_DEFAULT = {
    # Canales de anécdotas/confesiones mandadas por la audiencia. Se aceptan
    # @handles, URLs completas o channel_id (UC...). Los handles se resuelven
    # una vez y se guardan en pipeline_state/canales_youtube.json.
    #
    # Esta lista es deliberadamente corta: un @handle inventado da 404 y hace
    # perder una corrida entera, así que aquí solo va lo comprobado. Con
    # YOUTUBE_API_KEY el peso lo llevan las búsquedas de abajo, que encuentran
    # canales que uno no conocía; sin clave, conviene agregar a mano los
    # canales que sigas (copia el @handle de la URL del canal).
    "youtube_canales": [
        "@LaCotorrisaOficial",
    ],
    # Búsquedas por tema en todo YouTube, ordenadas por vistas. Solo funcionan
    # con YOUTUBE_API_KEY; sin ella se ignoran (el RSS no sabe buscar). Es la
    # vía para encontrar canales que no están en la lista de arriba.
    "youtube_busquedas": [
        "anécdotas reales contadas",
        "confesiones reales historias",
        "historias de venganza reales",
        "me pasó a mí historia real",
    ],
    "youtube_max_por_busqueda": 10,
    # Antigüedad mínima: vacío = sin límite, que es lo que se quiere. Un viral
    # de hace tres años sigue siendo buen material; el filtro de calidad son
    # las vistas, no la fecha.
    "youtube_publicado_desde": "",
    # Un video entra como candidato solo si pasa este número de vistas. Es el
    # filtro de viralidad, y el único: la fecha no descarta nada.
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
    # Varios patrones porque YouTube no sirve el mismo HTML a todos los
    # canales: en unos aparece "channelId", en otros solo "externalId" o el
    # enlace canónico. Con uno solo, canales perfectamente válidos fallaban.
    m = (re.search(r'"channelId":"(UC[A-Za-z0-9_-]{22})"', resp.text)
         or re.search(r'"externalId":"(UC[A-Za-z0-9_-]{22})"', resp.text)
         or re.search(r'channel/(UC[A-Za-z0-9_-]{22})', resp.text))
    if not m:
        raise RuntimeError(f"No se pudo averiguar el channel_id de {referencia}")

    cache[referencia] = m.group(1)
    _guardar_cache_canales(cache)
    return m.group(1)


def hay_api_key():
    return bool(os.environ.get("YOUTUBE_API_KEY", "").strip())


def _servicio_youtube():
    if construir_servicio is None:
        raise RuntimeError("Falta 'google-api-python-client'. Instálalo con: pip install google-api-python-client")
    return construir_servicio("youtube", "v3", developerKey=os.environ["YOUTUBE_API_KEY"].strip(),
                              cache_discovery=False)


def _detalles_de_videos(servicio, ids):
    """Vistas y duración reales de una lista de ids (hasta 50 por llamada).

    search.list devuelve ids y títulos pero NO las vistas, así que hay que
    pedirlas aparte. Sale barato: una llamada cubre 50 videos.
    """
    detalles = {}
    for i in range(0, len(ids), 50):
        resp = servicio.videos().list(
            part="snippet,statistics,contentDetails",
            id=",".join(ids[i:i + 50]),
        ).execute()
        for item in resp.get("items", []):
            detalles[item["id"]] = item
    return detalles


def _buscar(servicio, cfg, **extra):
    """Un search.list ordenado por vistas, con los filtros comunes."""
    params = dict(
        part="id",
        type="video",
        order="viewCount",          # los MÁS VISTOS de siempre, no los más nuevos
        maxResults=min(50, int(extra.pop("maxResults", 10))),
        relevanceLanguage="es",
        regionCode="MX",
        videoCaption="closedCaption",  # sin subtítulos no hay texto que reescribir
    )
    desde = (cfg.get("youtube_publicado_desde") or "").strip()
    if desde:
        params["publishedAfter"] = desde
    params.update(extra)
    resp = servicio.search().list(**params).execute()
    return [it["id"]["videoId"] for it in resp.get("items", []) if it.get("id", {}).get("videoId")]


def _motivo_error_api(exc):
    """Traduce un error de la API a un motivo accionable, o None si no lo es.

    Los errores de credencial vienen envueltos en un HttpError larguísimo, y
    todas las llamadas fallan igual: sin esto, la salida son cinco muros de
    texto idénticos y el diagnóstico acaba culpando a los canales.
    """
    texto = str(exc)
    if "API key not valid" in texto or "API_KEY_INVALID" in texto or "keyInvalid" in texto:
        return ("clave_invalida",
                "La YOUTUBE_API_KEY guardada no es válida.")
    if "SERVICE_DISABLED" in texto or "has not been used in project" in texto:
        return ("api_apagada",
                "La YouTube Data API v3 no está habilitada en el proyecto de esa clave.")
    if "quotaExceeded" in texto or "quota" in texto.lower():
        return ("sin_cuota",
                "Se agotó la cuota diaria de la API. Se renueva a medianoche (hora del Pacífico).")
    if "accessNotConfigured" in texto or "forbidden" in texto.lower():
        return ("clave_restringida",
                "La clave tiene restricciones que bloquean esta llamada.")
    return None


def videos_por_api(cfg, contar):
    """Videos más vistos de cada canal y de cada búsqueda, vía YouTube Data API.

    Es la vía que encuentra los virales viejos: order=viewCount ordena por
    vistas históricas, así que un video de hace tres años con dos millones de
    reproducciones sale primero. El RSS, en cambio, solo ve lo último subido.
    """
    servicio = _servicio_youtube()
    ids = []
    canales_de = {}

    for referencia in cfg["youtube_canales"]:
        try:
            channel_id = resolver_channel_id(referencia)
            encontrados = _buscar(servicio, cfg, channelId=channel_id,
                                  maxResults=cfg["youtube_max_videos_por_canal"] * 3)
        except Exception as exc:
            motivo = _motivo_error_api(exc)
            if motivo:
                # No tiene sentido repetir la misma llamada rota una vez por
                # canal y otra por búsqueda: todas van a fallar igual.
                contar["api_error"] = motivo
                logger.error(motivo[1])
                return []
            logger.warning(f"No se pudo buscar en {referencia}: {exc}")
            contar["canales_fallidos"] += 1
            continue
        contar["canales_ok"] += 1
        logger.info(f"{referencia}: {len(encontrados)} video(s) por vistas")
        for vid in encontrados:
            canales_de.setdefault(vid, referencia)
        ids.extend(encontrados)

    for consulta in cfg.get("youtube_busquedas") or []:
        try:
            encontrados = _buscar(servicio, cfg, q=consulta,
                                  maxResults=cfg.get("youtube_max_por_busqueda", 10))
        except Exception as exc:
            motivo = _motivo_error_api(exc)
            if motivo:
                contar["api_error"] = motivo
                logger.error(motivo[1])
                return []
            logger.warning(f"Falló la búsqueda «{consulta}»: {exc}")
            continue
        contar["busquedas_ok"] = contar.get("busquedas_ok", 0) + 1
        logger.info(f"«{consulta}»: {len(encontrados)} video(s) por vistas")
        ids.extend(encontrados)

    # Un mismo video puede salir en varias búsquedas; se pide una sola vez.
    ids = list(dict.fromkeys(ids))
    if not ids:
        return []

    detalles = _detalles_de_videos(servicio, ids)
    videos = []
    for vid in ids:
        item = detalles.get(vid)
        if not item:
            continue
        snip = item.get("snippet", {})
        try:
            vistas = int(item.get("statistics", {}).get("viewCount", 0))
        except (TypeError, ValueError):
            vistas = 0
        videos.append({
            "id": vid,
            "titulo": snip.get("title", ""),
            "canal": snip.get("channelTitle", canales_de.get(vid, "")),
            "publicado": snip.get("publishedAt", ""),
            "vistas": vistas,
            "url": f"https://www.youtube.com/watch?v={vid}",
        })
    videos.sort(key=lambda v: v["vistas"], reverse=True)
    return videos


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


def probar_clave():
    """Comprueba que YOUTUBE_API_KEY sirve, con una sola llamada barata.

    Existe porque los tres fallos posibles se parecen desde fuera (no hay
    clave, la clave es inválida, la API no está habilitada en ese proyecto)
    y cada uno se arregla en un sitio distinto de la consola de Google.
    """
    if not hay_api_key():
        print(" ✗ No hay YOUTUBE_API_KEY.")
        print("   Ponla con:  python youtube_scout.py --guardar-clave AIzaSy...")
        print("   Se saca en: consola de Google Cloud → APIs y servicios →")
        print("   Credenciales → Crear credenciales → Clave de API.")
        print("   (Ojo: client_secret.json NO sirve para esto; ese es para subir.)")
        return False

    clave = os.environ["YOUTUBE_API_KEY"].strip()
    print(f" Clave encontrada: {clave[:8]}…{clave[-4:]} ({len(clave)} caracteres)")

    # Pegar el texto de ejemplo en vez de la clave real es el error más fácil
    # de cometer y el más confuso de leer: Google responde "API key not valid",
    # que manda a revisar permisos y proyectos cuando en realidad no hay clave.
    # Una clave de verdad son ~39 caracteres y empieza por AIzaSy.
    if "..." in clave or "…" in clave or clave.upper().startswith("PEGA"):
        print(" ✗ Eso es el texto de ejemplo, no una clave.")
        print("   Guarda la real (sin comillas) con:")
        print("     python youtube_scout.py --guardar-clave AIzaSy...")
        return False
    if len(clave) < 30:
        print(f" ✗ La clave parece incompleta: {len(clave)} caracteres, y una real tiene ~39.")
        print("   Cópiala entera y guárdala con:")
        print("     python youtube_scout.py --guardar-clave AIzaSy...")
        return False

    try:
        servicio = _servicio_youtube()
        resp = servicio.videos().list(part="snippet", chart="mostPopular",
                                      regionCode="MX", maxResults=1).execute()
    except Exception as exc:
        texto = str(exc)
        print(" ✗ La clave no funcionó.")
        if "API_KEY_INVALID" in texto or "not valid" in texto:
            print("   Motivo: la clave es inválida. Vuelve a copiarla, sin espacios.")
        elif "has not been used" in texto or "disabled" in texto or "SERVICE_DISABLED" in texto:
            print("   Motivo: la YouTube Data API v3 no está habilitada EN ESE proyecto.")
            print("   Revisa que la clave sea del mismo proyecto donde la habilitaste.")
        elif "quota" in texto.lower():
            print("   Motivo: cuota agotada por hoy. Se renueva a medianoche (hora del Pacífico).")
        elif "referer" in texto.lower() or "blocked" in texto.lower():
            print("   Motivo: la clave tiene restricciones. En la consola, ponla")
            print("   sin restricción de aplicación, o restringida solo a YouTube Data API.")
        print(f"   Detalle: {texto[:300]}")
        return False

    titulo = (resp.get("items") or [{}])[0].get("snippet", {}).get("title", "?")
    print(" ✓ La clave funciona. YouTube respondió correctamente.")
    print(f"   (prueba: video más popular en MX ahora — «{titulo[:60]}»)")
    return True


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
              "titulo_excluido", "pocas_vistas", "sin_subtitulos", "bloqueado", "muy_corto", "nuevos"):
        contar.setdefault(k, 0)

    vistos = cola.cargar_historial()
    en_cola = cola.ids_en_cola()
    candidatos = []

    contar["via"] = "api" if hay_api_key() else "rss"
    if contar["via"] == "api":
        # order=viewCount: los más vistos de siempre. Aquí sí salen los
        # virales de hace años, que es donde está el material bueno.
        videos = videos_por_api(cfg, contar)
        por_canal = {}
    else:
        # Sin clave solo se puede leer el RSS, que trae lo último subido.
        videos = []
        cache = _cache_canales()
        for i, referencia in enumerate(cfg["youtube_canales"]):
            if i > 0:
                time.sleep(RATE_LIMIT_SEG)
            logger.info(f"Escaneando {referencia}...")
            try:
                channel_id = resolver_channel_id(referencia, cache)
                _, del_canal = videos_del_canal(channel_id)
            except Exception as exc:
                logger.warning(f"No se pudo leer {referencia}: {exc}")
                contar["canales_fallidos"] += 1
                continue
            contar["canales_ok"] += 1
            videos.extend(del_canal)
        videos.sort(key=lambda v: v["vistas"], reverse=True)
        por_canal = {}

    for video in videos:
        if len(candidatos) >= cfg.get("youtube_max_candidatos_salida", 8):
            break
        # Tope por canal, para que un solo canal no se lleve toda la tanda.
        canal = video.get("canal") or "?"
        if por_canal.get(canal, 0) >= cfg["youtube_max_videos_por_canal"]:
            continue
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
            # "Este video no tiene subtítulos" y "YouTube está bloqueando tu
            # IP" se arreglan de formas opuestas — buscar otro canal, o
            # cambiar de red y esperar. Contarlos juntos mandaría a perseguir
            # el problema equivocado.
            nombre = type(exc).__name__
            if nombre in ("IpBlocked", "RequestBlocked", "PoTokenRequired"):
                logger.warning(f"YouTube bloqueó la petición de subtítulos ({nombre}).")
                contar["bloqueado"] += 1
            else:
                logger.warning(f"Sin subtítulos usables en {video['id']}: {nombre}")
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
            "subreddit": canal,  # el resto del pipeline lee este campo como "de dónde salió"
            "canal": canal,
            "titulo_original": video["titulo"],
            "texto_original": texto,
            "vistas": video["vistas"],
            "rank_en_subreddit": por_canal.get(canal, 0),
            "url": video["url"],
            "autor": canal,
            "intentos": 0,
        })
        en_cola.add(cand_id)
        contar["nuevos"] += 1
        por_canal[canal] = por_canal.get(canal, 0) + 1

    return candidatos


def explicar(cfg, contar, pendientes_antes, agregados, total_cola):
    """Informe legible de por qué salieron (o no salieron) videos nuevos."""
    # Se lee con .get porque este informe existe justo para cuando algo salió
    # mal: si el escaneo abortó antes de llenar los contadores, un KeyError
    # aquí taparía el error de verdad con uno mío.
    via = contar.get("via", "rss")
    print("")
    print("─── Diagnóstico de la búsqueda en YouTube ───")
    if via == "api":
        print(" Vía                  : YouTube Data API (ordena por vistas de siempre)")
    else:
        print(" Vía                  : feed RSS — SOLO ve los ~15 videos más recientes")
    print(f" Canales leídos ok    : {contar.get('canales_ok', 0)} de {len(cfg['youtube_canales'])}"
          + (f"  ({contar.get('canales_fallidos', 0)} fallaron)" if contar.get('canales_fallidos', 0) else ""))
    print(f" Videos revisados     : {contar.get('videos', 0)}")
    print(f"   • ya usados antes  : {contar.get('ya_vistos', 0)}")
    print(f"   • ya en la cola    : {contar.get('ya_en_cola', 0)}")
    print(f"   • título excluido  : {contar.get('titulo_excluido', 0)}")
    print(f"   • pocas vistas (<{cfg['youtube_min_vistas']:,}) : {contar.get('pocas_vistas', 0)}")
    print(f"   • sin subtítulos   : {contar.get('sin_subtitulos', 0)}")
    print(f"   • bloqueados por YouTube : {contar.get('bloqueado', 0)}")
    print(f"   • muy cortos (<{cfg['youtube_min_palabras']} palabras) : {contar.get('muy_corto', 0)}")
    print(f"   • NUEVOS           : {contar.get('nuevos', 0)}")
    print("")
    print(f" Cola antes           : {pendientes_antes}")
    print(f" Agregados ahora      : {agregados}")
    print(f" Cola ahora           : {total_cola}")
    print("")

    api_error = contar.get("api_error")
    if api_error:
        codigo, mensaje = api_error
        # Va PRIMERO: con la clave rota no se llegó a mirar ningún canal, así
        # que hablar de @handles aquí manda a arreglar lo que no está roto.
        print(f" ⛔ {mensaje}")
        print("    No es problema de los canales ni de la configuración: ninguna")
        print("    búsqueda llegó a hacerse.")
        print("")
        if codigo == "clave_invalida":
            print("    Guarda la clave REAL de la consola de Google (empieza por AIzaSy y")
            print("    tiene ~39 caracteres). Pega la tuya, no este texto:")
            print("      python youtube_scout.py --guardar-clave TU_CLAVE_AQUI")
        elif codigo == "api_apagada":
            print("    Habilítala, o comprueba que la clave sea del mismo proyecto:")
            print("      https://console.cloud.google.com/apis/library/youtube.googleapis.com")
        elif codigo == "sin_cuota":
            print("    Espera al reinicio de cuota, o baja \"youtube_max_por_busqueda\"")
            print("    y el número de búsquedas en config_trends.json.")
        elif codigo == "clave_restringida":
            print("    En la consola, deja la clave con restricción de aplicación")
            print("    \"Ninguno\" y restringida solo a la YouTube Data API v3.")
        print("")
        print("    Compruébala sin escanear con:  python youtube_scout.py --probar-clave")
    elif contar.get('bloqueado', 0) and contar.get('bloqueado', 0) >= max(1, contar.get('nuevos', 0)):
        print(" ⛔ YouTube bloqueó las peticiones de subtítulos desde esta conexión.")
        print("    No es culpa de los canales ni de la configuración. Suele pasar con")
        print("    VPN, con datos compartidos, o si se corrió muchas veces seguidas.")
        print("    Prueba desde otra red (el WiFi de casa) o espera unas horas.")
    elif contar.get('canales_ok', 0) == 0:
        print(" ⛔ Ningún canal respondió. Revisa la conexión, y que los @handles de")
        print("    \"youtube_canales\" existan: ábrelos en el navegador; si dan 404,")
        print("    el nombre está mal (ej. @LaCotorrisa no existe, es @LaCotorrisaOficial).")
    elif via == "rss" and contar.get('pocas_vistas', 0) >= max(1, contar.get('videos', 0) // 2):
        # El caso más común y el más confuso: todo descartado por vistas, no
        # porque los canales sean malos, sino porque el RSS solo enseña lo
        # recién subido, que todavía no acumuló nada.
        print(" ⚠️  Sin YOUTUBE_API_KEY solo se ve lo ÚLTIMO que subió cada canal, y")
        print("    esos videos aún no acumulan vistas: por eso se descartan todos.")
        print("    Los virales de hace años NO aparecen por esta vía.")
        print("")
        print("    Para verlos, saca una clave gratis (5 minutos) y ponla en secretos.env:")
        print("      1. https://console.cloud.google.com/apis/library/youtube.googleapis.com")
        print("      2. Habilitar la API → Credenciales → Crear clave de API")
        print("      3. echo 'YOUTUBE_API_KEY=AIza...' >> secretos.env")
        print("")
        print("    Mientras tanto, baja \"youtube_min_vistas\" en config_trends.json.")
    elif contar.get('nuevos', 0) == 0 and contar.get('sin_subtitulos', 0) >= max(1, contar.get('videos', 0) // 3):
        print(" ℹ️  Muchos videos sin subtítulos públicos. Sin subtítulos no hay texto")
        print("    que reescribir; busca canales que sí los tengan activados.")
    elif contar.get('nuevos', 0) == 0 and contar.get('pocas_vistas', 0):
        print(" ℹ️  Ningún video llega al mínimo de vistas. Baja")
        print("    \"youtube_min_vistas\" en config_trends.json si quieres más material.")
    elif contar.get('nuevos', 0) == 0:
        print(" ℹ️  No hay videos nuevos que cumplan los filtros. Agrega más canales")
        print("    en \"youtube_canales\" o más temas en \"youtube_busquedas\".")

    if via == "rss" and contar.get('nuevos', 0):
        print("")
        print(" ℹ️  Con YOUTUBE_API_KEY en secretos.env saldrían además los videos más")
        print("    vistos de siempre, no solo los recientes.")


def main(argv=None):
    # argv explícito, y sin caer a sys.argv por omisión: pipeline.py llama a
    # main() sin argumentos, y argparse se comería los flags del pipeline
    # (--hasta, --forzar…) abortando la etapa con un error de uso absurdo.
    ap = argparse.ArgumentParser(description="Busca historias virales en YouTube y las deja en la cola.")
    ap.add_argument("--diagnostico", action="store_true",
                    help="Escanea y explica por qué salieron (o no) historias nuevas.")
    ap.add_argument("--canal", action="append", metavar="@CANAL",
                    help="Escanear solo este canal (se puede repetir). Ignora la lista de config.")
    ap.add_argument("--probar-clave", action="store_true",
                    help="Comprueba que YOUTUBE_API_KEY sirve, sin escanear nada.")
    ap.add_argument("--guardar-clave", metavar="AIzaSy...",
                    help="Guarda la clave en secretos.env (reemplaza la anterior) y la prueba.")
    args = ap.parse_args(argv or [])

    if args.guardar_clave:
        try:
            ruta = secretos.guardar("YOUTUBE_API_KEY", args.guardar_clave)
        except ValueError as exc:
            print(f" ✗ {exc}")
            return 1
        print(f" Guardada en {ruta}")
        return 0 if probar_clave() else 1

    if args.probar_clave:
        return 0 if probar_clave() else 1

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
    sys.exit(main(sys.argv[1:]) or 0)
