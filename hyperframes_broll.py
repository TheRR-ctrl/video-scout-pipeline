"""
HyperFrames B-roll — genera video de apoyo como una *composición HTML*
(HTML + CSS + GSAP) y la renderiza a MP4 determinista con el CLI de
HyperFrames (https://hyperframes.heygen.com, Apache-2.0).

Idea (la misma que manim_broll.py, pero con la web como motor gráfico): en vez
de pedirle un video a un modelo generativo, le pedimos a Gemini el *código* de
una animación y la renderizamos localmente. HyperFrames toma un `index.html`
normal y, en vez de reproducirlo, le pide al navegador un frame concreto a la
vez (`seek(0)`, `seek(1/30)`, ...) con Chrome headless en modo determinista, y
encadena los frames con ffmpeg. Nunca llama a `play()`, así que el resultado no
depende de la velocidad de la máquina: mismo HTML -> mismo MP4.

Frente a los otros motores de video de apoyo de estos pipelines:

| | veo | manim | hyperframes |
|---|---|---|---|
| Costo | de pago | gratis | gratis |
| Velocidad | minutos/clip | ~1 min/clip | ~3x tiempo real |
| Duración del clip | fija (~8 s) | fija (~8 s) | **exacta**, la que se pida |
| Estilo | fotorrealista | vectorial matemático | tipografía/diseño web |
| Assets propios | no hace falta | no hace falta | no hace falta |

## Módulo portable

Este archivo no depende de ningún otro del repo: se copia tal cual entre
proyectos. Lo único que cambia entre pipelines es el `PerfilVisual` — qué se
está ilustrando, qué se superpone encima y qué zonas del cuadro hay que dejar
libres. Hay dos perfiles listos abajo; añadir uno nuevo es rellenar un
dataclass, no tocar el motor.

Requiere: Node.js >= 22 (para `npx`), ffmpeg/ffprobe en el PATH.
Credenciales: GEMINI_API_KEY. El render de HyperFrames es local: no consume
créditos de HeyGen ni pide cuenta.
"""
import os
import re
import sys
import time
import json
import math
import shutil
import hashlib
import logging
import tempfile
import platform
import subprocess
from dataclasses import dataclass

from google import genai
from google.genai import errors as genai_errors

# Versión fijada del CLI: HyperFrames se mueve rápido y una corrida desatendida
# no debería cambiar de motor de render sin que lo decidas. Súbela a mano.
VERSION_CLI = "0.8.29"

MODELO_TEXTO_DEFAULT = "gemini-3.6-flash"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CARPETA_ESTADO = os.path.join(BASE_DIR, "pipeline_state")
CARPETA_CACHE = os.path.join(CARPETA_ESTADO, "hyperframes_cache")

FPS = 30
TIMEOUT_RENDER_SEG = 900
TIMEOUT_LINT_SEG = 120
DURACION_DEFAULT_SEG = 8.0
# Se renderiza un poco más largo de lo pedido: quien consume el clip lo recorta
# con ffmpeg, y así un desfase de décimas nunca deja el final en negro.
MARGEN_DURACION_SEG = 0.6
# El render va a ~3x tiempo real, así que una composición muy larga bloquea el
# lote. Por encima de esto, el llamador debe loopear un clip más corto.
DURACION_MAX_SEG = 120.0

# Tope de la caché de clips. Cada MP4 de 1080p ronda los 2-6 MB y la clave
# incluye el prompt, así que sin poda la carpeta crece sin fin — y en un
# teléfono el disco se acaba mucho antes que las ganas de generar fondos.
# Cuando se pasa, se borran los menos usados recientemente hasta volver bajo
# el tope (a un clip borrado le cuesta un render volver, no es una pérdida).
CACHE_MAX_MB = 600.0

# Cambiar la plantilla del prompt cambia el resultado para el mismo
# prompt_visual, así que la versión entra en la clave de caché.
VERSION_PROMPT = 2

RESOLUCIONES = {
    "16:9": (1920, 1080),
    "9:16": (1080, 1920),
    "1:1": (1080, 1080),
}


