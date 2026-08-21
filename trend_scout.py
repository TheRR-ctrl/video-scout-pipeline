"""
Trend Scout — detecta historias con potencial viral en Reddit.

Salida: pipeline_state/candidatos.json
Cada corrida evita repetir posts ya vistos (pipeline_state/historial_vistos.json).

Requiere: pip install praw
Credenciales: config_trends.json (junto a este script) o variables de entorno
REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET.

Cumplimiento con la Responsible Builder Policy de Reddit:
  - Propósito declarado: lectura de posts públicos (read_only) de un puñado de
    subreddits, para adaptar historias como narración en un canal propio de
    YouTube. No se hace scraping masivo ni se re-publica contenido en Reddit.
  - Transparencia: el user_agent debe identificar la app y a su operador (ver
    CONFIG_DEFAULT["user_agent"] más abajo) tal como exige la política de Reddit.
  - Cada historia conserva su autor y URL original (ver "autor"/"url" en el
    candidato) para poder dar atribución aguas abajo, en vez de presentar el
    contenido como propio.
"""
import os
import json
import logging

try:
    import praw
except ImportError:
    praw = None

CARPETA_ESTADO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pipeline_state")
RUTA_CANDIDATOS = os.path.join(CARPETA_ESTADO, "candidatos.json")
RUTA_HISTORIAL = os.path.join(CARPETA_ESTADO, "historial_vistos.json")
RUTA_CONFIG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config_trends.json")

CONFIG_DEFAULT = {
    "client_id": None,
    "client_secret": None,
    # Formato recomendado por Reddit: "<plataforma>:<nombre-app>:<version> (by /u/TU_USUARIO)".
    # Reemplaza TU_USUARIO_REDDIT por tu usuario real: es lo que le dice a Reddit
    # quién opera la app (requisito de transparencia de la Responsible Builder Policy).
    "user_agent": "python:trend-scout-video-pipeline:v1.0 (by /u/TU_USUARIO_REDDIT)",
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
    cfg["client_id"] = cfg["client_id"] or os.environ.get("REDDIT_CLIENT_ID")
    cfg["client_secret"] = cfg["client_secret"] or os.environ.get("REDDIT_CLIENT_SECRET")
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


def escanear(cfg):
    if praw is None:
        raise RuntimeError("Falta el paquete 'praw'. Instálalo con: pip install praw")
    if not cfg["client_id"] or not cfg["client_secret"]:
        raise RuntimeError(
            "Faltan credenciales de Reddit. Crea config_trends.json con client_id/client_secret "
            "o define REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET como variables de entorno."
        )
    if "TU_USUARIO_REDDIT" in cfg["user_agent"]:
        logger.warning(
            "user_agent sigue con el placeholder TU_USUARIO_REDDIT. Edítalo en "
            "config_trends.json para identificar tu app y tu usuario de Reddit "
            "(requisito de transparencia de la Responsible Builder Policy)."
        )

    reddit = praw.Reddit(
        client_id=cfg["client_id"],
        client_secret=cfg["client_secret"],
        user_agent=cfg["user_agent"],
    )
    reddit.read_only = True

    vistos = cargar_historial()
    candidatos = []

    for nombre_sub in cfg["subreddits"]:
        logger.info(f"Escaneando r/{nombre_sub}...")
        try:
            sub = reddit.subreddit(nombre_sub)
            posts = sub.top(time_filter=cfg["time_filter"], limit=cfg["limite_por_subreddit"])
        except Exception as exc:
            logger.warning(f"No se pudo leer r/{nombre_sub}: {exc}")
            continue

        for post in posts:
            if post.id in vistos:
                continue
            if post.stickied or not getattr(post, "selftext", ""):
                continue
            if post.score < cfg["min_score"] or post.num_comments < cfg["min_comentarios"]:
                continue

            num_palabras = len(post.selftext.split())
            if not (cfg["min_palabras_texto"] <= num_palabras <= cfg["max_palabras_texto"]):
                continue

            candidatos.append({
                "id": post.id,
                "subreddit": nombre_sub,
                "titulo_original": post.title,
                "texto_original": post.selftext,
                "score": post.score,
                "num_comentarios": post.num_comments,
                "url": f"https://reddit.com{post.permalink}",
                # Se conserva para dar atribución en la descripción del video
                # (evita presentar la historia como propia).
                "autor": f"u/{post.author}" if post.author else "[autor eliminado]",
            })
            vistos.add(post.id)

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
