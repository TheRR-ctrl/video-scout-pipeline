"""
Núcleo compartido de los motores que dibujan con HyperFrames.

**Este archivo es byte a byte idéntico en `video-scout-pipeline` y en
`video_generation`.** Si lo tocas en uno, cópialo al otro; `hyperframes_broll.py`,
en cambio, es distinto en cada repo a propósito (ver abajo).

## Por qué existe

Los dos repos empezaron compartiendo `hyperframes_broll.py` entero. En
septiembre las dos líneas de desarrollo lo llevaron a sitios distintos y el
archivo dejó de poder copiarse:

- `video-scout-pipeline` compone **un fondo por historia**, con `PerfilVisual`
  (qué se ilustra, qué se superpone, qué zonas del cuadro quedan libres) y lo
  loopea para cubrir la narración.
- `video_generation` compone **varios planos por escena**, los pide a Gemini
  **en lotes** para no agotar la cuota diaria de texto, y los dibuja con
  plantillas propias en vez de pedírselos al modelo.

Ninguna de las dos formas es la correcta para el otro pipeline, así que
unificarlas sería romper uno de los dos. Pero por debajo de esa diferencia hay
un trozo que no depende de *qué* se dibuja: invocar el CLI, pasar el linter,
mirar si esto puede correr aquí, y escribir el mp4 en disco sin dejar basura.
Eso es lo que vive aquí.

## El corte

Aquí va lo que no sabe qué se está dibujando:

- la puerta de plataforma (esto es un motor de PC),
- el comando y el entorno del CLI, y el chequeo de dependencias,
- el linter, que es lo que evita pagar un render que iba a fallar igual,
- la caché en disco: escritura atómica, marcado de uso y poda,
- validez y duración real de un mp4.

Fuera queda todo lo que sí lo sabe: el prompt de sistema, los perfiles, las
plantillas, los lotes y la forma de la caché (cada repo arma su propia clave).

No importa ningún módulo de ninguno de los dos repos, a propósito: es lo que
permite que el archivo se copie tal cual.
"""
import os
import re
import sys
import json
import shutil
import logging
import platform
import subprocess
import contextlib

logger = logging.getLogger("hyperframes_nucleo")

# Versión fijada del CLI: HyperFrames se mueve rápido y una corrida desatendida
# no debería cambiar de motor de render sin que lo decidas. Súbela a mano, en
# los dos repos a la vez.
#
# 0.8.27 es la que tiene pruebas de verdad detrás: cuatro corridas completas de
# Actions en video_generation (35436027903, 35457754258, 35457937458,
# 35458025036), todas en verde. La otra rama había fijado 0.8.29, que nunca
# llegó a renderizar nada fuera de una prueba local.
VERSION_CLI = "0.8.27"

# De los dos repos se toma el margen más ancho en cada uno: pasarse de tiempo
# solo retrasa un fallo, quedarse corto mata un render que iba bien. Scout
# compone un clip de hasta 120s y necesita el margen largo de render;
# generation vio arranques en frío de npx pasando de los 120s en el linter.
TIMEOUT_RENDER_SEG = 900
TIMEOUT_LINT_SEG = 180

RESOLUCIONES = {
    "16:9": (1920, 1080),
    "9:16": (1080, 1920),
    "1:1": (1080, 1080),
}

# Tope de la caché de clips. La clave lleva el prompt dentro, así que cada idea
# nueva añade un archivo y ninguno se borra solo; los workflows además la
# conservan entre corridas. Cuando se pasa, se borran los menos usados
# recientemente: a un clip borrado le cuesta un render volver, no es una
# pérdida.
CACHE_MAX_MB = 600.0


# ---------------------------------------------------------
# DÓNDE PUEDE CORRER ESTO
# ---------------------------------------------------------
# Esto es un motor de PC (o de runner), a propósito. El render arranca Chrome
# headless, y el Chrome que descarga el CLI está compilado contra glibc;
# Android usa bionic, así que el binario ni siquiera arranca. Encima harían
# falta Node >= 22, unos cientos de MB de caché de npx y CPU sostenida.
#
# Detectarlo aquí y decirlo claro es mejor que dejar que lo descubra un
# subprocess que falla a los diez minutos con un error de enlazado. Quien
# quiera intentarlo igual (proot con glibc) tiene HYPERFRAMES_FORZAR=1.
_MARCAS_ANDROID = ("/data/data/com.termux", "/system/build.prop")