# ---------------------------------------------------------
# PERFILES VISUALES (lo único específico de cada pipeline)
# ---------------------------------------------------------
@dataclass(frozen=True)
class PerfilVisual:
    """Describe qué tipo de video de apoyo se quiere y qué se le superpone.

    `nombre` entra en la clave de caché, así que cambiarlo fuerza a regenerar."""
    nombre: str
    contexto: str          # qué ilustra la composición y qué va encima
    direccion_arte: str    # paleta, ritmo, tono
    zonas_libres: str      # dónde no puede haber nada importante
    max_palabras_pantalla: int
    loopable: bool = False  # ¿el clip se va a repetir para cubrir más tiempo?


PERFIL_NARRACION_REFLEXIVA = PerfilVisual(
    nombre="narracion_reflexiva",
    contexto=(
        "Video de apoyo (b-roll) de UNA escena de un video largo narrado de "
        "psicología / desarrollo personal en español. La locución va en otra "
        "pista y los subtítulos karaoke se queman encima después: la "
        "composición es PURAMENTE VISUAL y MUDA."
    ),
    direccion_arte=(
        "- Fondo oscuro profundo (#080B10 - #12161D) con un degradado sutil; "
        "nada de blanco puro de fondo.\n"
        "- Paleta de acento fría y sobria, 2 colores como máximo (p. ej. "
        '"#7C9CFF", "#4ADE9B", "#F2C14E"). Editorial y calmado, no infantil '
        "ni \"startup\".\n"
        "- Movimiento lento y continuo: derivas, escalas suaves, parallax, "
        "líneas que se dibujan, formas geométricas grandes, degradados que "
        "respiran. Easing `power2.out` / `power3.inOut`. Nada rebota ni "
        "parpadea.\n"
        "- Nunca se queda quieto: siempre hay algo moviéndose despacio.\n"
        "- Metáfora visual abstracta del tema, nunca ilustración literal: sin "
        "caras, sin figuras humanas reconocibles, sin logos ni marcas."
    ),
    zonas_libres=(
        "- **25% inferior**: ahí van los subtítulos karaoke. Nada importante "
        "ni brillante en esa banda.\n"
        "- **30% superior**: en la primera escena va la tarjeta de título."
    ),
    max_palabras_pantalla=3,
)


PERFIL_HISTORIA_VERTICAL = PerfilVisual(
    nombre="historia_vertical",
    contexto=(
        "Fondo de un Short vertical en español: una historia personal narrada "
        "(drama, venganza, suspenso o comedia) con subtítulos karaoke grandes "
        "sobre el video. El fondo NO cuenta la historia, solo sostiene la "
        "atención mientras se escucha. Es PURAMENTE VISUAL y MUDO."
    ),
    direccion_arte=(
        "- Fondo oscuro (#07090D - #14181F) con un degradado o viñeta que "
        "empuja la mirada al centro.\n"
        "- Un solo color de acento saturado según el tono de la historia "
        '(p. ej. "#FF5C7A" tensión, "#7C9CFF" melancolía, "#4ADE9B" giro '
        "favorable). Textura sutil de grano o ruido estático, sin exagerar.\n"
        "- Movimiento hipnótico y constante de ritmo medio: patrones que se "
        "desplazan, formas que rotan despacio, ondas, cuadrículas en "
        "perspectiva, partículas grandes a la deriva. Es un fondo tipo "
        '\"satisfying loop\", no una animación con guion.\n'
        "- Nunca se detiene y nunca cambia de escena bruscamente: sin cortes, "
        "sin flashes, sin nada que compita con los subtítulos.\n"
        "- Abstracto siempre: sin caras, sin figuras humanas, sin logos, sin "
        "texto que pueda leerse como parte de la historia."
    ),
    zonas_libres=(
        "- **Franja central (del 30% al 75% de altura)**: ahí van los "
        "subtítulos karaoke, que son grandes. Deja esa zona oscura y sin "
        "detalle fino ni elementos brillantes.\n"
        "- **20% superior**: ahí va la tarjeta de título del hook."
    ),
    max_palabras_pantalla=0,
    loopable=True,
)


