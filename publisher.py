"""
Publisher — sube los videos completados a YouTube con un gate de calidad
y una ventana de revisión antes de que se vuelvan públicos.

Flujo por video:
  1. Chequeo técnico (ffprobe): duración razonable, audio presente, archivo válido.
  2. Chequeo de contenido (Gemini, capa gratuita): detecta clickbait engañoso,
     texto roto o contenido inapropiado en título/descripción antes de subir.
  3. Si pasa ambos: sube como privado con publishAt = ahora + BUFFER_HORAS.
     Tienes esa ventana para revisar/cancelar en YouTube Studio antes de que
     se publique solo.
  4. Si falla algún chequeo: no sube, queda en pipeline_state/rechazados.json.

Requiere:
  pip install google-api-python-client google-auth-oauthlib google-genai
Credenciales:
  - client_secret.json (OAuth de Google, para subir a YouTube) junto a este script.
  - GEMINI_API_KEY como variable de entorno (gratis en https://aistudio.google.com/apikey).
"""
import os
import json
import time
import logging
import subprocess
from datetime import datetime, timedelta, timezone

from google import genai
from google.genai import types as genai_types
from google.genai import errors as genai_errors
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CARPETA_ESTADO = os.path.join(BASE_DIR, "pipeline_state")
RUTA_PUBLICADOS = os.path.join(CARPETA_ESTADO, "publicados.json")
RUTA_RECHAZADOS = os.path.join(CARPETA_ESTADO, "rechazados.json")
RUTA_CLIENT_SECRET = os.path.join(BASE_DIR, "client_secret.json")
RUTA_TOKEN = os.path.join(BASE_DIR, "youtube_token.json")

CONFIG_DEFAULT = {
    "carpeta_salida": os.path.join(os.path.expanduser("~"), "Desktop", "Videos Creados"),
    "buffer_horas_revision": 12,
    "duracion_min_sec": 10,
    "duracion_max_sec": 15 * 60,
    "categoria_youtube": "24",  # Entertainment
    "idioma": "es",
}

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
MODEL = "gemini-3.5-flash-lite"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("publisher")


def cargar_config(ruta=os.path.join(BASE_DIR, "config.json")):
    cfg = dict(CONFIG_DEFAULT)
    if os.path.exists(ruta):
        with open(ruta, "r", encoding="utf-8") as f:
            cfg.update(json.load(f))
    return cfg


def cargar_json(ruta, default):
    if os.path.exists(ruta):
        with open(ruta, "r", encoding="utf-8") as f:
            return json.load(f)
    return default


def guardar_json(ruta, data):
    os.makedirs(os.path.dirname(ruta), exist_ok=True)
    with open(ruta, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------
# FASE 1: chequeo técnico
# ---------------------------------------------------------
def chequeo_tecnico(ruta_video, cfg):
    if not os.path.isfile(ruta_video) or os.path.getsize(ruta_video) == 0:
        return False, "Archivo de video inexistente o vacío."

    try:
        res = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type",
             "-of", "json", ruta_video],
            capture_output=True, text=True, timeout=15,
        )
        data = json.loads(res.stdout)
    except Exception as exc:
        return False, f"ffprobe falló: {exc}"

    duracion = float(data.get("format", {}).get("duration", 0))
    if not (cfg["duracion_min_sec"] <= duracion <= cfg["duracion_max_sec"]):
        return False, f"Duración fuera de rango: {duracion:.1f}s"

    tipos_stream = {s.get("codec_type") for s in data.get("streams", [])}
    if "audio" not in tipos_stream:
        return False, "El video no tiene pista de audio."
    if "video" not in tipos_stream:
        return False, "El video no tiene pista de video."

    return True, "OK"


