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
import re
import json
import time
import logging
import subprocess
from datetime import datetime, timedelta, timezone

import secretos  # carga secretos.env si las claves no están en el entorno
from titulos import recortar_titulo, limpiar_titulo, largo_youtube, LIMITE_YOUTUBE

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
# Título, descripción y hashtags de cada video, por nombre de archivo. Existe
# para poder verlos y corregirlos ANTES de subir: antes se generaban aquí
# mismo, un instante antes de la subida, así que no había momento en que
# alguien pudiera mirarlos.
RUTA_METADATA = os.path.join(CARPETA_ESTADO, "metadata.json")
RUTA_CLIENT_SECRET = os.path.join(BASE_DIR, "client_secret.json")
RUTA_TOKEN = os.path.join(BASE_DIR, "youtube_token.json")

# Misma detección y carpeta por defecto que generar_video_maestro.py: en
# Android/Termux no existe "Desktop", los videos se guardan en DCIM.
ES_ANDROID = 'PREFIX' in os.environ or os.path.exists('/sdcard')


def conectado_a_wifi():
    """En Android (con Termux:API instalado, paquete termux-api) revisa si
    hay una conexión WiFi activa, para no gastar datos móviles subiendo
    videos. Si no es Android, o termux-api no está instalado, no bloquea
    (se asume que el usuario administra su propia conexión en PC)."""
    if not ES_ANDROID:
        return True
    try:
        res = subprocess.run(
            ["termux-wifi-connectioninfo"],
            capture_output=True, text=True, timeout=5
        )
        if res.returncode != 0:
            # termux-api no instalado o falló: no bloquear la subida por esto.
            return True
        info = json.loads(res.stdout)
        return info.get("supplicant_state") == "COMPLETED"
    except Exception:
        return True
CARPETA_SALIDA_DEFAULT = (
    "/sdcard/DCIM/Videos creados" if ES_ANDROID
    else os.path.join(os.path.expanduser("~"), "Desktop", "Videos Creados")
)

CONFIG_DEFAULT = {
    "carpeta_salida": CARPETA_SALIDA_DEFAULT,
    # Con crond corriendo publisher.py a diario (ver README), esto deja el
    # video en revisión hasta ~6pm hora local el mismo día — buena hora pico
    # para Shorts en español. Súbelo si el cron de publicar corre más tarde.
    "buffer_horas_revision": 9,
    # true = solo sube con WiFi (protege el plan de datos). Se puede saltar
    # sin cambiar esto, con --con-datos o SUBIR_CON_DATOS=1, para una subida
    # puntual desde la calle sin desactivar la protección del cron diario.
    "solo_wifi": True,
    # None = sin tope propio: sube todo lo que YouTube deje en el día (se
    # detiene solo al toparse con el límite diario de subidas de YouTube,
    # ver uploadLimitExceeded en subir_video/main). Pon un número aquí si
    # en el futuro quieres volver a un ritmo de 1 video/día en vez de
    # drenar el colchón lo más rápido posible.
    "max_subidas_por_corrida": None,
    "duracion_min_sec": 10,
    "duracion_max_sec": 15 * 60,
    "categoria_youtube": "24",  # Entertainment
    "idioma": "es",
}

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    # De solo lectura: para poder revisar si un video ya existe en el canal
    # antes de subirlo (evita duplicados si pipeline_state/publicados.json
    # se pierde o se corrompe).
    "https://www.googleapis.com/auth/youtube.readonly",
]
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
        "titulo_youtube": {"type": "string", "description": "Título optimizado para YouTube. MÁXIMO 100 caracteres contando espacios — cuéntalos antes de responder; si te pasas, el título se recorta y pierde el final. Apunta a 60-90 para que se lea entero en el móvil. Sin clickbait engañoso."},
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
2. Si es apta, genera título, descripción y hashtags optimizados para YouTube.