# El mismo fondo de historia, pero para el formato horizontal. Existe porque
# los dos perfiles de arriba no sirven tal cual: el vertical deja libre la
# franja central (donde van los subtítulos de un Short) y en 16:9 los
# subtítulos van abajo, así que lo interesante acabaría justo debajo del
# texto; y el reflexivo, que sí tiene las zonas libres correctas, no es
# cíclico, porque está pensado para una escena de largo exacto. Este pipeline
# siempre loopea el fondo para cubrir la historia, así que necesita las zonas
# del horizontal y el bucle cerrado a la vez.
PERFIL_HISTORIA_HORIZONTAL = PerfilVisual(
    nombre="historia_horizontal",
    contexto=(
        "Fondo de un video horizontal en español: una historia personal "
        "narrada (drama, venganza, suspenso o comedia) con subtítulos karaoke "
        "quemados encima. El fondo NO cuenta la historia, solo sostiene la "
        "atención mientras se escucha. Es PURAMENTE VISUAL y MUDO."
    ),
    direccion_arte=PERFIL_HISTORIA_VERTICAL.direccion_arte,
    zonas_libres=(
        "- **25% inferior**: ahí van los subtítulos karaoke. Nada importante "
        "ni brillante en esa banda.\n"
        "- **30% superior**: ahí va la tarjeta de título del hook."
    ),
    max_palabras_pantalla=0,
    loopable=True,
)


_PLANTILLA_PROMPT = """Eres un generador de composiciones HTML para HyperFrames, un motor
que renderiza HTML a video MP4 frame a frame con Chrome headless.

{CONTEXTO}

Responde ÚNICAMENTE con el archivo HTML completo. Sin explicaciones, sin ```.

## Contrato de HyperFrames (obligatorio)

- Documento HTML completo, empezando por `<!doctype html>`.
- Carga GSAP con exactamente esta etiqueta:
  `<script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script>`
- El elemento raíz debe ser:
  `<div id="root" data-composition-id="main" data-start="0" data-duration="{DURACION}" data-width="{ANCHO}" data-height="{ALTO}">`
  con `position: relative; width: {ANCHO}px; height: {ALTO}px; overflow: hidden;`.
- `data-duration` de la raíz vale EXACTAMENTE {DURACION}. No lo cambies.
- Cada bloque visible es una `<section class="clip" id="...">` con `data-start`
  y `data-duration` en segundos, dentro de la ventana [0, {DURACION}].
  Regla `.clip {{ position: absolute; inset: 0; }}`.
- Crea UNA sola línea de tiempo GSAP, pausada, y regístrala de forma síncrona:
  ```
  window.__timelines = window.__timelines || {{}};
  const tl = gsap.timeline({{ paused: true }});
  // ... tweens ...
  window.__timelines.main = tl;
  ```
- La animación debe durar {DURACION} segundos: encadena los tweens para llenar
  ese tiempo (usa posiciones absolutas en la timeline, p. ej. `tl.to(x, {{...}}, 2.4)`).

## Determinismo (el render pide frames sueltos, no reproduce)

- Nada de `Date`, `performance.now()`, `Math.random()` sin semilla,
  `requestAnimationFrame`, `setTimeout`, `setInterval`, `repeat: -1` ni
  animaciones CSS infinitas. El estado visual en el segundo T debe depender
  solo de T.
- Nada de `<video>`, `<audio>`, `<canvas>` con WebGL, ni imágenes externas.
- Sin peticiones de red salvo el `<script>` de GSAP indicado arriba.
- Fuentes: solo la pila del sistema
  `font-family: system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;`.

## Dirección de arte

{DIRECCION_ARTE}

## Zonas del cuadro que deben quedar libres

{ZONAS_LIBRES}

## Texto en pantalla

{REGLA_TEXTO}

## Principio y final

{REGLA_BUCLE}
"""

_REGLA_TEXTO_SIN_TEXTO = (
    "**Ninguna palabra en pantalla.** Los subtítulos de la narración se queman "
    "encima después; cualquier texto de la composición compite con ellos."
)
_REGLA_TEXTO_CON_LIMITE = (
    "**Como mucho {N} palabras en toda la composición, o ninguna.** Los "
    "subtítulos de la narración se queman encima después; más texto compite "
    "con ellos y con la locución."
)

_REGLA_BUCLE_CERRADO = (
    "El clip se va a repetir en bucle para cubrir toda la narración, así que "
    "**el último frame debe encajar con el primero**: que el estado visual en "
    "el segundo {DURACION} sea prácticamente el mismo que en el segundo 0 "
    "(mismas posiciones, mismas opacidades, misma escala), para que el corte "
    "del bucle no se vea. Diseña el movimiento como un ciclo completo: una "
    "vuelta entera, un desplazamiento de exactamente un patrón, una onda que "
    "vuelve a su fase inicial."
)
_REGLA_BUCLE_ABIERTO = (
    "Empieza y termina en un estado compuesto: ni en negro ni a medio fundido. "
    "El clip dura exactamente lo que la escena, así que no hace falta que el "
    "final enlace con el principio."
)


