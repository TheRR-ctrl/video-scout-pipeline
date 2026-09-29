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
  pip install google-api-python-client google-auth-oauthlib requests
Credenciales:
  - client_secret.json (OAuth de Google, para subir a YouTube) junto a este script.
  - GEMINI_API_KEY como variable de entorno (gratis en https://aistudio.google.com/apikey).
"""
import os
import re
import json
import time
import logging
import unicodedata
import subprocess
from datetime import datetime, timedelta, timezone

import almacen   # leer y escribir los .json de estado
import secretos  # carga secretos.env si las claves no están en el entorno
import ruido     # calla los avisos del SDK de Google que aqui no dicen nada
from titulos import (recortar_titulo, limpiar_titulo, largo_youtube, LIMITE_YOUTUBE,
                     parte_de_titulo, con_parte, sin_marca_de_parte)

import gemini as genai
from gemini import types as genai_types
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
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
# La subida a medias: la URL de la sesión de YouTube, para retomarla desde el
# último trozo que llegó si el proceso muere (Android lo mata, se va la luz)
# en vez de volver a mandar el archivo entero. Quien tiene esa URL puede
# terminar la subida sin más credenciales, así que va en 600 como el token.
RUTA_SUBIDA_EN_CURSO = os.path.join(CARPETA_ESTADO, "subida_en_curso.json")
# Videos que YouTube dio por fallidos y no se pudieron borrar (el token no
# tenía permiso): la búsqueda de duplicados los salta, o el video bueno no se
# volvería a subir nunca porque "ya hay uno con ese título".
RUTA_SUBIDAS_ROTAS = os.path.join(CARPETA_ESTADO, "subidas_rotas.json")
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
    # Días que un video ya subido a YouTube se queda en el teléfono mientras
    # no esté en TikTok, para que dé tiempo a pasarlo. Los que ya están en
    # TikTok se borran a los DIAS_RETENCION_LOCAL. Se cambia en Ajustes →
    # Automático.
    "dias_espera_tiktok": 14,
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
    # Cada corrida del pipeline le enseña UN video a Gemini para que diga qué
    # falla (calidad_ia.py). Va por fotogramas, así que son unos pocos miles
    # de tokens y menos de un megabyte; aun así, se salta sin wifi. Ponlo en
    # false para que solo pase cuando lo pidas tú desde el panel.
    "calidad_ia_automatica": True,
    # Despues de cada tanda de renders, cambiar por otras las pistas de musica
    # que ya sonaron (actualizar_musica.py --rotar). Sin esto la biblioteca se
    # queda fija y las mismas canciones se reparten entre todos los videos.
    # Se salta sola sin wifi y sin JAMENDO_CLIENT_ID.
    "musica_rotacion_automatica": True,
    "duracion_min_sec": 10,
    "duracion_max_sec": 15 * 60,
    "categoria_youtube": "24",  # Entertainment
    "idioma": "es",
}

# Lo que se le PIDE a Google al generar un token nuevo. Un token que ya
# existe se usa con los permisos que tenga (ver obtener_servicio_youtube):
# añadir algo aquí no invalida el token de nadie, solo hace que el siguiente
# que se genere venga con el permiso nuevo.
SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    # De solo lectura: para poder revisar si un video ya existe en el canal
    # antes de subirlo (evita duplicados si pipeline_state/publicados.json
    # se pierde o se corrompe).
    "https://www.googleapis.com/auth/youtube.readonly",
    # Para borrar del canal (videos.delete), que es lo que necesitan
    # relanzar.py y rehacer_todo.py. Con solo "upload" la API responde 403.
    "https://www.googleapis.com/auth/youtube.force-ssl",
]
MODEL = "gemini-3.5-flash-lite"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
ruido.callar_sdk_google()   # los avisos de AFC del SDK, que aqui no aplican
logger = logging.getLogger("publisher")


def cargar_config(ruta=os.path.join(BASE_DIR, "config.json")):
    cfg = dict(CONFIG_DEFAULT)
    if os.path.exists(ruta):
        with open(ruta, "r", encoding="utf-8") as f:
            cfg.update(json.load(f))
    return cfg


# Los otros scripts llaman a publisher.cargar_json / publisher.guardar_json
# desde antes de que existiera almacen.py; se quedan como el nombre de aquí.
cargar_json = almacen.cargar
guardar_json = almacen.guardar


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
    limite = LIMITE_YOUTUBE

    # Una historia partida (ver partir_historias.py): sin avisar, una parte
    # que empieza o acaba a mitad puede parecerle un texto roto. Y el
    # "(Parte N/M)" lo pone con_parte al final, no Gemini, así que su título
    # tiene que dejarle sitio.
    parte = parte_de_titulo(titulo)
    if parte:
        limite -= largo_youtube(f"(Parte {parte[0]}/{parte[1]})") + 1
        prompt = (
            f"Es la parte {parte[0]} de {parte[1]} de una historia publicada en varios "
            f"shorts seguidos: que empiece o acabe a mitad es a propósito, no un texto "
            f"roto. No pongas «Parte» en el título: se añade solo, y el título entero "
            f"no puede pasar de {limite} caracteres.\n\n{prompt}"
        )

    for intento in range(1, INTENTOS_TITULO + 1):
        metadata = _pedir_metadata(client, prompt)
        propuesto = limpiar_titulo(metadata.get("titulo_youtube", ""))
        largo = largo_youtube(propuesto)

        if largo <= limite:
            metadata["titulo_youtube"] = con_parte(propuesto, parte)
            if intento > 1:
                logger.info(f"  Título dentro del límite al intento {intento}: {largo} caracteres.")
            return metadata

        sobran = largo - limite
        logger.warning(
            f"  Intento {intento}: el título tiene {largo} caracteres, "
            f"{sobran} de más. Pidiendo uno más corto."
        )
        if intento == INTENTOS_TITULO:
            # Se agotaron los reintentos: se devuelve tal cual y el recorte
            # por palabras se encarga. Nunca se sube un título largo.
            logger.warning("  Gemini no consiguió acortarlo; se recortará por palabras.")
            if parte:
                metadata["titulo_youtube"] = con_parte(propuesto, parte)
            return metadata

        prompt = (
            f"{prompt}\n\n"
            f"--- CORRECCIÓN ---\n"
            f"El título que propusiste tiene {largo} caracteres y el máximo son "
            f"{limite}: te sobran {sobran}.\n"
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


def en_orden_de_serie(videos):
    """Las partes de una misma historia en su orden, sin mover nada más.

    Se sube por número de historia, y el número es la posición en guion.txt,
    que cambia con cada limpieza de la cola. Si la parte 1 se graba hoy y una
    limpieza renumera antes de grabar las otras dos, esas pueden quedar con
    un número menor y subirse antes. Aquí cada serie se reordena dentro de
    los huecos que ya ocupaba, y todo lo demás se queda donde estaba.
    """
    huecos = {}
    for pos, v in enumerate(videos):
        parte = parte_de_titulo(v.get("titulo"))
        if parte:
            huecos.setdefault((sin_marca_de_parte(v["titulo"]), parte[1]), []).append(pos)
    resultado = list(videos)
    for posiciones in huecos.values():
        en_orden = sorted((videos[p] for p in posiciones),
                          key=lambda v: parte_de_titulo(v["titulo"])[0])
        for p, v in zip(posiciones, en_orden):
            resultado[p] = v
    return resultado


def metadata_de_respaldo(video):
    """Metadata genérica pero funcional, usada solo cuando revisar_y_generar_metadata
    falla (red, cuota de la API, etc.) — para no dejar el video sin subir por
    un fallo pasajero ajeno al contenido en sí. No reemplaza el chequeo de
    contenido de Gemini, solo cubre su ausencia: el técnico ya pasó antes."""
    emocion = video.get("emocion", "drama")
    hashtags = HASHTAGS_DE_RESPALDO_POR_EMOCION.get(emocion, ["historias"]) + ["reddit", "shorts"]
    titulo = video.get("titulo") or "Historia de Reddit"
    parte = parte_de_titulo(titulo)
    return {
        "aprobado": True,
        "motivo_rechazo": "",
        "titulo_youtube": con_parte(titulo, parte) if parte else recortar_titulo(titulo),
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
        # Sin pasarle SCOPES: así se usa con los permisos que el token trae
        # escritos. Si se le impone una lista más amplia que la que Google
        # concedió, el refresco del token falla con "Not all requested scopes
        # were granted" y deja de poder subir — un token viejo tiene que
        # seguir sirviendo para lo que sí le dieron permiso.
        creds = Credentials.from_authorized_user_file(RUTA_TOKEN)

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
        # Se reescribe en cada renovación: atómico, y en 600 como el resto
        # de credenciales.
        almacen.escribir_texto(RUTA_TOKEN, creds.to_json(), privado=True)

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
        rotos = set(almacen.leer(RUTA_SUBIDAS_ROTAS, []) or [])
        for item in resp.get("items", []):
            if item["snippet"]["title"] == titulo and item["id"]["videoId"] not in rotos:
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
        # La frase cambia según de dónde salió: un post de Reddit lo escribió
        # su propio autor, mientras que una anécdota de YouTube se contó en el
        # canal de alguien más. Dar el crédito equivocado es peor que no darlo.
        if "youtube.com" in fuente_url or "youtu.be" in fuente_url:
            linea_fuente = "Historia inspirada en una anécdota contada en el canal"
            linea_fuente += f" {autor}" if autor else " de un tercero"
        else:
            linea_fuente = "Historia inspirada en una publicación pública de Reddit"
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


TROZO_SUBIDA = 8 * 1024 * 1024
# Cuánto esperar antes de cada reintento cuando se corta la conexión a media
# subida: unos 6 minutos en total, lo que tarda un teléfono en volver a
# engancharse a la WiFi o salir de un túnel. Cada reintento sigue desde el
# último trozo que YouTube confirmó, no desde el principio.
ESPERAS_RECONEXION = [5, 10, 20, 30, 60, 60, 90, 90]


def _es_corte_de_red(exc):
    """¿Vale la pena reintentar? Cortes de red y errores pasajeros de Google
    sí; una clave mala o el límite diario de subidas, no."""
    if isinstance(exc, HttpError):
        return getattr(exc.resp, "status", 0) in (500, 502, 503, 504)
    if isinstance(exc, (OSError, TimeoutError, ConnectionError)):
        return True   # socket.timeout, ssl.SSLError, "Network is unreachable"…
    return type(exc).__module__.startswith("httplib2")


def _firma_archivo(ruta):
    st = os.stat(ruta)
    return {"ruta": ruta, "tam": st.st_size, "mtime": int(st.st_mtime)}


def _sesion_guardada(ruta):
    """La URL de una subida de este mismo archivo que se quedó a medias, si
    la hay y todavía sirve (YouTube la guarda una semana; aquí, 5 días)."""
    s = almacen.leer(RUTA_SUBIDA_EN_CURSO, None)
    if not isinstance(s, dict) or not s.get("uri"):
        return None
    try:
        firma = _firma_archivo(ruta)
    except OSError:
        return None
    if any(s.get(k) != v for k, v in firma.items()):
        return None
    if time.time() - s.get("desde", 0) > 5 * 86400:
        return None
    return s


def _guardar_sesion(ruta, uri):
    almacen.guardar(RUTA_SUBIDA_EN_CURSO,
                    {**_firma_archivo(ruta), "uri": uri, "desde": int(time.time())},
                    privado=True)


def _olvidar_sesion():
    try:
        os.remove(RUTA_SUBIDA_EN_CURSO)
    except FileNotFoundError:
        pass


def subir_video(servicio, ruta_video, metadata, video, publish_at_iso, dormir=time.sleep):
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
    nombre = os.path.basename(ruta_video)
    total = os.path.getsize(ruta_video)
    total_mb = total / (1024 * 1024)

    def nueva_peticion():
        # En trozos de 8 MB y no de una vez (chunksize=-1): de una vez,
        # YouTube no contesta hasta el final y el panel se pasaba minutos sin
        # decir nada. Cada trozo que llega es una línea con el porcentaje,
        # que la tarjeta del trabajo convierte en barra. Tiene que ser
        # múltiplo de 256 KB.
        media = MediaFileUpload(ruta_video, chunksize=TROZO_SUBIDA, resumable=True,
                                mimetype="video/mp4")
        return servicio.videos().insert(part="snippet,status", body=body, media_body=media)

    request = nueva_peticion()
    retomada = False
    sesion = _sesion_guardada(ruta_video)
    if sesion:
        # _in_error_state hace que la librería pregunte primero a YouTube
        # cuánto le llegó ("bytes */total") y siga desde ahí.
        request.resumable_uri = sesion["uri"]
        request._in_error_state = True
        retomada = True
        logger.info(f"Retomando la subida de {nombre} que se cortó la otra vez.")
    else:
        logger.info(f"Subiendo {nombre}: 0% (0/{total_mb:.0f} MB)")

    respuesta, cortes, uri_guardada = None, 0, sesion["uri"] if sesion else None
    while respuesta is None:
        try:
            status, respuesta = request.next_chunk()
        except HttpError as exc:
            estado = getattr(exc.resp, "status", 0)
            if retomada and estado in (400, 404, 410):
                # La sesión de la otra vez ya no existe: se empieza de cero.
                logger.info(f"La subida a medias de {nombre} caducó; empieza de nuevo.")
                _olvidar_sesion()
                request, retomada, uri_guardada = nueva_peticion(), False, None
                continue
            if not _es_corte_de_red(exc) or cortes >= len(ESPERAS_RECONEXION):
                raise
            cortes = _esperar_corte(nombre, request, total, exc, cortes, dormir)
            continue
        except Exception as exc:
            if not _es_corte_de_red(exc) or cortes >= len(ESPERAS_RECONEXION):
                raise
            cortes = _esperar_corte(nombre, request, total, exc, cortes, dormir)
            continue
        retomada, cortes = False, 0
        if request.resumable_uri and request.resumable_uri != uri_guardada:
            uri_guardada = request.resumable_uri
            _guardar_sesion(ruta_video, uri_guardada)
        if status:
            logger.info(f"Subiendo {nombre}: {int(status.progress() * 100)}% "
                        f"({status.resumable_progress / (1024 * 1024):.0f}/{total_mb:.0f} MB)")
    _olvidar_sesion()
    logger.info(f"Subiendo {nombre}: 100% ({total_mb:.0f}/{total_mb:.0f} MB)")

    return respuesta["id"]


def _esperar_corte(nombre, request, total, exc, cortes, dormir):
    espera = ESPERAS_RECONEXION[cortes]
    pct = int(100 * request.resumable_progress / total) if total else 0
    logger.warning(
        f"Se cortó la conexión subiendo {nombre} al {pct}% ({type(exc).__name__}); "
        f"reintento {cortes + 1}/{len(ESPERAS_RECONEXION)} en {espera} s, "
        f"sigue desde donde se quedó.")
    dormir(espera)
    return cortes + 1


def verificar_subida(servicio, video_id):
    """Qué dice YouTube del archivo que acaba de recibir.

    Devuelve (estado, detalle), con estado:
      "ok"         — llegó entero; puede que aún lo esté procesando.
      "procesado"  — además ya terminó de procesarlo: no hace falta mirar más.
      "fallida"    — YouTube lo recibió roto o no pudo procesarlo
                     (failureReason: conversion, uploadAborted, invalidFile…).
                     Hay que borrarlo y volver a subirlo.
      "rechazada"  — lo recibió bien y no lo acepta (copyright, duplicado,
                     duración…). Volver a subirlo no lo arregla.
      "no_existe"  — no aparece en el canal.
      None         — no se pudo preguntar (sin red): no se sabe nada.
    """
    try:
        r = servicio.videos().list(part="status,processingDetails", id=video_id).execute()
    except Exception as exc:
        logger.warning(f"No se pudo comprobar la subida de {video_id} ({exc}).")
        return None, ""
    items = r.get("items") or []
    if not items:
        return "no_existe", ""
    st = items[0].get("status", {})
    proc = items[0].get("processingDetails", {})
    subida = st.get("uploadStatus")
    if subida == "failed":
        return "fallida", st.get("failureReason") or ""
    if subida == "rejected":
        return "rechazada", st.get("rejectionReason") or ""
    if subida == "deleted":
        return "no_existe", "borrado"
    if proc.get("processingStatus") == "failed":
        return "fallida", proc.get("processingFailureReason") or "processing"
    if subida == "processed" or proc.get("processingStatus") == "succeeded":
        return "procesado", ""
    return "ok", subida or ""


def quitar_subida_rota(servicio, video_id):
    """Borra del canal un video que YouTube dio por fallido. Si el token no
    tiene permiso de borrar, lo apunta para que la búsqueda de duplicados no
    lo confunda con el bueno, y avisa de que hay que quitarlo a mano."""
    try:
        servicio.videos().delete(id=video_id).execute()
        logger.info(f"Borrado del canal el video roto {video_id}.")
        return True
    except Exception as exc:
        rotos = almacen.leer(RUTA_SUBIDAS_ROTAS, []) or []
        if video_id not in rotos:
            almacen.guardar(RUTA_SUBIDAS_ROTAS, rotos + [video_id])
        logger.warning(
            f"No se pudo borrar el video roto {video_id} ({exc}). Quítalo a mano en "
            f"https://studio.youtube.com/video/{video_id}/edit — se sube uno nuevo igual.")
        return False


def subir_y_verificar(servicio, ruta, metadata, video, publish_at_iso):
    """Sube y comprueba en YouTube que llegó bien. Si llegó roto, lo borra y
    lo sube otra vez (una sola vez: si falla dos seguidas, algo más pasa).

    Devuelve (video_id, estado, detalle) como verificar_subida.
    """
    for intento in (1, 2):
        video_id = subir_video(servicio, ruta, metadata, video, publish_at_iso)
        estado, detalle = verificar_subida(servicio, video_id)
        if estado not in ("fallida", "no_existe"):
            return video_id, estado, detalle
        logger.warning(f"YouTube dice que la subida de {os.path.basename(ruta)} salió mal "
                       f"({estado}{': ' + detalle if detalle else ''}).")
        if estado == "fallida":
            quitar_subida_rota(servicio, video_id)
        if intento == 1:
            logger.info("La vuelvo a subir desde el principio.")
    raise RuntimeError(f"La subida salió mal dos veces seguidas ({estado} {detalle}).")


def _por_verificar(p, dias=3):
    """¿Subido hace menos de `dias` y YouTube aún no dijo que quedó bien?"""
    if not p.get("video_id") or p.get("verificado") or not p.get("subido_en"):
        return False
    try:
        t = datetime.strptime(p["subido_en"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return False
    return datetime.now(timezone.utc) - t < timedelta(days=dias)


def revisar_subidas_recientes(servicio, publicados, dias=3):
    """Vuelve a mirar los videos subidos en los últimos días que YouTube no
    había terminado de procesar. Justo al subir, un video sale "uploaded" y
    el fallo de procesado llega minutos después, así que el primer vistazo
    no basta.

    Los que salieron rotos se borran del canal y se quitan de publicados:
    como el archivo sigue en el teléfono, esta misma corrida los vuelve a
    subir. Devuelve la lista nueva y cuántos se quitaron.
    """
    quedan, quitados = [], 0
    for p in publicados:
        if not _por_verificar(p, dias):
            quedan.append(p)
            continue
        estado, detalle = verificar_subida(servicio, p["video_id"])
        if estado == "procesado":
            p = {**p, "verificado": True}
        elif estado == "rechazada":
            p = {**p, "verificado": True, "estado_youtube": f"rechazado: {detalle}"}
            logger.warning(f"YouTube rechazó {p.get('titulo_youtube')!r} ({detalle}). "
                           f"No se vuelve a subir: {p.get('url_revision')}")
        elif estado in ("fallida", "no_existe"):
            if not os.path.exists(p["ruta"]):
                p = {**p, "verificado": True, "estado_youtube": f"{estado}: {detalle}, sin archivo local"}
                logger.warning(f"{p.get('titulo_youtube')!r} salió mal en YouTube y ya no está "
                               f"en el teléfono para volver a subirlo.")
            else:
                logger.warning(f"{p.get('titulo_youtube')!r} salió mal en YouTube "
                               f"({estado}{': ' + detalle if detalle else ''}); se vuelve a subir.")
                if estado == "fallida":
                    quitar_subida_rota(servicio, p["video_id"])
                quitados += 1
                continue
        quedan.append(p)
    return quedan, quitados


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
                 + (", sin fecha programada" if not real.get("publish_at") else ""))
    if real.get("privacidad") == "public":
        logger.error("     El video YA ESTÁ PÚBLICO. No hubo ventana de revisión.")
    logger.error("     Causa habitual: el canal no está verificado por teléfono,")
    logger.error("     y sin verificar YouTube no deja programar publicaciones.")
    logger.error("     Verifícalo en https://www.youtube.com/verify y vuelve a probar.")
    logger.error("     Mientras tanto, ponlo privado a mano:")
    logger.error(f"     https://studio.youtube.com/video/{video_id}/edit")
    logger.error("=" * 62)
    return False


# Lo que ya está en TikTok no hace falta en el teléfono: se borra a los 7
# días de subirlo a YouTube, como siempre. Lo que todavía no está espera
# "dias_espera_tiktok" (14 de fábrica), para poder pasarlo a mano.
DIAS_RETENCION_LOCAL = 7
# El registro de tiktok_publisher.py. Va por ruta y no importando el módulo:
# tiktok_publisher importa este, y al revés sería un import circular.
RUTA_TIKTOK_SUBIDOS = os.path.join(CARPETA_ESTADO, "tiktok_subidos.json")


def _nombre_archivo(ruta):
    # NFC: en la SD los nombres con acentos pueden venir normalizados
    # distinto y la misma ruta no compararía igual (ver estado.py).
    return unicodedata.normalize("NFC", os.path.basename(ruta or ""))


def nombres_en_tiktok():
    """Los archivos que ya se subieron a TikTok (por la API o marcados a mano)."""
    registro = almacen.leer(RUTA_TIKTOK_SUBIDOS, []) or []
    return {_nombre_archivo(v.get("ruta")) for v in registro if isinstance(v, dict)}


def dias_espera_tiktok(cfg=None):
    cfg = cargar_config() if cfg is None else cfg
    try:
        return max(1, min(90, int(cfg.get("dias_espera_tiktok", 14))))
    except (TypeError, ValueError):
        return 14


def dias_de_retencion(registro, cfg=None, en_tiktok=None):
    """Cuántos días se queda en el teléfono este video tras subirlo a YouTube."""
    en_tiktok = nombres_en_tiktok() if en_tiktok is None else en_tiktok
    if _nombre_archivo(registro.get("ruta")) in en_tiktok:
        return DIAS_RETENCION_LOCAL
    return dias_espera_tiktok(cfg)


def limpiar_videos_locales_vencidos():
    """Borra los .mp4 locales de videos ya subidos a YouTube cuando cumplen
    su plazo (dias_de_retencion): 7 días si ya están en TikTok, y
    "dias_espera_tiktok" si todavía no, para que dé tiempo a subirlos a mano
    a TikTok u otras plataformas antes de que se borren."""
    publicados = cargar_json(RUTA_PUBLICADOS, [])
    cfg, en_tiktok = cargar_config(), nombres_en_tiktok()
    ahora = datetime.now(timezone.utc)
    cambios = False

    for p in publicados:
        if p.get("_borrado_local") or not p.get("subido_en"):
            continue
        try:
            fecha_subida = datetime.strptime(p["subido_en"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        except ValueError:
            continue

        limite = dias_de_retencion(p, cfg, en_tiktok)
        if ahora - fecha_subida >= timedelta(days=limite):
            ruta = p["ruta"]
            if os.path.exists(ruta):
                try:
                    os.remove(ruta)
                    logger.info(f"🗑️  Borrado local (cumplió {limite} días subido): {os.path.basename(ruta)}")
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

    # Antes de decidir qué falta: un video de ayer que YouTube no pudo
    # procesar sale de publicados aquí y vuelve a subirse en esta corrida.
    servicio_yt = None
    if any(_por_verificar(p) for p in publicados):
        try:
            servicio_yt = obtener_servicio_youtube()
            publicados, quitados = revisar_subidas_recientes(servicio_yt, publicados)
            guardar_json(RUTA_PUBLICADOS, publicados)
        except Exception as exc:
            logger.warning(f"No se pudieron revisar las subidas recientes ({exc}).")
    rutas_ya_procesadas = {p["ruta"] for p in publicados} | {r["ruta"] for r in rechazados}

    pendientes = en_orden_de_serie(
        [v for v in completados if v["ruta"] not in rutas_ya_procesadas])
    if not pendientes:
        logger.info("Todos los videos completados ya fueron procesados anteriormente.")
        return

    client = genai.Client()
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
            video_id, estado_yt, detalle_yt = subir_y_verificar(
                servicio_yt, ruta, metadata, video, publish_at_iso)
        except Exception as exc:
            if "uploadLimitExceeded" in str(exc):
                logger.info(
                    "Se alcanzó el límite diario de subidas de YouTube — el resto del "
                    "colchón queda pendiente para la próxima corrida (no se pierde nada)."
                )
                break
            # Cualquier otro fallo de subida (red, timeout, error temporal de la
            # API) tampoco descarta el video: se reintenta en la próxima corrida
            # en vez de quedar rechazado para siempre. Si se cortó a medias, la
            # próxima retoma desde el último trozo (subida_en_curso.json).
            logger.warning(f"Fallo al subir {ruta} (se reintentará más adelante): {exc}")
            continue
        if estado_yt == "rechazada":
            logger.warning(f"YouTube rechazó el video ({detalle_yt}); no se vuelve a subir.")

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
            # Sin "verificado", la próxima corrida vuelve a preguntar a YouTube
            # si terminó de procesarlo bien (ver revisar_subidas_recientes).
            "verificado": estado_yt in ("procesado", "rechazada"),
            **({"estado_youtube": f"rechazado: {detalle_yt}"} if estado_yt == "rechazada" else {}),
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