# ---------------------------------------------------------
# FASE 2: chequeo de contenido + generación de metadata (Claude)
# ---------------------------------------------------------
SCHEMA_METADATA = {
    "type": "object",
    "properties": {
        "aprobado": {"type": "boolean", "description": "False si el contenido es clickbait engañoso, inapropiado, o el texto está roto/incoherente."},
        "motivo_rechazo": {"type": "string", "description": "Si aprobado=false, explica por qué. Si aprobado=true, cadena vacía."},
        "titulo_youtube": {"type": "string", "description": "Título optimizado para YouTube, máx 100 caracteres, sin clickbait engañoso."},
        "descripcion_youtube": {"type": "string", "description": "Descripción de 2-4 líneas con hashtags relevantes al final."},
        "hashtags": {"type": "array", "items": {"type": "string"}, "description": "3 a 6 hashtags sin el símbolo #."},
    },
    "required": ["aprobado", "motivo_rechazo", "titulo_youtube", "descripcion_youtube", "hashtags"],
}

SYSTEM_REVISOR = """Eres un revisor de calidad y editor de metadata para un canal de YouTube Shorts
de historias narradas en español. Recibes el título/hook y el cuerpo de una historia ya usada
para generar un video, y debes:

1. Decidir si es apta para publicar: rechaza SOLO si el texto está roto/incoherente,
   es clickbait manifiestamente engañoso respecto al contenido, o incluye contenido
   inapropiado (odio, sexual explícito, violencia gráfica gratuita). Historias de
   drama/venganza/conflicto normales SÍ son aptas, es el género del canal.
2. Si es apta, genera título, descripción y hashtags optimizados para YouTube."""


def revisar_y_generar_metadata(client, titulo, cuerpo):
    prompt = f"Título/hook: {titulo}\n\nCuerpo:\n{cuerpo[:3000]}"
    response = client.models.generate_content(
        model=MODEL,
        contents=prompt,
        config=genai_types.GenerateContentConfig(
            system_instruction=SYSTEM_REVISOR,
            response_mime_type="application/json",
            response_schema=SCHEMA_METADATA,
        ),
    )
    return json.loads(response.text)