logger = logging.getLogger("hyperframes_broll")

_client = None
_cmd_cli = None


# ---------------------------------------------------------
# LLAMADA A GEMINI
# ---------------------------------------------------------
_RE_RETRY_DELAY = re.compile(r"retryDelay['\"]?\s*:\s*['\"]?(\d+(?:\.\d+)?)s")


def _llamar_con_reintentos(fn, *args, reintentos=3, espera_base_seg=20.0, **kwargs):
    """Reintenta en 429 (RESOURCE_EXHAUSTED) respetando el `retryDelay` que
    sugiere la propia API. Duplicado a propósito del `gemini_utils` de los
    pipelines: este módulo se copia entre repos y no debe arrastrar imports."""
    for intento in range(1, reintentos + 1):
        try:
            return fn(*args, **kwargs)
        except genai_errors.APIError as exc:
            es_cuota = "429" in str(exc) or "RESOURCE_EXHAUSTED" in str(exc)
            if not es_cuota or intento == reintentos:
                raise
            m = _RE_RETRY_DELAY.search(str(exc))
            espera = float(m.group(1)) + 1.0 if m else espera_base_seg * intento
            logger.warning(
                f"Cuota excedida (intento {intento}/{reintentos}), "
                f"reintentando en {espera:.0f}s..."
            )
            time.sleep(espera)
    return None  # inalcanzable


def _obtener_cliente():
    global _client
    if _client is None:
        _client = genai.Client()
    return _client


# ---------------------------------------------------------
# CLI DE HYPERFRAMES
# ---------------------------------------------------------
def _entorno_cli():
    """Entorno para el CLI: sin telemetría ni chequeo de actualizaciones, que
    en una corrida desatendida solo añaden latencia y llamadas de red."""
    env = dict(os.environ)
    env["HYPERFRAMES_NO_TELEMETRY"] = "1"
    env["DO_NOT_TRACK"] = "1"
    env["HYPERFRAMES_NO_UPDATE_CHECK"] = "1"
    env["HYPERFRAMES_SKIP_SKILLS"] = "1"
    env["CI"] = env.get("CI", "1")
    return env


def comando_cli():
    """Prefijo de comando del CLI de HyperFrames.

    Si hay un binario instalado (`npm i -g hyperframes`) se usa ese; si no, se
    cae a `npx`, que descarga el paquete la primera vez y luego lo cachea."""
    global _cmd_cli
    if _cmd_cli is None:
        binario = os.environ.get("HYPERFRAMES_BIN") or shutil.which("hyperframes")
        _cmd_cli = [binario] if binario else ["npx", "-y", f"hyperframes@{VERSION_CLI}"]
    return list(_cmd_cli)


# ---------------------------------------------------------
# DÓNDE PUEDE CORRER ESTO
# ---------------------------------------------------------
# Este motor es de PC, a propósito. El render arranca Chrome headless, y el
# Chrome que descargan las herramientas de Node está compilado contra glibc;
# Android usa bionic, así que el binario ni siquiera arranca. Encima harían
# falta Node >= 22, unos cientos de MB de caché de npx y ~3x tiempo real de
# CPU sostenida — en un teléfono eso es el proceso muriendo a media tarea.
#
# Detectarlo aquí y decirlo claro es mejor que dejar que lo descubra un
# subprocess que falla a los diez minutos con un error de enlazado. Quien
# quiera intentarlo igual (proot con glibc, por ejemplo) tiene la salida de
# emergencia: HYPERFRAMES_FORZAR=1.
_MARCAS_ANDROID = ("/data/data/com.termux", "/system/build.prop")


def _es_android():
    if os.environ.get("TERMUX_VERSION") or "com.termux" in (os.environ.get("PREFIX") or ""):
        return True
    if hasattr(sys, "getandroidapilevel"):
        return True
    if "android" in platform.platform().lower():
        return True
    return any(os.path.exists(m) for m in _MARCAS_ANDROID)


