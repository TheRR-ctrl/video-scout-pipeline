"""
Trend Scout — detecta historias con potencial viral en Reddit.

Salida: pipeline_state/candidatos.json
Cada corrida evita repetir posts ya vistos (pipeline_state/historial_vistos.json).

Requiere: pip install requests

Nota sobre el acceso a Reddit:
  La API oficial de Reddit requiere pasar por su formulario de "Reddit Data
  Access" (revisión de la Responsible Builder Policy), que no siempre se
  concede para uso personal. El endpoint JSON público sin autenticación
  (www.reddit.com/r/<sub>/top/.json) también está bloqueado por el filtro
  anti-bot de Reddit, incluso con un User-Agent de navegador real.

  Lo que sí funciona sin bloqueo es el **feed RSS/Atom** de cada subreddit
  (www.reddit.com/r/<sub>/top/.rss), que trae el texto completo de cada post.
  Es una vía pública, de solo lectura, pensada para lectores de RSS — el
  mismo tipo de acceso que cualquier agregador de noticias usa. Igual que
  antes: nunca se postea, comenta, vota ni envía mensajes, y cada historia
  conserva su autor y URL original para dar atribución en el video (ver
  "autor"/"url" en el candidato), en vez de presentar el contenido como
  propio.

  El RSS no expone score ni número de comentarios (a diferencia del JSON),
  así que el filtrado por "viralidad" se basa en el orden del feed /top/
  (ya viene rankeado por Reddit) en vez de umbrales de score/comentarios.

  Si Reddit empieza a bloquear también el RSS, hay que espaciar más las
  corridas (ver RATE_LIMIT_SEG) o retomar la vía de la API oficial.
"""
import os
import re
import html
import json
import time
import logging
from xml.etree import ElementTree as ET

try:
    import requests
except ImportError:
    requests = None

CARPETA_ESTADO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pipeline_state")
RUTA_CANDIDATOS = os.path.join(CARPETA_ESTADO, "candidatos.json")
RUTA_HISTORIAL = os.path.join(CARPETA_ESTADO, "historial_vistos.json")
RUTA_CONFIG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config_trends.json")

RATE_LIMIT_SEG = 12.0  # pausa entre requests a reddit.com; el RSS es más estricto que el JSON con el rate limit