# ---------------------------------------------------------
# FASE 3: subida a YouTube
# ---------------------------------------------------------
def obtener_servicio_youtube():
    creds = None
    if os.path.exists(RUTA_TOKEN):
        creds = Credentials.from_authorized_user_file(RUTA_TOKEN, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(RUTA_CLIENT_SECRET):
                raise RuntimeError(
                    f"Falta {RUTA_CLIENT_SECRET}. Descárgalo desde Google Cloud Console "
                    "(OAuth client ID tipo 'Desktop app')."
                )
            flow = InstalledAppFlow.from_client_secrets_file(RUTA_CLIENT_SECRET, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(RUTA_TOKEN, "w", encoding="utf-8") as f:
            f.write(creds.to_json())

    return build("youtube", "v3", credentials=creds)


def construir_descripcion(metadata, video):
    """Arma la descripción final: lo que genera el modelo + atribución fija a la
    fuente y aviso de adaptación con IA. Esto no depende del modelo (que puede
    olvidarlo) para cumplir con el requisito de transparencia de Reddit de no
    presentar contenido ajeno como propio."""
    partes = [metadata["descripcion_youtube"]]

    fuente_url = video.get("fuente_url")
    autor = video.get("autor_original")
    if fuente_url:
        linea_fuente = f"Historia inspirada en una publicación pública de Reddit"
        if autor:
            linea_fuente += f" de {autor}"
        linea_fuente += f", adaptada con fines narrativos. Fuente: {fuente_url}"
        partes.append(linea_fuente)

    partes.append(" ".join(f"#{h}" for h in metadata["hashtags"]))
    return "\n\n".join(partes)


def subir_video(servicio, ruta_video, metadata, video, publish_at_iso):
    body = {
        "snippet": {
            "title": metadata["titulo_youtube"][:100],
            "description": construir_descripcion(metadata, video),
            "tags": metadata["hashtags"],
            "categoryId": "24",
        },
        "status": {
            "privacyStatus": "private",
            "publishAt": publish_at_iso,
            "selfDeclaredMadeForKids": False,
        },
    }
    media = MediaFileUpload(ruta_video, chunksize=-1, resumable=True, mimetype="video/mp4")
    request = servicio.videos().insert(part="snippet,status", body=body, media_body=media)

    respuesta = None
    while respuesta is None:
        status, respuesta = request.next_chunk()
        if status:
            logger.info(f"Subiendo {os.path.basename(ruta_video)}: {int(status.progress() * 100)}%")

    return respuesta["id"]


# ---------------------------------------------------------
# ORQUESTACIÓN
# ---------------------------------------------------------
def main():
    cfg = cargar_config()
    ruta_resultado = os.path.join(cfg["carpeta_salida"], "resultado_lote.json")

    if not os.path.exists(ruta_resultado):
        logger.error(f"No se encontró {ruta_resultado}. Corre generar_video_maestro.py primero.")
        return

    with open(ruta_resultado, "r", encoding="utf-8") as f:
        lote = json.load(f)

    completados = lote.get("completados", [])
    if not completados:
        logger.info("No hay videos completados para publicar.")
        return

    publicados = cargar_json(RUTA_PUBLICADOS, [])
    rechazados = cargar_json(RUTA_RECHAZADOS, [])
    rutas_ya_procesadas = {p["ruta"] for p in publicados} | {r["ruta"] for r in rechazados}

    pendientes = [v for v in completados if v["ruta"] not in rutas_ya_procesadas]
    if not pendientes:
        logger.info("Todos los videos completados ya fueron procesados anteriormente.")
        return

    client = genai.Client()
    servicio_yt = None

    for video in pendientes:
        ruta = video["ruta"]
        logger.info(f"Procesando: {os.path.basename(ruta)}")

        ok_tecnico, motivo_tecnico = chequeo_tecnico(ruta, cfg)
        if not ok_tecnico:
            logger.warning(f"Rechazado (técnico): {motivo_tecnico}")
            rechazados.append({"ruta": ruta, "fase": "tecnico", "motivo": motivo_tecnico})
            guardar_json(RUTA_RECHAZADOS, rechazados)
            continue

        try:
            metadata = revisar_y_generar_metadata(client, video["titulo"], video.get("cuerpo", ""))
        except Exception as exc:
            logger.warning(f"Rechazado (error de revisión): {exc}")
            rechazados.append({"ruta": ruta, "fase": "revision", "motivo": str(exc)})
            guardar_json(RUTA_RECHAZADOS, rechazados)
            continue

        if not metadata["aprobado"]:
            logger.warning(f"Rechazado (contenido): {metadata['motivo_rechazo']}")
            rechazados.append({"ruta": ruta, "fase": "contenido", "motivo": metadata["motivo_rechazo"]})
            guardar_json(RUTA_RECHAZADOS, rechazados)
            continue

        if servicio_yt is None:
            servicio_yt = obtener_servicio_youtube()

        publish_at = datetime.now(timezone.utc) + timedelta(hours=cfg["buffer_horas_revision"])
        publish_at_iso = publish_at.strftime("%Y-%m-%dT%H:%M:%SZ")

        try:
            video_id = subir_video(servicio_yt, ruta, metadata, video, publish_at_iso)
        except Exception as exc:
            logger.error(f"Fallo al subir {ruta}: {exc}")
            rechazados.append({"ruta": ruta, "fase": "subida", "motivo": str(exc)})
            guardar_json(RUTA_RECHAZADOS, rechazados)
            continue

        logger.info(f"✅ Subido como privado, se publica solo el {publish_at_iso} — https://studio.youtube.com/video/{video_id}/edit")
        publicados.append({
            "ruta": ruta,
            "video_id": video_id,
            "titulo_youtube": metadata["titulo_youtube"],
            "publish_at": publish_at_iso,
            "url_revision": f"https://studio.youtube.com/video/{video_id}/edit",
        })
        guardar_json(RUTA_PUBLICADOS, publicados)


if __name__ == "__main__":
    main()