def plataforma_apta():
    """(apta, motivo). `motivo` solo tiene sentido cuando no es apta.

    Se consulta antes de gastar una llamada a Gemini o un render: el llamador
    decide si eso es un error del lote o simplemente caer al otro motor."""
    if os.environ.get("HYPERFRAMES_FORZAR") == "1":
        return True, ""
    if _es_android():
        return False, (
            "El motor 'hyperframes' es solo para PC: el render necesita Chrome "
            "headless (compilado contra glibc, no arranca en Android), Node >= 22 "
            "y ~3x tiempo real de CPU. Desde el teléfono usa motor_fondo "
            '"cortes", o genera los fondos en el runner con el workflow '
            "'Fabricar fondos con IA' y bájalos. Para intentarlo igual: "
            "HYPERFRAMES_FORZAR=1."
        )
    return True, ""


def comprobar_dependencias():
    """Lanza si falta algo para renderizar. Conviene llamarlo antes del lote
    para fallar temprano en vez de a mitad del primer video."""
    apta, motivo = plataforma_apta()
    if not apta:
        raise RuntimeError(motivo)
    if not (os.environ.get("HYPERFRAMES_BIN") or shutil.which("hyperframes") or shutil.which("npx")):
        raise RuntimeError(
            "El motor 'hyperframes' necesita Node.js >= 22 (para npx) o el CLI "
            "instalado. Ver README, sección del motor de video de apoyo."
        )
    faltantes = [exe for exe in ("ffmpeg", "ffprobe") if shutil.which(exe) is None]
    if faltantes:
        raise RuntimeError(
            "El motor 'hyperframes' necesita " + ", ".join(faltantes) + " en el PATH."
        )


# ---------------------------------------------------------
# GENERACIÓN Y RENDER
# ---------------------------------------------------------
def _archivo_valido(ruta):
    return bool(ruta) and os.path.isfile(ruta) and os.path.getsize(ruta) > 0


def _ruta_cache(prompt_visual, aspecto, duracion, perfil):
    clave = hashlib.sha256(
        f"v{VERSION_PROMPT}|{perfil.nombre}|{aspecto}|{duracion:.1f}|{prompt_visual}".encode("utf-8")
    ).hexdigest()[:24]
    os.makedirs(CARPETA_CACHE, exist_ok=True)
    return os.path.join(CARPETA_CACHE, f"hf_{clave}.mp4")


def _ruta_parcial(ruta_final):
    """Dónde escribe el render antes de que el clip cuente como bueno.

    Oculto y con otra extensión a propósito: mientras se escribe no debe
    parecerse a un clip de la caché, porque un render a medias tiene el
    tamaño de un MP4 de verdad y nada lo distinguiría después."""
    carpeta, nombre = os.path.split(ruta_final)
    return os.path.join(carpeta, f".{nombre}.parcial")


def limpiar_cache(max_mb=None):
    """Deja la caché por debajo de `max_mb` borrando los clips menos usados.

    La clave de caché lleva el prompt dentro, así que cada idea visual nueva
    añade un archivo y ninguno se borra solo. Con el tope puesto, la carpeta
    se estabiliza y lo único que se pierde es un render que se puede rehacer.

    De paso se barren los `.parcial` que dejó un render muerto a mitad."""
    tope_bytes = float(CACHE_MAX_MB if max_mb is None else max_mb) * 1024 * 1024
    if not os.path.isdir(CARPETA_CACHE):
        return 0

    borrados = 0
    clips = []
    for nombre in os.listdir(CARPETA_CACHE):
        ruta = os.path.join(CARPETA_CACHE, nombre)
        try:
            if nombre.endswith(".parcial"):
                # Nadie lo va a terminar: el proceso que lo escribía ya no está.
                os.remove(ruta)
                borrados += 1
                continue
            if not nombre.startswith("hf_") or not os.path.isfile(ruta):
                continue
            st = os.stat(ruta)
            clips.append((st.st_mtime, st.st_size, ruta))
        except OSError:
            continue

    total = sum(c[1] for c in clips)
    if total <= tope_bytes:
        return borrados

    # Del más viejo al más nuevo. `generar_clip_cacheado` toca el archivo en
    # cada acierto, así que "viejo" aquí es "hace mucho que no se usa", no
    # "se generó hace mucho".
    for _, tam, ruta in sorted(clips):
        if total <= tope_bytes:
            break
        try:
            os.remove(ruta)
        except OSError:
            continue
        total -= tam
        borrados += 1
    if borrados:
        logger.info(f"Caché de HyperFrames podada: {borrados} archivo(s) fuera.")
    return borrados