def es_android():
    if os.environ.get("TERMUX_VERSION") or "com.termux" in (os.environ.get("PREFIX") or ""):
        return True
    if hasattr(sys, "getandroidapilevel"):
        return True
    if "android" in platform.platform().lower():
        return True
    return any(os.path.exists(m) for m in _MARCAS_ANDROID)


def plataforma_apta():
    """(apta, motivo). `motivo` solo tiene sentido cuando no es apta.

    Se consulta antes de gastar una llamada al modelo o un render; el llamador
    decide si eso es abortar el lote o caer a otro motor. Cada repo envuelve
    esto para pegarle al motivo la salida concreta de su pipeline, que es lo
    que de verdad necesita quien lee el aviso desde el teléfono."""
    if os.environ.get("HYPERFRAMES_FORZAR") == "1":
        return True, ""
    if es_android():
        return False, (
            "Los motores que dibujan con HyperFrames son solo para PC o runner: "
            "el render necesita Chrome headless (compilado contra glibc, no "
            "arranca en Android), Node >= 22 y CPU sostenida. Para intentarlo "
            "igual: HYPERFRAMES_FORZAR=1."
        )
    return True, ""


# ---------------------------------------------------------
# EL CLI
# ---------------------------------------------------------
_cmd_cli = None


def comando_cli():
    """Prefijo de comando del CLI.

    Si hay un binario instalado (`npm i -g hyperframes`) se usa ese; si no, se
    cae a `npx`, que descarga el paquete la primera vez y luego lo cachea."""
    global _cmd_cli
    if _cmd_cli is None:
        binario = os.environ.get("HYPERFRAMES_BIN") or shutil.which("hyperframes")
        _cmd_cli = [binario] if binario else ["npx", "--yes", f"hyperframes@{VERSION_CLI}"]
    return list(_cmd_cli)


def entorno_cli():
    """Entorno para el CLI en corridas desatendidas: sin telemetría, sin
    comprobación de versión nueva y sin cargar skills. Los nombres de variable
    son los que documenta el propio CLI; se ponen todos porque han cambiado
    entre versiones y sobra con que alguna coincida."""
    env = dict(os.environ)
    env.update({
        "HYPERFRAMES_SKIP_SKILLS": "1",
        "HYPERFRAMES_TELEMETRY_DISABLED": "1",
        "HYPERFRAMES_NO_TELEMETRY": "1",
        "DO_NOT_TRACK": "1",
        "HYPERFRAMES_NO_UPDATE_CHECK": "1",
    })
    env["CI"] = env.get("CI", "1")
    return env


def comprobar_dependencias():
    """Lanza si falta algo para renderizar. Conviene llamarlo antes del lote
    para fallar temprano en vez de a mitad del primer video."""
    apta, motivo = plataforma_apta()
    if not apta:
        raise RuntimeError(motivo)
    if not (os.environ.get("HYPERFRAMES_BIN") or shutil.which("hyperframes")
            or shutil.which("npx")):
        raise RuntimeError(
            "HyperFrames necesita Node.js >= 22 (para npx) o el CLI instalado. "
            "Ver README, sección del motor de video de apoyo."
        )
    faltantes = [exe for exe in ("ffmpeg", "ffprobe") if shutil.which(exe) is None]
    if faltantes:
        raise RuntimeError(
            "HyperFrames necesita " + ", ".join(faltantes) + " en el PATH."
        )


# ---------------------------------------------------------
# ARCHIVOS
# ---------------------------------------------------------
def archivo_valido(ruta):
    return bool(ruta) and os.path.isfile(ruta) and os.path.getsize(ruta) > 0