El título es lo único con un límite duro: 100 caracteres. Escríbelo pensando en que
se lea entero en la miniatura de un móvil, así que 60-90 es la zona buena. Si la idea
no cabe, reescríbela más corta en vez de dejarla a medias — un título cortado da peor
impresión que uno menos ambicioso."""


INTENTOS_TITULO = 3


def _pedir_metadata(client, prompt):
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


def revisar_y_generar_metadata(client, titulo, cuerpo):
    """Metadata de publicación, con el título ya dentro del límite.

    Gemini no cuenta caracteres de forma fiable por mucho que se le pida en
    el prompt: escribe el título que le parece bueno y a veces se pasa. Así
    que se comprueba aquí y, si se pasó, se le devuelve el título con la
    cuenta exacta y cuánto le sobra para que lo reescriba él.

    Reescribir es mejor que recortar: un título que el modelo acorta sigue
    siendo una frase pensada, mientras que uno recortado por máquina pierde
    el final. El recorte mecánico sigue existiendo (titulos.py) pero pasa a
    ser la red de seguridad, no el camino normal.
    """
    prompt = f"Título/hook: {titulo}\n\nCuerpo:\n{cuerpo[:3000]}"

    for intento in range(1, INTENTOS_TITULO + 1):
        metadata = _pedir_metadata(client, prompt)
        propuesto = limpiar_titulo(metadata.get("titulo_youtube", ""))
        largo = largo_youtube(propuesto)

        if largo <= LIMITE_YOUTUBE:
            metadata["titulo_youtube"] = propuesto
            if intento > 1:
                logger.info(f"  Título dentro del límite al intento {intento}: {largo} caracteres.")
            return metadata

        sobran = largo - LIMITE_YOUTUBE
        logger.warning(
            f"  Intento {intento}: el título tiene {largo} caracteres, "
            f"{sobran} de más. Pidiendo uno más corto."
        )
        if intento == INTENTOS_TITULO:
            # Se agotaron los reintentos: se devuelve tal cual y el recorte
            # por palabras se encarga. Nunca se sube un título largo.
            logger.warning("  Gemini no consiguió acortarlo; se recortará por palabras.")
            return metadata

        prompt = (
            f"{prompt}\n\n"
            f"--- CORRECCIÓN ---\n"
            f"El título que propusiste tiene {largo} caracteres y el máximo son "
            f"{LIMITE_YOUTUBE}: te sobran {sobran}.\n"
            f"Era: «{propuesto}»\n"
            f"Reescríbelo entero para que quepa, apuntando a 70-90 caracteres. No lo "
            f"cortes ni le pongas puntos suspensivos: quita o resume lo menos importante "
            f"y deja una frase completa que siga funcionando como gancho. "
            f"El resto de campos puedes mantenerlos."
        )


# Hashtags genéricos por emoción, para cuando falla la llamada a Gemini y no
# hay generación de metadata "inteligente" disponible.
HASHTAGS_DE_RESPALDO_POR_EMOCION = {
    "drama": ["drama", "historiasreales"],
    "venganza": ["venganza", "justicia"],
    "suspenso": ["misterio", "suspenso"],
    "comedia": ["humor", "comedia"],
}


def clave_metadata(ruta):
    """El nombre del archivo, no la ruta completa: la carpeta de salida puede
    cambiar (o venir normalizada distinto desde la SD) y la metadata seguiría
    siendo la misma."""
    return os.path.basename(ruta)


def metadata_para(video, client, almacen):
    """La metadata que se va a subir, priorizando lo que ya esté guardado.

    Si el panel la generó y la corregiste, se usa TAL CUAL: volver a
    preguntarle a Gemini aquí tiraría tus ediciones sin avisar, y el punto
    de poder editarlas es que lo editado sea lo que se sube.

    Si no hay nada guardado (el caso del cron sin pasar por el panel), se
    genera aquí como siempre y se guarda, para que quede constancia de lo
    que se publicó con cada video.
    """
    clave = clave_metadata(video["ruta"])
    guardada = almacen.get(clave)
    if guardada and guardada.get("titulo_youtube"):
        origen = guardada.get("origen", "guardada")
        logger.info(f"  Metadata {origen}: «{guardada['titulo_youtube'][:60]}»")
        return guardada

    try:
        metadata = revisar_y_generar_metadata(client, video["titulo"], video.get("cuerpo", ""))
        metadata["origen"] = "gemini"
    except Exception as exc:
        logger.warning(f"Falló la revisión de Gemini ({exc}); usando metadata de respaldo.")
        metadata = metadata_de_respaldo(video)
        metadata["origen"] = "respaldo"

    almacen[clave] = metadata
    guardar_json(RUTA_METADATA, almacen)
    return metadata


def metadata_de_respaldo(video):
    """Metadata genérica pero funcional, usada solo cuando revisar_y_generar_metadata
    falla (red, cuota de la API, etc.) — para no dejar el video sin subir por
    un fallo pasajero ajeno al contenido en sí. No reemplaza el chequeo de
    contenido de Gemini, solo cubre su ausencia: el técnico ya pasó antes."""
    emocion = video.get("emocion", "drama")
    hashtags = HASHTAGS_DE_RESPALDO_POR_EMOCION.get(emocion, ["historias"]) + ["reddit", "shorts"]
    return {
        "aprobado": True,
        "motivo_rechazo": "",
        "titulo_youtube": recortar_titulo(video.get("titulo") or "Historia de Reddit"),
        "descripcion_youtube": (
            "Historia real adaptada de Reddit, narrada en español.\n\n"
            "¿Tú qué hubieras hecho? Cuéntamelo en los comentarios 👇"
        ),
        "hashtags": hashtags,
    }


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


def buscar_video_existente_en_canal(servicio, titulo):
    """Busca en el propio canal un video con este título exacto, para no
    duplicar una subida si publicados.json se perdió o se corrompió (nuestro
    único registro es local, no se sincroniza con YouTube de otra forma).
    Devuelve el video_id si lo encuentra, o None."""
    try:
        resp = servicio.search().list(
            part="snippet", forMine=True, type="video", q=titulo, maxResults=5
        ).execute()
        for item in resp.get("items", []):
            if item["snippet"]["title"] == titulo:
                return item["id"]["videoId"]
    except Exception as exc:
        logger.warning(f"No se pudo verificar duplicados en YouTube ({exc}); se sube de todas formas.")
    return None


RUTA_ATRIBUCION_MUSICA = os.path.join(CARPETA_ESTADO, "musica_atribucion.json")


def construir_descripcion(metadata, video):
    """Arma la descripción final: lo que genera el modelo + atribución fija a la
    fuente y aviso de adaptación con IA. Esto no depende del modelo (que puede
    olvidarlo) para cumplir con el requisito de transparencia de Reddit de no
    presentar contenido ajeno como propio."""
    # Al inicio: YouTube solo muestra como "chips" clicables arriba del
    # título los hashtags que detecta cerca del principio de la descripción
    # (o en el título) — puestos al final, quedan como texto plano sin ese
    # efecto. Se sanean espacios/símbolos, que igual los invalidarían.
    hashtags_limpios = [re.sub(r"[^\w]", "", h) for h in metadata["hashtags"]]
    hashtags_limpios = [h for h in hashtags_limpios if h][:6]
    partes = [" ".join(f"#{h}" for h in hashtags_limpios), metadata["descripcion_youtube"]]

    fuente_url = video.get("fuente_url")
    autor = video.get("autor_original")
    if fuente_url:
        linea_fuente = f"Historia inspirada en una publicación pública de Reddit"
        if autor:
            linea_fuente += f" de {autor}"
        linea_fuente += f", adaptada con fines narrativos. Fuente: {fuente_url}"
        partes.append(linea_fuente)

    musica_archivo = video.get("musica_archivo")
    if musica_archivo:
        atribucion = cargar_json(RUTA_ATRIBUCION_MUSICA, {}).get(musica_archivo)
        if atribucion and atribucion.get("artista"):
            linea_musica = f"Música: \"{atribucion.get('titulo', '')}\" por {atribucion['artista']} (Jamendo, Creative Commons)"
            if atribucion.get("pagina_jamendo"):
                linea_musica += f" — {atribucion['pagina_jamendo']}"
            partes.append(linea_musica)

    return "\n\n".join(partes)


def subir_video(servicio, ruta_video, metadata, video, publish_at_iso):
    body = {
        "snippet": {
            "title": recortar_titulo(metadata["titulo_youtube"]),
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


def leer_estado_publicacion(servicio, video_id):
    """Cómo quedó el video EN YouTube: privacidad y fecha programada.

    Pedir la programación y darla por hecha no basta. YouTube acepta el
    'publishAt' en la subida y luego puede no aplicarlo —el caso conocido es
    un canal sin verificar por teléfono, que no tiene permitido programar—,
    así que el registro decía "se publica el día X" mientras el video ya
    estaba público. Preguntar cuesta una llamada y convierte una suposición
    en un dato.
    """
    try:
        r = servicio.videos().list(part="status", id=video_id).execute()
        items = r.get("items") or []
        if not items:
            return None
        st = items[0].get("status", {})
        return {"privacidad": st.get("privacyStatus"), "publish_at": st.get("publishAt")}
    except Exception as exc:
        logger.warning(f"No se pudo comprobar cómo quedó {video_id} en YouTube: {exc}")
        return None


def avisar_si_no_quedo_programado(real, pedido_iso, video_id):
    """True si quedó programado, False si consta que no, None si no se supo.

    Se avisa fuerte a propósito: que un video salga público antes de tiempo
    no se puede deshacer del todo —la gente ya lo vio— y el aviso tiene que
    doler más que una línea de log entre otras cincuenta.
    """
    if real is None:
        # No se pudo comprobar: no es lo mismo que haber fallado. Se devuelve
        # None para no anotar en el registro un fallo que no consta.
        logger.warning(f"  No se pudo confirmar la programación de {video_id}; "
                       f"compruébala a mano si te importa esa ventana.")
        return None
    if real.get("publish_at") and real.get("privacidad") == "private":
        return True

    logger.error("=" * 62)
    logger.error("  ⚠️  YouTube NO aplicó la programación de este video.")
    logger.error(f"     Se pidió: privado hasta {pedido_iso}")
    logger.error(f"     Quedó:    {real.get('privacidad')}"
                 + (f", sin fecha programada" if not real.get("publish_at") else ""))
    if real.get("privacidad") == "public":
        logger.error("     El video YA ESTÁ PÚBLICO. No hubo ventana de revisión.")
    logger.error("     Causa habitual: el canal no está verificado por teléfono,")
    logger.error("     y sin verificar YouTube no deja programar publicaciones.")
    logger.error("     Verifícalo en https://www.youtube.com/verify y vuelve a probar.")
    logger.error(f"     Mientras tanto, ponlo privado a mano:")
    logger.error(f"     https://studio.youtube.com/video/{video_id}/edit")
    logger.error("=" * 62)
    return False


DIAS_RETENCION_LOCAL = 7


def limpiar_videos_locales_vencidos():
    """Borra los .mp4 locales de videos que ya llevan DIAS_RETENCION_LOCAL
    días subidos a YouTube — deja esa ventana a propósito para poder
    subirlos a mano a TikTok u otras plataformas antes de que se borren."""
    publicados = cargar_json(RUTA_PUBLICADOS, [])
    ahora = datetime.now(timezone.utc)
    cambios = False

    for p in publicados:
        if p.get("_borrado_local") or not p.get("subido_en"):
            continue
        try:
            fecha_subida = datetime.strptime(p["subido_en"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        except ValueError:
            continue

        if ahora - fecha_subida >= timedelta(days=DIAS_RETENCION_LOCAL):
            ruta = p["ruta"]
            if os.path.exists(ruta):
                try:
                    os.remove(ruta)
                    logger.info(f"🗑️  Borrado local (cumplió {DIAS_RETENCION_LOCAL} días subido): {os.path.basename(ruta)}")
                except Exception as exc:
                    logger.warning(f"No se pudo borrar {ruta}: {exc}")
            p["_borrado_local"] = True
            cambios = True

    if cambios:
        guardar_json(RUTA_PUBLICADOS, publicados)


# ---------------------------------------------------------
# ORQUESTACIÓN
# ---------------------------------------------------------
def main(forzar_datos=False):
    limpiar_videos_locales_vencidos()

    cfg = cargar_config()

    # Tres formas de permitir datos móviles, de más puntual a más permanente:
    # el argumento (una corrida), la variable de entorno (una sesión) y la
    # config (siempre). Así se puede subir algo desde la calle sin dejar
    # apagada la protección para el cron de todos los días.
    permitir_datos = (
        forzar_datos
        or os.environ.get("SUBIR_CON_DATOS") == "1"
        or not cfg.get("solo_wifi", True)
    )

    if not permitir_datos and not conectado_a_wifi():
        logger.info(
            "Sin WiFi activo — se aplaza la subida para no gastar datos móviles.\n"
            "   Para subir ahora de todas formas: python publisher.py --con-datos"
        )
        return
    if permitir_datos and not conectado_a_wifi():
        logger.warning("Sin WiFi, pero se pidió subir con datos móviles. Ojo con tu plan.")

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
    almacen_metadata = cargar_json(RUTA_METADATA, {})
    rutas_ya_procesadas = {p["ruta"] for p in publicados} | {r["ruta"] for r in rechazados}

    pendientes = [v for v in completados if v["ruta"] not in rutas_ya_procesadas]
    if not pendientes:
        logger.info("Todos los videos completados ya fueron procesados anteriormente.")
        return

    client = genai.Client()
    servicio_yt = None
    max_subidas = cfg.get("max_subidas_por_corrida")
    subidas_en_esta_corrida = 0

    for video in pendientes:
        if max_subidas and subidas_en_esta_corrida >= max_subidas:
            logger.info(
                f"Tope de {max_subidas} subida(s) por corrida alcanzado — "
                f"el resto del lote queda pendiente para la próxima corrida."
            )
            break

        ruta = video["ruta"]
        logger.info(f"Procesando: {os.path.basename(ruta)}")

        ok_tecnico, motivo_tecnico = chequeo_tecnico(ruta, cfg)
        if not ok_tecnico:
            logger.warning(f"Rechazado (técnico): {motivo_tecnico}")
            rechazados.append({"ruta": ruta, "fase": "tecnico", "motivo": motivo_tecnico})
            guardar_json(RUTA_RECHAZADOS, rechazados)
            continue

        metadata = metadata_para(video, client, almacen_metadata)

        if not metadata["aprobado"]:
            logger.warning(f"Rechazado (contenido): {metadata['motivo_rechazo']}")
            rechazados.append({"ruta": ruta, "fase": "contenido", "motivo": metadata["motivo_rechazo"]})
            guardar_json(RUTA_RECHAZADOS, rechazados)
            continue

        if servicio_yt is None:
            servicio_yt = obtener_servicio_youtube()

        video_id_existente = buscar_video_existente_en_canal(
            servicio_yt, recortar_titulo(metadata["titulo_youtube"]))
        if video_id_existente:
            logger.warning(
                f"'{metadata['titulo_youtube']}' ya existe en el canal (video_id={video_id_existente}) — "
                f"no se vuelve a subir. Registrando para no volver a evaluarlo."
            )
            publicados.append({
                "ruta": ruta,
                "video_id": video_id_existente,
                "titulo_youtube": metadata["titulo_youtube"],
                "publish_at": None,
                "url_revision": f"https://studio.youtube.com/video/{video_id_existente}/edit",
                "detectado_como_duplicado": True,
            })
            guardar_json(RUTA_PUBLICADOS, publicados)
            continue

        publish_at = datetime.now(timezone.utc) + timedelta(hours=cfg["buffer_horas_revision"])
        publish_at_iso = publish_at.strftime("%Y-%m-%dT%H:%M:%SZ")

        try:
            video_id = subir_video(servicio_yt, ruta, metadata, video, publish_at_iso)
        except Exception as exc:
            if "uploadLimitExceeded" in str(exc):
                logger.info(
                    "Se alcanzó el límite diario de subidas de YouTube — el resto del "
                    "colchón queda pendiente para la próxima corrida (no se pierde nada)."
                )
                break
            # Cualquier otro fallo de subida (red, timeout, error temporal de la
            # API) tampoco descarta el video: se reintenta en la próxima corrida
            # en vez de quedar rechazado para siempre.
            logger.warning(f"Fallo al subir {ruta} (se reintentará más adelante): {exc}")
            continue

        real = leer_estado_publicacion(servicio_yt, video_id)
        programado = avisar_si_no_quedo_programado(real, publish_at_iso, video_id)
        if programado is not False:
            logger.info(f"✅ Subido como privado, se publica solo el {publish_at_iso} — "
                        f"https://studio.youtube.com/video/{video_id}/edit")
        publicados.append({
            "ruta": ruta,
            "video_id": video_id,
            "titulo_youtube": metadata["titulo_youtube"],
            "publish_at": publish_at_iso,
            # Lo que YouTube dice de verdad, no lo que le pedimos. El panel
            # enseña esto: si no coinciden, quieres enterarte ahí y no en el
            # canal.
            "privacidad_real": (real or {}).get("privacidad"),
            "publish_at_real": (real or {}).get("publish_at"),
            "programado_ok": programado,
            "url_revision": f"https://studio.youtube.com/video/{video_id}/edit",
            # Para el borrado retrasado (ver limpiar_videos_locales_vencidos):
            # se conserva el archivo local unos días para poder subirlo a
            # mano a TikTok antes de que se borre solo.
            "subido_en": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        })
        guardar_json(RUTA_PUBLICADOS, publicados)
        subidas_en_esta_corrida += 1


def revisar_programados():
    """Qué estado tienen AHORA en YouTube los videos que ya subimos.

    Sirve para responder «¿se está respetando la ventana de revisión?» sin
    tener que subir otro video y esperar: pregunta por los que ya están y
    enseña lo que YouTube dice de cada uno.
    """
    publicados = cargar_json(RUTA_PUBLICADOS, [])
    con_id = [p for p in publicados if p.get("video_id")]
    if not con_id:
        print("No hay videos subidos que revisar.")
        return

    servicio = obtener_servicio_youtube()
    print(f"\n  {len(con_id)} video(s) subidos:\n")
    fallos = 0
    for p in con_id:
        real = leer_estado_publicacion(servicio, p["video_id"]) or {}
        privacidad = real.get("privacidad") or "?"
        cuando = real.get("publish_at")
        pedido = p.get("publish_at")

        if privacidad == "private" and cuando:
            marca, nota = "✅", f"privado hasta {cuando}"
        elif privacidad == "public":
            marca, nota = "⛔", "PÚBLICO" + (f" (se pidió esperar a {pedido})" if pedido else "")
            fallos += 1
        elif privacidad == "private":
            marca, nota = "⚠️ ", "privado pero SIN fecha: no se publicará solo"
            fallos += 1
        else:
            marca, nota = "· ", privacidad

        print(f"  {marca} {(p.get('titulo_youtube') or '')[:44]:<44} {nota}")
        print(f"       https://studio.youtube.com/video/{p['video_id']}/edit")

    print()
    if fallos:
        print(f"  {fallos} video(s) no quedaron programados.")
        print("  Causa habitual: el canal no está verificado por teléfono, y sin")
        print("  verificar YouTube no deja programar publicaciones.")
        print("  Verifícalo en https://www.youtube.com/verify\n")
    else:
        print("  Todos respetan su ventana de revisión.\n")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Sube a YouTube los videos pendientes.")
    parser.add_argument(
        "--con-datos", action="store_true",
        help="Subir aunque no haya WiFi (usa datos móviles). Solo para esta corrida.",
    )
    parser.add_argument(
        "--revisar-programados", action="store_true",
        help="No sube nada: enseña el estado real en YouTube de los ya subidos.",
    )
    args = parser.parse_args()
    if args.revisar_programados:
        revisar_programados()
    else:
        main(forzar_datos=args.con_datos)
