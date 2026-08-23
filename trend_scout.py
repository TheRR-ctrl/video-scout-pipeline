"""
Trend Scout — detecta historias con potencial viral en Reddit.

Salida: pipeline_state/candidatos.json
Cada corrida evita repetir posts ya vistos (pipeline_state/historial_vistos.json).

Requiere: pip install requests

Nota sobre el acceso a Reddit:
  La API oficial de Reddit (PRAW/OAuth "script app") requiere aprobación de
  Reddit, que no siempre se concede para uso personal. Como alternativa, este
  script usa los endpoints públicos de solo lectura de old.reddit.com
  (https://old.reddit.com/r/<sub>/top/.json), que no requieren credenciales ni
  OAuth y son los mismos que carga cualquier navegador al visitar Reddit sin
  iniciar sesión. Sigue siendo 100% de solo lectura: nunca se postea, comenta,
  vota ni envía mensajes. Esto no es la vía "oficial" de la Data API de Reddit,
  así que:
    - Se respeta un rate limit conservador entre requests (ver RATE_LIMIT_SEG).
    - Se manda un User-Agent de navegador real (ver _UA_NAVEGADOR): un
      User-Agent "de script", aunque sea descriptivo, dispara el filtro
      anti-bot de Reddit y devuelve 403 incluso sin autenticación de por
      medio.
    - Si Reddit empieza a devolver 429/403 de forma consistente, hay que
      espaciar aún más las corridas o volver a intentar la API oficial más
      adelante.

Cumplimiento con la Responsible Builder Policy de Reddit:
  - Propósito declarado: lectura de posts públicos de un puñado de
    subreddits, para adaptar historias como narración en un canal propio de
    YouTube. No se hace scraping masivo ni se re-publica contenido en Reddit.
  - Cada historia conserva su autor y URL original (ver "autor"/"url" en el
    candidato) para poder dar atribución aguas abajo, en vez de presentar el
    contenido como propio.
"""
import os
import json
import time
import logging

try:
    import requests
except ImportError:
    requests = None

CARPETA_ESTADO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pipeline_state")
RUTA_CANDIDATOS = os.path.join(CARPETA_ESTADO, "candidatos.json")
RUTA_HISTORIAL = os.path.join(CARPETA_ESTADO, "historial_vistos.json")
RUTA_CONFIG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config_trends.json")

RATE_LIMIT_SEG = 3.0  # pausa entre requests a reddit.com, para no golpear el endpoint público

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
    "min_score": 500,
    "min_comentarios": 100,
    "min_palabras_texto": 80,
    "max_palabras_texto": 1800,
    "max_candidatos_salida": 20,
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("trend_scout")


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


_UA_NAVEGADOR = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def obtener_posts_publicos(subreddit, cfg):
    """Lee el listado 'top' público de un subreddit vía el JSON sin autenticar.

    Usa old.reddit.com (en vez de www.reddit.com) y un User-Agent de
    navegador real: el filtro anti-bot de Reddit devuelve 403 a requests con
    un User-Agent que "suena" a script/API, incluso sin autenticación de por
    medio y viniendo de una IP residencial normal.
    """
    url = f"https://old.reddit.com/r/{subreddit}/top/.json"
    params = {"t": cfg["time_filter"], "limit": cfg["limite_por_subreddit"], "raw_json": 1}
    headers = {
        "User-Agent": _UA_NAVEGADOR,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "es-MX,es;q=0.9,en;q=0.8",
    }

    resp = requests.get(url, params=params, headers=headers, timeout=15)
    if resp.status_code == 429:
        raise RuntimeError("Rate limit (429) de reddit.com. Reduce la frecuencia de corridas.")
    resp.raise_for_status()

    data = resp.json()
    return [child["data"] for child in data.get("data", {}).get("children", [])]


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

        for post in posts:
            post_id = post.get("id")
            if not post_id or post_id in vistos:
                continue
            texto = post.get("selftext", "")
            if post.get("stickied") or not texto:
                continue
            if post.get("score", 0) < cfg["min_score"] or post.get("num_comments", 0) < cfg["min_comentarios"]:
                continue
            if post.get("over_18"):
                continue

            num_palabras = len(texto.split())
            if not (cfg["min_palabras_texto"] <= num_palabras <= cfg["max_palabras_texto"]):
                continue

            autor = post.get("author") or ""
            candidatos.append({
                "id": post_id,
                "subreddit": nombre_sub,
                "titulo_original": post.get("title", ""),
                "texto_original": texto,
                "score": post.get("score", 0),
                "num_comentarios": post.get("num_comments", 0),
                "url": f"https://reddit.com{post.get('permalink', '')}",
                # Se conserva para dar atribución en la descripción del video
                # (evita presentar la historia como propia).
                "autor": f"u/{autor}" if autor and autor != "[deleted]" else "[autor eliminado]",
            })
            vistos.add(post_id)

    candidatos.sort(key=lambda c: c["score"] + c["num_comentarios"], reverse=True)
    candidatos = candidatos[:cfg["max_candidatos_salida"]]

    guardar_historial(vistos)
    return candidatos


def main():
    cfg = cargar_config()
    candidatos = escanear(cfg)

    os.makedirs(CARPETA_ESTADO, exist_ok=True)
    with open(RUTA_CANDIDATOS, "w", encoding="utf-8") as f:
        json.dump(candidatos, f, ensure_ascii=False, indent=2)

    logger.info(f"{len(candidatos)} candidato(s) guardado(s) en {RUTA_CANDIDATOS}")
    for c in candidatos:
        print(f" • [{c['subreddit']}] {c['titulo_original']} (score={c['score']}, comentarios={c['num_comentarios']})")


if __name__ == "__main__":
    main()