def duracion_real(ruta):
    """Segundos que dice ffprobe, o None si no puede leer el archivo.

    Un mp4 truncado no tiene el índice al final, así que aquí se cae — que es
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


def ruta_parcial(ruta_final):
    """Dónde se escribe un clip antes de que cuente como bueno.

    Oculto y con otra extensión a propósito: mientras se escribe no debe
    parecerse a un clip terminado, porque un render a medias tiene el tamaño de
    un mp4 de verdad y nada lo distinguiría después."""
    carpeta, nombre = os.path.split(ruta_final)
    return os.path.join(carpeta, f".{nombre}.parcial")


@contextlib.contextmanager
def escritura_atomica(destino):
    """Cede una ruta temporal y, si el bloque termina bien, la mueve a destino.

    Existe porque escribir directo sobre el nombre de la caché es un fallo que
    no se nota hasta mucho después: si el proceso muere a media escritura —un
    runner sin tiempo, un portátil que se suspende— queda un mp4 truncado que
    «existe y pesa más de cero», o sea que la caché lo da por bueno **para
    siempre** y ese clip no se regenera nunca más.

    `os.replace` es atómico dentro del mismo sistema de archivos, así que el
    nombre definitivo solo aparece cuando el archivo está entero."""
    parcial = ruta_parcial(destino)
    try:
        yield parcial
        os.replace(parcial, destino)
    finally:
        if os.path.exists(parcial):
            try:
                os.remove(parcial)
            except OSError:
                pass


def marcar_usado(ruta):
    """Un acierto de caché cuenta como uso: es por donde decide la poda."""
    try:
        os.utime(ruta, None)
    except OSError:
        pass


def limpiar_cache(carpeta, max_mb=None):
    """Deja `carpeta` por debajo de `max_mb` borrando los clips menos usados.

    De paso barre los `.parcial` que dejó un render muerto a mitad."""
    tope_bytes = float(CACHE_MAX_MB if max_mb is None else max_mb) * 1024 * 1024
    if not os.path.isdir(carpeta):
        return 0

    borrados, clips = 0, []
    for nombre in os.listdir(carpeta):
        ruta = os.path.join(carpeta, nombre)
        try:
            if nombre.endswith(".parcial"):
                # Nadie lo va a terminar: el proceso que lo escribía ya no está.
                os.remove(ruta)
                borrados += 1
                continue
            if not os.path.isfile(ruta) or not nombre.endswith(".mp4"):
                continue
            st = os.stat(ruta)
            clips.append((st.st_mtime, st.st_size, ruta))
        except OSError:
            continue

    total = sum(c[1] for c in clips)
    if total <= tope_bytes:
        return borrados

    # Del que hace más tiempo que no se usa al más reciente: cada acierto toca
    # el archivo, así que mtime es «última vez que sirvió», no «cuándo se hizo».
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


# ---------------------------------------------------------
# EL LINTER
# ---------------------------------------------------------
def hallazgo_del_modelo(hallazgo):
    """¿El hallazgo del linter es sobre el HTML que se generó?

    Todo lo que no sea el `index.html` del proyecto (o sea: las
    sub-composiciones que ponemos nosotros) es nuestro y el modelo no puede
    arreglarlo; pasárselo como corrección solo ensucia el reintento. Sin ruta
    en el hallazgo se asume que sí, para no tragarse errores reales.

    Esto viene de un fallo real: `compositions/chart-story.html`, vendorizado
    en video_generation, a propósito no declara data-width/data-height —llena
    la caja que le da el anfitrión— y por eso siempre reporta
    `root_missing_dimensions`. Con ese error contando como propio, TODA
    composición quedaba rechazada por algo que el modelo no escribió: la
    corrida 33995064364 se quedó sin un solo clip."""
    ruta = hallazgo.get("file") or hallazgo.get("filePath")
    if not ruta:
        return True
    return os.path.basename(ruta) == "index.html"


def lint(directorio):
    """Errores del linter del CLI, ya formateados para dárselos al modelo, o
    None si la composición está limpia.

    El linter no abre navegador y tarda ~1s, contra los 20-30s de un render:
    atrapa los incumplimientos del contrato (timeline sin registrar, CDN
    externo, data-duration fuera de rango) antes de pagar un render que iba a
    fallar igual. Y devuelve un `fixHint` por error, que es lo que sube de
    verdad la tasa de acierto del reintento."""
    try:
        res = subprocess.run(
            comando_cli() + ["lint", directorio, "--json"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=TIMEOUT_LINT_SEG, env=entorno_cli(),
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
    for hallazgo in datos.get("findings", []):
        if hallazgo.get("severity") != "error":
            continue
        if not hallazgo_del_modelo(hallazgo):
            continue
        linea = f"- {hallazgo.get('code', 'error')}: {hallazgo.get('message', '')}"
        # El linter trae la corrección concreta; se la pasamos tal cual al
        # modelo, que acierta mucho más que con solo el mensaje de error.
        if hallazgo.get("fixHint"):
            linea += f"\n  Cómo se arregla: {hallazgo['fixHint']}"
        errores.append(linea)
    if not errores:
        return None
    return "El linter de HyperFrames reportó errores:\n" + "\n".join(errores[:10])


# ---------------------------------------------------------
# HTML
# ---------------------------------------------------------
def limpiar_html(texto):
    """Quita las vallas de markdown que el modelo a veces añade pese al prompt."""
    texto = (texto or "").strip()
    texto = re.sub(r"^```(?:html)?\s*", "", texto)
    texto = re.sub(r"\s*```$", "", texto)
    return texto.strip()
