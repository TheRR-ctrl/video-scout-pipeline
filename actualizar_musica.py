"""
Actualizar Música — descarga pistas nuevas de música libre de derechos desde
Jamendo (https://www.jamendo.com) para rotar el fondo musical de los videos.

No es parte de la corrida diaria del pipeline (la música no necesita
cambiar por video): se pensó para correrse cada tanto (semanal/mensual,
manual o con su propio cron) y sumar variedad a lo que ya hay.

Requiere: pip install requests
Credenciales: variable de entorno JAMENDO_CLIENT_ID (gratis en
https://devportal.jamendo.com/ → Manage Apps → Add a new application).

Solo descarga pistas con licencia que permite uso comercial y sin cláusula
"No Derivatives" (evita problemas al mezclarlas con la narración). Guarda la
atribución de cada pista (artista, licencia, URL) en
pipeline_state/musica_atribucion.json para poder incluirla en la descripción
del video si la licencia lo exige (CC-BY).
"""
import os
import re
import json
import logging

try:
    import requests
except ImportError:
    requests = None

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CARPETA_ESTADO = os.path.join(BASE_DIR, "pipeline_state")
RUTA_HISTORIAL = os.path.join(CARPETA_ESTADO, "musica_historial.json")
RUTA_ATRIBUCION = os.path.join(CARPETA_ESTADO, "musica_atribucion.json")

JAMENDO_API = "https://api.jamendo.com/v3.0/tracks/"
PISTAS_POR_EMOCION = 3  # cuántas pistas mantener por categoría

# Tags de Jamendo por emoción (ver detectar_emocion_historia en
# generar_video_maestro.py para las mismas 4 categorías). Jamendo combina
# varios tags a la vez con lógica "Y" (casi nunca hay resultados), así que
# se consulta un tag a la vez y se van juntando resultados de varios.
TAGS_POR_EMOCION = {
    "drama": ["sad", "dramatic", "emotional", "melancholic", "piano"],
    "venganza": ["dark", "intense", "epic", "angry", "action"],
    "suspenso": ["dark", "ambient", "cinematic", "tension", "mysterious"],
    "comedia": ["happy", "funny", "upbeat", "comedy", "fun"],
}

# Licencias Creative Commons que sí permiten uso comercial y derivados
# (necesario para poder mezclar la pista con la narración/voiceover).
LICENCIAS_PERMITIDAS = ["by", "by-sa"]

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("actualizar_musica")


def cargar_json(ruta, default):
    if os.path.exists(ruta):
        with open(ruta, "r", encoding="utf-8") as f:
            return json.load(f)
    return default


def guardar_json(ruta, data):
    os.makedirs(os.path.dirname(ruta), exist_ok=True)
    with open(ruta, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def buscar_pistas(client_id, tags, cantidad, ya_descargadas):
    """Prueba un tag y una licencia a la vez (Jamendo combina varios tags con
    lógica "Y", que casi nunca da resultados) hasta juntar suficientes pistas
    nuevas, válidas y no repetidas."""
    elegidas = []
    vistas_en_esta_busqueda = set()

    for tag in tags:
        for licencia in LICENCIAS_PERMITIDAS:
            if len(elegidas) >= cantidad:
                return elegidas

            params = {
                "client_id": client_id,
                "format": "json",
                "limit": 10,
                "tags": tag,
                "license_cc": licencia,
                "audioformat": "mp32",
                "include": "musicinfo",
                "order": "popularity_total",
            }
            try:
                resp = requests.get(JAMENDO_API, params=params, timeout=20)
                resp.raise_for_status()
                data = resp.json()
            except Exception as exc:
                logger.warning(f"    fallo consultando tag={tag} licencia={licencia}: {exc}")
                continue

            for track in data.get("results", []):
                if len(elegidas) >= cantidad:
                    break
                track_id = str(track.get("id"))
                if track_id in ya_descargadas or track_id in vistas_en_esta_busqueda:
                    continue
                if not track.get("audiodownload_allowed"):
                    continue
                vistas_en_esta_busqueda.add(track_id)
                elegidas.append(track)

    return elegidas


def descargar_pista(track, destino):
    url = track.get("audiodownload") or track.get("audio")
    if not url:
        return False
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    with open(destino, "wb") as f:
        f.write(resp.content)
    return True


def limpiar_nombre_archivo(nombre):
    return re.sub(r"[^\w\-]", "_", nombre)[:60]


def main():
    if requests is None:
        raise SystemExit("Falta el paquete 'requests'. Instálalo con: pip install requests")

    client_id = os.environ.get("JAMENDO_CLIENT_ID")
    if not client_id:
        raise SystemExit(
            "Falta la variable de entorno JAMENDO_CLIENT_ID. Consíguela gratis en "
            "https://devportal.jamendo.com/ (Manage Apps → Add a new application)."
        )

    historial = cargar_json(RUTA_HISTORIAL, [])
    ya_descargadas = set(historial)
    atribucion = cargar_json(RUTA_ATRIBUCION, {})

    for emocion, tags in TAGS_POR_EMOCION.items():
        existentes = [
            f for f in os.listdir(BASE_DIR)
            if f.startswith(f"musica_{emocion}_") and f.endswith(".mp3")
        ]
        faltan = max(0, PISTAS_POR_EMOCION - len(existentes))
        if faltan == 0:
            logger.info(f"{emocion}: ya hay {len(existentes)} pista(s), no se descarga nada.")
            continue

        logger.info(f"{emocion}: buscando {faltan} pista(s) nueva(s) (tags: {tags})...")
        try:
            pistas = buscar_pistas(client_id, tags, faltan, ya_descargadas)
        except Exception as exc:
            logger.warning(f"{emocion}: fallo al consultar Jamendo: {exc}")
            continue

        for i, track in enumerate(pistas):
            nombre_archivo = f"musica_{emocion}_{limpiar_nombre_archivo(track.get('artist_name', 'na'))}_{track['id']}.mp3"
            destino = os.path.join(BASE_DIR, nombre_archivo)
            try:
                if descargar_pista(track, destino):
                    logger.info(f"  ✅ {nombre_archivo} ({track.get('artist_name')} - {track.get('name')})")
                    ya_descargadas.add(str(track["id"]))
                    atribucion[nombre_archivo] = {
                        "artista": track.get("artist_name"),
                        "titulo": track.get("name"),
                        "licencia_url": track.get("license_ccurl"),
                        "pagina_jamendo": track.get("shareurl"),
                    }
                else:
                    logger.warning(f"  ⚠️ Sin URL de descarga para track {track['id']}")
            except Exception as exc:
                logger.warning(f"  ⚠️ Fallo descargando track {track['id']}: {exc}")

    guardar_json(RUTA_HISTORIAL, sorted(ya_descargadas))
    guardar_json(RUTA_ATRIBUCION, atribucion)
    logger.info("Listo.")


if __name__ == "__main__":
    main()