def _limpiar_html(texto):
    """Quita las vallas de markdown que el modelo a veces añade pese al prompt."""
    texto = (texto or "").strip()
    texto = re.sub(r'^```(?:html)?\s*', '', texto)
    texto = re.sub(r'\s*```$', '', texto)
    return texto.strip()


def _construir_prompt_sistema(perfil, duracion, w, h):
    if perfil.max_palabras_pantalla <= 0:
        regla_texto = _REGLA_TEXTO_SIN_TEXTO
    else:
        regla_texto = _REGLA_TEXTO_CON_LIMITE.format(N=perfil.max_palabras_pantalla)

    regla_bucle = (
        _REGLA_BUCLE_CERRADO.format(DURACION=f"{duracion:.2f}")
        if perfil.loopable else _REGLA_BUCLE_ABIERTO
    )

    return _PLANTILLA_PROMPT.format(
        CONTEXTO=perfil.contexto,
        DIRECCION_ARTE=perfil.direccion_arte,
        ZONAS_LIBRES=perfil.zonas_libres,
        REGLA_TEXTO=regla_texto,
        REGLA_BUCLE=regla_bucle,
        DURACION=f"{duracion:.2f}",
        ANCHO=w,
        ALTO=h,
    )


def _generar_html(cliente, prompt_visual, modelo, duracion, w, h, perfil, correccion=None):
    partes = [
        _construir_prompt_sistema(perfil, duracion, w, h),
        "",
        "Idea visual (interprétala como metáfora abstracta, no la escribas en "
        f"pantalla): {prompt_visual}",
    ]
    if correccion:
        partes += [
            "",
            "El intento anterior falló. Corrige EXACTAMENTE esto y devuelve el "
            "HTML completo de nuevo:",
            correccion,
        ]

    respuesta = _llamar_con_reintentos(
        cliente.models.generate_content,
        model=modelo,
        contents="\n".join(partes),
    )
    html = _limpiar_html(respuesta.text or "")
    if "data-composition-id" not in html or "__timelines" not in html:
        raise ValueError("La respuesta de Gemini no es una composición de HyperFrames válida.")
    return html


def _lint(proyecto):
    """Corre el linter del CLI (sin navegador, ~1 s) y devuelve el texto de los
    errores, o None si la composición está limpia. Atrapa fallos estructurales
    antes de pagar los segundos de un render que iba a fallar igual."""
    try:
        res = subprocess.run(
            comando_cli() + ["lint", proyecto, "--json"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=TIMEOUT_LINT_SEG, env=_entorno_cli(),
        )
        salida = res.stdout or ""
        inicio = salida.find("{")
        if inicio < 0:
            return None  # sin JSON parseable: que decida el render
        datos = json.loads(salida[inicio:])
    except Exception as exc:
        logger.debug(f"lint no utilizable, se sigue al render: {exc}")
        return None

    if not datos.get("errorCount"):
        return None

    errores = []
    for f in datos.get("findings", []):
        if f.get("severity") != "error":
            continue
        linea = f"- {f.get('code', 'error')}: {f.get('message', '')}"
        # El linter trae la corrección concreta; se la pasamos tal cual al
        # modelo, que acierta mucho más que con solo el mensaje de error.
        if f.get("fixHint"):
            linea += f"\n  Cómo se arregla: {f['fixHint']}"
        errores.append(linea)
    return "El linter de HyperFrames reportó errores:\n" + "\n".join(errores[:10])


def _render(proyecto, ruta_salida):
    res = subprocess.run(
        comando_cli() + [
            "render", proyecto,
            "-o", ruta_salida,
            "--fps", str(FPS),
            "--quality", "standard",
            "--quiet",
        ],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=TIMEOUT_RENDER_SEG, env=_entorno_cli(),
    )
    if res.returncode != 0 or not _archivo_valido(ruta_salida):
        detalle = (res.stderr or res.stdout or "").strip()[-2000:]
        raise RuntimeError(f"hyperframes render falló (código {res.returncode}):\n{detalle}")


def _duracion_real(ruta):
    """Segundos que dice ffprobe, o None si no puede leer el archivo.

    Un MP4 truncado no tiene el índice al final, así que aquí se cae — que es
    justo lo que hace falta para no guardar medio render como si fuera bueno."""
    try:
        res = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", ruta],
            capture_output=True, text=True, timeout=60,
        )
        return float((res.stdout or "").strip())
    except Exception:
        return None