CONFIG_DEFAULT = {
    "subreddits": [
        "AmItheAsshole",
        "relationships",
        "tifu",
        "confession",
        "AmItheButtface",
        "relationship_advice",
    ],
    "time_filter": "day",
    "limite_por_subreddit": 15,
    "min_palabras_texto": 80,
    "max_palabras_texto": 1800,
    "max_candidatos_salida": 20,
    # Para que salgan también videos largos (3-5 min): de los candidatos que
    # superen este umbral de palabras, se reservan algunos cupos aunque no
    # sean los mejor rankeados en /top/. El resto de los cupos se llena
    # normal, por ranking.
    "umbral_palabras_historia_larga": 400,
    "min_candidatos_largos": 3,
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("trend_scout")

_UA_NAVEGADOR = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_NS = {"a": "http://www.w3.org/2005/Atom"}


def cargar_config():
    cfg = dict(CONFIG_DEFAULT)
    if os.path.exists(RUTA_CONFIG):
        with open(RUTA_CONFIG, "r", encoding="utf-8") as f:
            cfg.update(json.load(f))
    return cfg


def cargar_historial():
    if os.path.exists(RUTA_HISTORIAL):
        with open(RUTA_HISTORIAL, "r", encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def guardar_historial(vistos):
    os.makedirs(CARPETA_ESTADO, exist_ok=True)
    with open(RUTA_HISTORIAL, "w", encoding="utf-8") as f:
        json.dump(sorted(vistos), f, ensure_ascii=False, indent=2)


def _limpiar_contenido_html(contenido_crudo):
    """Convierte el HTML del <content> del feed en texto plano, quitando el
    pie que Reddit agrega ("submitted by ... [link] [comments]")."""
    texto = html.unescape(contenido_crudo or "")
    texto = re.sub(r"<!--.*?-->", "", texto, flags=re.DOTALL)
    texto = texto.split("submitted by")[0]
    texto = re.sub(r"<[^>]+>", " ", texto)
    return re.sub(r"\s+", " ", texto).strip()


def obtener_posts_publicos(subreddit, cfg):
    """Lee el feed RSS/Atom público 'top' de un subreddit (solo lectura)."""
    url = f"https://www.reddit.com/r/{subreddit}/top/.rss"
    params = {"t": cfg["time_filter"], "limit": cfg["limite_por_subreddit"]}
    headers = {"User-Agent": _UA_NAVEGADOR}

    resp = requests.get(url, params=params, headers=headers, timeout=15)
    if resp.status_code == 429:
        # Un solo reintento con una espera más larga antes de rendirse.
        time.sleep(RATE_LIMIT_SEG * 2)
        resp = requests.get(url, params=params, headers=headers, timeout=15)
    if resp.status_code in (429, 403):
        raise RuntimeError(f"Bloqueado por reddit.com ({resp.status_code}).")
    resp.raise_for_status()

    root = ET.fromstring(resp.text)
    posts = []
    for entry in root.findall("a:entry", _NS):
        post_id = (entry.findtext("a:id", default="", namespaces=_NS) or "").replace("t3_", "")
        titulo = entry.findtext("a:title", default="", namespaces=_NS) or ""
        texto = _limpiar_contenido_html(entry.findtext("a:content", default="", namespaces=_NS))
        link_el = entry.find("a:link", _NS)
        url_post = link_el.get("href") if link_el is not None else ""
        autor = entry.findtext("a:author/a:name", default="", namespaces=_NS) or ""
        autor = autor.replace("/u/", "").strip()

        posts.append({
            "id": post_id,
            "titulo": titulo,
            "texto": texto,
            "url": url_post,
            "autor": autor,
        })
    return posts


def escanear(cfg):
    if requests is None:
        raise RuntimeError("Falta el paquete 'requests'. Instálalo con: pip install requests")

    vistos = cargar_historial()
    candidatos = []

    for i, nombre_sub in enumerate(cfg["subreddits"]):
        if i > 0:
            time.sleep(RATE_LIMIT_SEG)

        logger.info(f"Escaneando r/{nombre_sub}...")
        try:
            posts = obtener_posts_publicos(nombre_sub, cfg)
        except Exception as exc:
            logger.warning(f"No se pudo leer r/{nombre_sub}: {exc}")
            continue

        for rank, post in enumerate(posts):
            post_id = post["id"]
            if not post_id or post_id in vistos:
                continue
            texto = post["texto"]
            if not texto:
                continue

            num_palabras = len(texto.split())
            if not (cfg["min_palabras_texto"] <= num_palabras <= cfg["max_palabras_texto"]):
                continue

            autor = post["autor"]
            candidatos.append({
                "id": post_id,
                "subreddit": nombre_sub,
                "titulo_original": post["titulo"],
                "texto_original": texto,
                # El feed RSS no trae score/num_comments; usamos la posición
                # en el ranking /top/ (ya ordenado por Reddit) como proxy.
                "rank_en_subreddit": rank,
                "url": post["url"],
                # Se conserva para dar atribución en la descripción del video
                # (evita presentar la historia como propia).
                "autor": f"u/{autor}" if autor and autor != "[deleted]" else "[autor eliminado]",
            })
            vistos.add(post_id)

    candidatos.sort(key=lambda c: c["rank_en_subreddit"])

    # Reserva algunos cupos para historias largas (para que también salgan
    # videos de varios minutos), aunque no sean las mejor rankeadas.
    umbral = cfg["umbral_palabras_historia_larga"]
    cupos_largos = cfg["min_candidatos_largos"]
    largas = [c for c in candidatos if len(c["texto_original"].split()) >= umbral]

    seleccionados = largas[:cupos_largos]
    ids_ya_elegidos = {c["id"] for c in seleccionados}
    for c in candidatos:
        if len(seleccionados) >= cfg["max_candidatos_salida"]:
            break
        if c["id"] not in ids_ya_elegidos:
            seleccionados.append(c)
            ids_ya_elegidos.add(c["id"])

    seleccionados.sort(key=lambda c: c["rank_en_subreddit"])

    guardar_historial(vistos)
    return seleccionados


def main():
    cfg = cargar_config()
    candidatos = escanear(cfg)

    os.makedirs(CARPETA_ESTADO, exist_ok=True)
    with open(RUTA_CANDIDATOS, "w", encoding="utf-8") as f:
        json.dump(candidatos, f, ensure_ascii=False, indent=2)

    logger.info(f"{len(candidatos)} candidato(s) guardado(s) en {RUTA_CANDIDATOS}")
    for c in candidatos:
        print(f" • [{c['subreddit']}] {c['titulo_original']} (rank={c['rank_en_subreddit']})")


if __name__ == "__main__":
    main()