def generar_clip_cacheado(prompt_visual, aspecto="16:9", modelo=MODELO_TEXTO_DEFAULT,
                          reintentos=3, duracion_seg=None,
                          perfil=PERFIL_NARRACION_REFLEXIVA):
    """Devuelve la ruta local a un clip de video para la idea visual dada, o
    None si falló tras los reintentos.

    Misma interfaz que veo_broll/manim_broll, más `duracion_seg` y `perfil`.
    A diferencia de esos motores, el clip se compone con la duración que se
    pide (hasta DURACION_MAX_SEG), así que no hace falta loopearlo para cubrir
    la narración."""
    duracion = float(duracion_seg or DURACION_DEFAULT_SEG) + MARGEN_DURACION_SEG
    duracion = min(max(duracion, 2.0), DURACION_MAX_SEG)
    # Se redondea a medio segundo para que dos escenas de duración parecida con
    # la misma idea visual compartan clip en vez de renderizar dos veces.
    duracion = math.ceil(duracion * 2) / 2

    apta, motivo = plataforma_apta()
    if not apta:
        # Ni llamada a Gemini ni render: el llamador cae a su otro motor.
        logger.warning(motivo)
        return None

    ruta_salida = _ruta_cache(prompt_visual, aspecto, duracion, perfil)
    if _archivo_valido(ruta_salida):
        # Un clip que quedó truncado por el camino viejo (antes de que el
        # render fuera atómico) sigue pesando más de cero y la caché lo
        # serviría igual. Se comprueba de verdad una vez y, si está roto, se
        # tira y se regenera. Solo si hay ffprobe: sin él, mejor servir el
        # clip que negarlo por no poder mirarlo.
        if shutil.which("ffprobe") and _duracion_real(ruta_salida) is None:
            logger.warning("Clip cacheado ilegible, se regenera: "
                           f"{os.path.basename(ruta_salida)}")
            try:
                os.remove(ruta_salida)
            except OSError:
                pass
        else:
            # Recién usado, que es lo que mira la poda de la caché.
            try:
                os.utime(ruta_salida, None)
            except OSError:
                pass
            return ruta_salida

    w, h = RESOLUCIONES.get(aspecto, RESOLUCIONES["16:9"])
    parcial = _ruta_parcial(ruta_salida)
    cliente = _obtener_cliente()
    correccion = None

    for intento in range(1, reintentos + 1):
        try:
            html = _generar_html(cliente, prompt_visual, modelo, duracion, w, h,
                                 perfil, correccion)
            with tempfile.TemporaryDirectory(prefix="hyperframes_broll_") as tmp:
                with open(os.path.join(tmp, "index.html"), "w", encoding="utf-8") as f:
                    f.write(html)

                # El linter es barato; si encuentra errores, se los devolvemos
                # al modelo en el siguiente intento en vez de gastar un render.
                errores = _lint(tmp)
                if errores:
                    raise RuntimeError(errores)

                # Se renderiza a un archivo aparte y solo al final se mueve al
                # nombre de la caché, con os.replace, que es atómico. Si el
                # proceso muere a media escritura —en un portátil que se
                # suspende, en un runner que se queda sin tiempo— lo que queda
                # es un .parcial que nadie lee, no un MP4 truncado que la
                # caché daría por bueno para siempre.
                _render(tmp, parcial)
                dur_real = _duracion_real(parcial)
                if dur_real is None or dur_real < duracion * 0.5:
                    raise RuntimeError(
                        "El render salió ilegible o demasiado corto "
                        f"({'ilegible' if dur_real is None else f'{dur_real:.1f}s'} "
                        f"de {duracion:.1f}s pedidos)."
                    )
                os.replace(parcial, ruta_salida)

            if _archivo_valido(ruta_salida):
                limpiar_cache()
                return ruta_salida
        except Exception as exc:
            logger.warning(f"HyperFrames intento {intento}/{reintentos} falló: {exc}")
            correccion = str(exc)[-1500:]
        finally:
            if os.path.exists(parcial):
                try:
                    os.remove(parcial)
                except OSError:
                    pass

    return None
