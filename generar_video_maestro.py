import os
import re
import sys
import time
import json
import glob
import shutil
import random
import asyncio
import logging
import hashlib
import threading
import subprocess
import textwrap
import tempfile
import collections
import unicodedata
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from PIL import Image, ImageDraw, ImageFont

import narrador   # género de quien narra: decide la voz del video

try:
    import edge_tts
except ImportError:
    edge_tts = None

# ---------------------------------------------------------
# CONFIGURACIÓN GLOBAL Y VARIABLES DE ESTADO
# ---------------------------------------------------------
ES_ANDROID = 'PREFIX' in os.environ or os.path.exists('/sdcard')

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# Las fuentes viajan en el repo en vez de depender de que estén instaladas en
# el sistema: pedir una fuente ausente no falla, libass la sustituye en
# silencio (p.ej. "Montserrat Black" -> DejaVu Sans en peso normal), así que
# el video salía con una tipografía que nadie eligió. Con fontsdir apuntando
# aquí, el resultado es idéntico en el teléfono y en la PC.
RUTA_FUENTES = os.path.join(BASE_DIR, "fuentes")

# Estilos de subtítulo disponibles. La idea es que esto sea lo único que haya
# que tocar (desde config.json, o más adelante desde un selector) para cambiar
# cómo se ven, sin meter mano en la generación del .ass.
ESTILOS_SUBTITULOS = ("frase_activa", "relleno", "pop")

SUBTITULOS_DEFAULT = {
    "estilo": "frase_activa",
    "fuente": "Anton",
    # Cuántas palabras se ven en pantalla a la vez. Con 1 se pierde el efecto
    # de "palabra activa dentro de la frase" (no hay frase que resaltar).
    "palabras_por_frase_short": 4,
    "palabras_por_frase_largo": 6,
    "tamano_short": 84,
    "tamano_largo": 104,
    "color_texto": "#FFFFFF",
    "color_activo": "#3BF07A",
    "color_borde": "#000000",
    "grosor_borde": 7,
    "sombra": 0,
    "mayusculas": True,
    "italica": True,
    # Cuánto crece la palabra que se está diciendo, en % (100 = sin crecer).
    "escala_activa": 112,
    # Si trae más de un color, el resalte va rotando entre ellos en vez de
    # usar siempre color_activo. Vacío = usar color_activo.
    "colores_resalte": ["#3BF07A", "#3BE0F0", "#FFE14D", "#FF6B6B"],
    # Con True, solo se pintan las palabras con carga (sustantivos, verbos):
    # las de relleno ("que", "la", "de") se quedan del color del texto. Es lo
    # que hace que el resalte se sienta como acento y no como un metrónomo.
    "resaltar_solo_clave": True,
    "min_letras_resalte": 5,
}

# Palabras que no aportan y por eso no se pintan cuando resaltar_solo_clave
# está activo. No pretende ser exhaustivo: sobran las más frecuentes.
PALABRAS_SIN_CARGA = {
    "que", "de", "la", "el", "los", "las", "un", "una", "unos", "unas", "y", "o",
    "a", "en", "con", "por", "para", "su", "sus", "mi", "mis", "tu", "tus", "se",
    "lo", "le", "les", "me", "te", "nos", "es", "era", "fue", "ser", "al", "del",
    "no", "si", "ya", "pero", "como", "cuando", "porque", "eso", "esa", "ese",
    "esto", "esta", "este", "yo", "él", "ella", "ellos", "muy", "más", "ni",
}

# Presets: paquetes con nombre de los valores de arriba. La idea es no volver
# a cambiar los defaults para probar un look — se agrega un preset y se elige,
# así lo que ya te gusta sigue saliendo igual aunque se sumen opciones nuevas.
#
# Se eligen con config.json {"subtitulos": {"preset": "nombre"}} o, para una
# sola corrida, con --estilo nombre. Lo que pongas suelto en "subtitulos"
# pisa al preset, para poder tomar uno y cambiarle un detalle.
PRESETS_SUBTITULOS = {
    # Vacío a propósito: son los defaults tal cual.
    # Frase completa legible, y solo las palabras con carga se pintan, con el
    # color rotando — que es lo medido en los canales del género.
    "predeterminado": {},

    # El look del primer video renderizado: TODAS las palabras activas del
    # mismo verde, sin rotación ni filtro de palabras vacías.
    "verde_fijo": {
        "colores_resalte": [],
        "resaltar_solo_clave": False,
        "color_activo": "#3BF07A",
    },

    "amarillo_clasico": {
        "estilo": "relleno",
        "color_texto": "#FFFFFF",
        "color_activo": "#FFE14D",
        "italica": False,
        "escala_activa": 100,
        # El karaoke por relleno se lee mal si el color va cambiando: lo que
        # comunica es "esto ya se dijo", y eso pide un solo color.
        "colores_resalte": [],
        "resaltar_solo_clave": False,
    },

    "una_palabra": {
        "estilo": "pop",
        "fuente": "Montserrat Black",
        "color_texto": "#FFFFFF",
        "tamano_short": 96,
        "tamano_largo": 120,
        # Una palabra a la vez, todas del mismo color (sin rotación).
        "colores_resalte": [],
        "resaltar_solo_clave": False,
    },

    "sobrio": {
        "estilo": "frase_activa",
        "fuente": "Archivo Black",
        "color_activo": "#FFFFFF",
        "italica": False,
        "escala_activa": 104,
        "grosor_borde": 5,
        # Sin color: la palabra activa solo crece. Lo más discreto.
        "colores_resalte": [],
        "resaltar_solo_clave": False,
    },

    "alto_contraste": {
        "estilo": "frase_activa",
        "fuente": "Bebas Neue",
        "color_activo": "#FF3B30",
        "tamano_short": 100,
        "tamano_largo": 124,
        "palabras_por_frase_short": 3,
        "colores_resalte": [],
        "resaltar_solo_clave": False,
    },

    # Igual que el predeterminado en el trato del color, pero con una sola
    # palabra en pantalla: es el formato exacto del video de referencia.
    "palabras_clave": {
        "estilo": "pop",
        "fuente": "Montserrat Black",
        "tamano_short": 96,
        "tamano_largo": 120,
    },
}

CONFIG_DEFAULT = {
    "carpeta_salida": "/sdcard/DCIM/Videos creados" if ES_ANDROID else os.path.join(os.path.expanduser("~"), "Desktop", "Videos Creados"),
    "duracion_max_short_sec": 180.0,
    "voz_masculina": "es-MX-JorgeNeural",
    "voz_femenina": "es-MX-DaliaNeural",
    "reintentar_existentes": False,
    "subtitulos": dict(SUBTITULOS_DEFAULT),
    # La tarjeta de intro de los videos largos ocupa todo el cuadro en vez de
    # quedar como una tarjetita centrada.
    "tarjeta_intro_pantalla_completa_en_largos": True,
    # Cómo se dibuja la tarjeta cuando NO hay tarjeta_plantilla.png:
    #   "velo_oscuro"   - velo translúcido sobre el gameplay, título en blanco
    #   "hoja_blanca"   - fondo blanco opaco, título en negro (el de antes)
    # Con plantilla propia esto no aplica: manda tu imagen.
    "tarjeta_intro_respaldo": "velo_oscuro",
    # Velo blanco encima del video de fondo, de 0.0 (nada) a 1.0. Estaba fijo
    # en 0.30, que lava el gameplay y deja todo lechoso; los canales del
    # género no lo usan (se apoyan en el contorno grueso del subtítulo para
    # la legibilidad, no en apagar el fondo).
    "velo_blanco_fondo": 0.0,
    # Efecto que suena una vez, al terminar la tarjeta de intro. Es el nombre
    # base de un archivo en la carpeta del repo; si hay varios que empiezan
    # igual (efecto_transicion_1.mp3, _2.mp3...) se elige uno al azar. Con
    # cadena vacía se desactiva.
    "sonido_transicion": "efecto_transicion",
    "volumen_sonido_transicion": 0.5,
    # Niveles de la mezcla. Se fijan a mano (amix va con normalize=0), así
    # que son absolutos: subir la música NO baja la locución sola. La música
    # va muy por debajo a propósito — compite con la voz en el rango medio y
    # a 0.20 ya empieza a tapar consonantes en el altavoz de un teléfono.
    "volumen_musica": 0.04,
    "volumen_locucion": 0.5,
    # Reparto del material de fondo por duración del video. Los archivos de
    # gameplay muy pesados (decenas de GB) se reservan para los videos largos:
    # cortar 40 segundos de un archivo de 24 GB obliga a ffmpeg a recorrer un
    # índice enorme por cada corte, y un short se pasa más tiempo buscando el
    # punto de corte que renderizando. Para los videos por debajo del umbral se
    # usan solo los fondos ligeros — uno o varios, los que haya.
    "umbral_video_largo_seg": 240.0,
    "fondo_max_gb_video_corto": 5.0,
    # Segundos que el efecto se adelanta respecto al final de la tarjeta.
    # Con 0 arranca justo cuando empieza a hablar y compite con la primera
    # palabra; adelantarlo un poco hace que su golpe caiga en la transición
    # y la voz entre limpia después.
    "adelanto_sonido_transicion": 0.25,
}

def resolver_subtitulos(pedido, preset=None):
    """Arma la configuración de subtítulos en cascada:

        defaults  ->  preset  ->  lo que pusiste suelto en "subtitulos"

    Así puedes tomar un preset y cambiarle un detalle sin copiar el resto, y
    lo que ya funciona sigue igual aunque se agreguen presets nuevos.
    """
    pedido = dict(pedido or {})
    nombre = preset or pedido.pop("preset", None) or "predeterminado"
    if nombre not in PRESETS_SUBTITULOS:
        print(
            f"⚠️ Preset de subtítulos desconocido: '{nombre}'. "
            f"Disponibles: {', '.join(PRESETS_SUBTITULOS)}. Se usa 'predeterminado'."
        )
        nombre = "predeterminado"

    subs = dict(SUBTITULOS_DEFAULT)
    subs.update(PRESETS_SUBTITULOS[nombre])
    pedido.pop("preset", None)
    subs.update(pedido)
    subs["preset"] = nombre
    return subs


def cargar_config(ruta="config.json", preset=None):
    cfg = dict(CONFIG_DEFAULT)
    usuario_subs = {}
    if os.path.exists(ruta):
        try:
            with open(ruta, "r", encoding="utf-8") as f:
                usuario = json.load(f)
            usuario_subs = usuario.pop("subtitulos", {}) or {}
            cfg.update(usuario)
        except Exception as exc:
            print(f"⚠️ No se pudo leer {ruta}, usando valores por defecto: {exc}")
    cfg["subtitulos"] = resolver_subtitulos(usuario_subs, preset)
    return cfg

CONFIG = cargar_config()
CARPETA_SALIDA = CONFIG["carpeta_salida"]
DURACION_MAX_SHORT_SEC = CONFIG["duracion_max_short_sec"]

# ---------------------------------------------------------
# LOGGING A ARCHIVO (además del HUD visual en consola)
# ---------------------------------------------------------
logger = logging.getLogger("video_maestro")
logger.setLevel(logging.INFO)
_log_dir = CARPETA_SALIDA if os.path.isdir(os.path.dirname(CARPETA_SALIDA) or ".") else os.getcwd()
try:
    os.makedirs(CARPETA_SALIDA, exist_ok=True)
    _handler = logging.FileHandler(os.path.join(CARPETA_SALIDA, "video_maestro.log"), encoding="utf-8")
except Exception:
    _handler = logging.FileHandler("video_maestro.log", encoding="utf-8")
_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
logger.addHandler(_handler)

# Variables globales para el HUD flotante
ultimo_refresco_hw = 0
cpu_pct, gpu_pct, ram_pct, disk_pct = "--%", "--%", "--%", "--GB"
temp_pct, bat_pct = "--°C", "--%"
prev_proc_total = 0
prev_proc_idle = 0

# ---------------------------------------------------------
# HILO ASÍNCRONO PARA TERMUX (Evita el lag por Throttling)
# ---------------------------------------------------------
def termux_monitor_daemon():
    """Se ejecuta en segundo plano consultando la temperatura y batería sin bloquear el script."""
    global temp_pct, bat_pct
    while True:
        try:
            res = subprocess.run(["termux-battery-status"], capture_output=True, text=True, timeout=2.0)
            if res.returncode == 0:
                data = json.loads(res.stdout)
                t = data.get('temperature', 0)
                b = data.get('percentage', 0)
                if t > 0: temp_pct = f"{t}°C"
                if b > 0: bat_pct = f"{b}%"
        except Exception: pass
        time.sleep(3)

if ES_ANDROID:
    hilo_termux = threading.Thread(target=termux_monitor_daemon, daemon=True)
    hilo_termux.start()


def ejecutar_comando(cmd, descripcion="Comando", timeout=None, check=True):
    """Ejecuta un proceso y conserva stderr para diagnósticos útiles."""
    try:
        res = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"{descripcion}: tiempo de espera agotado.") from exc
    except FileNotFoundError as exc:
        raise RuntimeError(
            f"{descripcion}: no se encontró el ejecutable '{cmd[0]}'."
        ) from exc

    if check and res.returncode != 0:
        detalle = (res.stderr or res.stdout or "").strip()
        if len(detalle) > 2500:
            detalle = detalle[-2500:]
        raise RuntimeError(
            f"{descripcion} falló (código {res.returncode}).\n{detalle}"
        )

    return res


def archivo_valido(ruta):
    return bool(ruta) and os.path.isfile(ruta) and os.path.getsize(ruta) > 0


def comprobar_dependencias():
    """Comprueba las herramientas externas necesarias antes del lote."""
    faltantes = []
    for exe in ("ffmpeg", "ffprobe"):
        if shutil.which(exe) is None:
            faltantes.append(exe)

    if faltantes:
        raise RuntimeError(
            "Faltan dependencias externas: " + ", ".join(faltantes)
        )

class GestorTemporales:
    """Directorio temporal aislado por historia; evita colisiones entre procesos."""
    def __init__(self, prefijo="video_maestro_"):
        self._tmp = tempfile.TemporaryDirectory(prefix=prefijo)
        self.directorio = self._tmp.name
        self.archivos = []

    def registrar(self, ruta):
        ruta = str(ruta)
        if not os.path.isabs(ruta):
            ruta = os.path.join(self.directorio, ruta)
        self.archivos.append(ruta)
        return ruta

    def limpiar(self):
        self._tmp.cleanup()

def obtener_metricas_hardware(old_cpu, old_gpu, old_ram, old_disk):
    cpu_str, gpu_str, ram_str, disk_str = old_cpu, old_gpu, old_ram, old_disk
    cpu_ok = False

    try:
        import psutil
        val = psutil.cpu_percent(interval=None)
        if val > 0: 
            cpu_str = f"{val:.0f}%"
            cpu_ok = True
    except Exception: pass 

    if not cpu_ok and os.path.exists('/proc/stat'):
        try:
            global prev_proc_total, prev_proc_idle
            with open('/proc/stat', 'r') as f:
                fields = [float(x) for x in f.readline().split()[1:]]
                idle, total = fields[3], sum(fields)
                diff_idle, diff_total = idle - prev_proc_idle, total - prev_proc_total
                prev_proc_total, prev_proc_idle = total, idle
                if diff_total > 0:
                    cpu_str = f"{max(0.0, min(100.0, (1.0 - (diff_idle / diff_total)) * 100.0)):.0f}%"
                    cpu_ok = True
        except Exception: pass

    if not cpu_ok:
        try:
            res_top = subprocess.run(["top", "-n", "1"], capture_output=True, text=True, timeout=1.0)
            m_idle = re.search(r'(\d+)%idle', res_top.stdout)
            m_cpu = re.search(r'(\d+)%cpu', res_top.stdout)
            if m_idle and m_cpu:
                c, i = float(m_cpu.group(1)), float(m_idle.group(1))
                cpu_str = f"{((c - i) / max(c, 1)) * 100:.0f}%"
            else:
                m_id = re.search(r'(\d+\.?\d*)\s*id', res_top.stdout)
                if m_id: cpu_str = f"{100.0 - float(m_id.group(1)):.0f}%"
        except Exception: pass

    try:
        with open('/proc/meminfo', 'r') as f:
            lines = f.readlines()
            total_k = float(lines[0].split()[1])
            avail_k = float(next((l for l in lines if 'Available' in l), lines[1]).split()[1])
            ram_str = f"{(1.0 - (avail_k / total_k)) * 100.0:.0f}%"
    except Exception: pass

    try:
        free_gb = shutil.disk_usage(os.path.dirname(CARPETA_SALIDA) if os.path.exists(os.path.dirname(CARPETA_SALIDA)) else "/").free / (1024**3)
        disk_str = f"{free_gb:.1f}GB"
    except Exception: pass

    if not ES_ANDROID:
        try:
            res = subprocess.run(["nvidia-smi", "--query-gpu=utilization.gpu", "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=0.5)
            if res.returncode == 0 and res.stdout.strip(): gpu_str = f"{res.stdout.strip()}%"
        except Exception: pass

    return cpu_str, gpu_str, ram_str, disk_str

def actualizar_hud(mensaje_lista, finalizado=False):
    global ultimo_refresco_hw, cpu_pct, gpu_pct, ram_pct, disk_pct
    
    if time.time() - ultimo_refresco_hw > 2.0:
        cpu_pct, gpu_pct, ram_pct, disk_pct = obtener_metricas_hardware(cpu_pct, gpu_pct, ram_pct, disk_pct)
        ultimo_refresco_hw = time.time()
        
    cpu_disp = f"CPU:{cpu_pct} | " if cpu_pct not in ("0%", "--%") else ""
    if ES_ANDROID: 
        hw_str = f"{cpu_disp}RAM:{ram_pct} | Libre:{disk_pct} | Temp:{temp_pct} | Bat:{bat_pct}"
    else: 
        hw_str = f"{cpu_disp}GPU:{gpu_pct} | RAM:{ram_pct} | Libre:{disk_pct}"
    
    if isinstance(mensaje_lista, str):
        mensaje_lista = [mensaje_lista]
        
    term_w = shutil.get_terminal_size((40, 24)).columns
    out = ""
    
    if not finalizado:
        for m in mensaje_lista:
            out += f"\r{m[:term_w - 1]}\033[K\n"
        hw_recortado = f" └─ 📊 {hw_str}"[:term_w - 1]
        out += f"\r{hw_recortado}\033[K"
        out += f"\033[{len(mensaje_lista)}A\r"
    else:
        out += f"\r{mensaje_lista[0][:term_w - 3]} ✅\033[K\n"
        for _ in range(len(mensaje_lista)):
            out += "\r\033[K\n"
        out += f"\033[{len(mensaje_lista)}A\r"
        
    sys.stdout.write(out)
    sys.stdout.flush()

def obtener_fuente_bold(tamano=46):
    # Primero las fuentes del repo: son las mismas que usan los subtítulos,
    # así la tarjeta de intro y los subtítulos comparten tipografía y el
    # resultado no depende de qué tenga instalado cada dispositivo. Antes se
    # iba directo a las del sistema, y donde no hubiera ninguna se caía a
    # load_default(), que es un mapa de bits y IGNORA el tamaño pedido: el
    # título salía diminuto sin que nada avisara.
    if os.path.isdir(RUTA_FUENTES):
        preferida = _cfg_subs().get("fuente", "")
        candidatas = sorted(
            (f for f in os.listdir(RUTA_FUENTES) if f.lower().endswith((".ttf", ".otf"))),
            # La configurada para subtítulos primero, si está.
            key=lambda f: (preferida.replace(" ", "").lower() not in f.replace("-", "").lower(), f),
        )
        for f in candidatas:
            try:
                return ImageFont.truetype(os.path.join(RUTA_FUENTES, f), tamano)
            except Exception:
                pass

    rutas_preferidas = [
        "/system/fonts/Roboto-Black.ttf",
        "/system/fonts/Roboto-Bold.ttf",
        "/system/fonts/NotoSans-Bold.ttf",
        "/system/fonts/DroidSans-Bold.ttf",
        "/system/fonts/SamsungSans-Bold.ttf",
        "C:\\Windows\\Fonts\\impact.ttf",
        "C:\\Windows\\Fonts\\arialbd.ttf"
    ]
    for r in rutas_preferidas:
        if os.path.exists(r):
            try: return ImageFont.truetype(r, tamano)
            except Exception: pass
            
    dir_fonts = "/system/fonts/"
    if os.path.exists(dir_fonts):
        try:
            archivos = os.listdir(dir_fonts)
            for f in archivos:
                if f.endswith(".ttf") and "bold" in f.lower():
                    try: return ImageFont.truetype(os.path.join(dir_fonts, f), tamano)
                    except Exception: pass
            for f in archivos:
                if f.endswith(".ttf"):
                    try: return ImageFont.truetype(os.path.join(dir_fonts, f), tamano)
                    except Exception: pass
        except Exception: pass
    return ImageFont.load_default()

def limpiar_texto_seguro(texto):
    if not texto: return "Historia sin texto"
    t = re.sub(r'[\"\'«»“”‘’]', '', texto.replace('\r', ' ').replace('\n', ' '))
    t = re.sub(r'[^a-zA-ZáéíóúÁÉÍÓÚüÜñÑ0-9\s\.,;:¿?¡!()-]', '', t)
    return ' '.join(t.split()) or "Historia sin texto"

def medir_duracion_media(ruta_archivo):
    try:
        if not archivo_valido(ruta_archivo):
            return 0.0
        res = ejecutar_comando(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "json", ruta_archivo],
            "ffprobe",
            timeout=5
        )
        dur = float(json.loads(res.stdout)['format']['duration'])
        return max(0.0, dur)
    except Exception:
        return 0.0

def _peso_archivo(ruta):
    try:
        return os.path.getsize(ruta)
    except OSError:
        return 0


def crear_fondo_multi_corte(duracion_requerida_sec, es_short, gestor_temp, num_index=1):
    exts = ('.webm', '.mp4', '.mkv', '.mov')
    prefijo = "fondo_vertical" if es_short else "fondo_horizontal"
    cands = [f for f in os.listdir('.') if f.endswith(exts) and not f.startswith(('0','1','2','3','temp_','fondo_ensamblado'))]
    # Preferencia en orden: material marcado para ESTE formato; si no hay,
    # cualquier fondo; si tampoco, lo que haya. El escalado de abajo recorta
    # al centro, así que un 16:9 sirve para shorts y viceversa — por eso caer
    # al siguiente nivel es correcto y no un apaño.
    #
    # (Antes la condición era `prefijo in c or 'fondo' in c`, y ese `or`
    # anulaba al primer filtro: cualquier archivo fondo_* entraba siempre,
    # así que los shorts tomaban fondos horizontales aun teniendo verticales
    # disponibles.)
    vids_base = (
        [c for c in cands if prefijo in c]
        or [c for c in cands if 'fondo' in c]
        or cands
    )
    # Los videos cortos no tocan el material pesado: ver arriba
    # ("umbral_video_largo_seg" / "fondo_max_gb_video_corto"). Si el filtro
    # dejara la lista vacía se cae al archivo más ligero que haya, porque
    # quedarse sin fondo aborta el render entero.
    umbral_largo = float(CONFIG.get("umbral_video_largo_seg", 240.0) or 240.0)
    max_bytes_corto = float(CONFIG.get("fondo_max_gb_video_corto", 5.0) or 5.0) * (1024 ** 3)
    if duracion_requerida_sec < umbral_largo and len(vids_base) > 1:
        ligeros = [c for c in vids_base if _peso_archivo(c) <= max_bytes_corto]
        if ligeros and len(ligeros) < len(vids_base):
            pesados = [c for c in vids_base if c not in ligeros]
            print(f" ├─ 🪶 Video corto ({duracion_requerida_sec:.0f}s): "
                  f"{len(ligeros)} fondo(s) ligero(s); se omite(n) {', '.join(pesados)}")
            vids_base = ligeros
        elif not ligeros:
            # Todos pesan de más: al menos usar el más chico, no uno al azar.
            vids_base = [min(vids_base, key=_peso_archivo)]
            print(f" ├─ ⚠️  Ningún fondo baja de {max_bytes_corto/(1024**3):.1f} GB; "
                  f"se usa el más ligero ({vids_base[0]}).")

    # --fondo limita el material a un archivo concreto. Sirve para rehacer un
    # video con otro gameplay cuando hay varios; sin él se usan todos, que es
    # lo que da variedad. Si el filtro no deja nada se ignora, porque quedarse
    # sin fondo aborta el render entero.
    filtro_fondo = (CONFIG.get("_fondo_forzado") or "").strip().lower()
    if filtro_fondo:
        acotados = [c for c in vids_base if filtro_fondo in c.lower()]
        if acotados:
            vids_base = acotados
        else:
            print(f" ├─ ⚠️  Ningún fondo coincide con '{filtro_fondo}'; se usan todos.")
    if not vids_base: return None

    w_res, h_res = (1080, 1920) if es_short else (1920, 1080)
    filtro = f"scale={w_res}:{h_res}:force_original_aspect_ratio=increase,crop={w_res}:{h_res},fps=30"

    acumulado = 0.0
    archivos_clips = []
    
    anch = max(10, shutil.get_terminal_size((40, 24)).columns - 35)
    txt_base = " ├─ 🎞️ [2/4] Cortes:"
    actualizar_hud([f"{txt_base} [  0.0%] [{' ' * anch}]"])

    fallos_consecutivos = 0
    while acumulado < duracion_requerida_sec:
        if fallos_consecutivos >= 8:
            logger.error("Demasiados cortes fallidos seguidos, abortando ensamblado de fondo.")
            break
        clip_dur = min(random.uniform(6.0, 12.0), duracion_requerida_sec - acumulado)
        vid_elegido = random.choice(vids_base)
        dur_total_vid = medir_duracion_media(vid_elegido)
        
        ss = random.uniform(0.5, dur_total_vid - clip_dur - 1.0) if dur_total_vid > (clip_dur + 2.0) else 0.0
        nom_clip = gestor_temp.registrar(f"temp_clip_{num_index}_{len(archivos_clips)}.mp4")
        
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{ss:.2f}", "-i", vid_elegido, "-t", f"{clip_dur:.2f}", "-vf", filtro, "-c:v", "libx264", "-preset", "ultrafast", "-an", nom_clip]
        res_clip = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")

        if res_clip.returncode != 0 or not archivo_valido(nom_clip):
            logger.warning(f"Corte descartado ({vid_elegido} @ {ss:.2f}s): {(res_clip.stderr or '').strip()[-300:]}")
            fallos_consecutivos += 1
            continue

        fallos_consecutivos = 0
        archivos_clips.append(nom_clip)
        acumulado += clip_dur
        
        pct = min(100.0, (acumulado/duracion_requerida_sec)*100.0)
        bl = int(anch * pct / 100)
        actualizar_hud([f"{txt_base} [{pct:5.1f}%] [{'█'*bl}{' '*(anch-bl)}]"])

    if not archivos_clips: 
        actualizar_hud([f"{txt_base} [ Fallo] [{'❌'*anch}]"], True)
        return None

    actualizar_hud([f" ├─ 🎞️ [2/4] Uniendo: [{100.0:5.1f}%] [{'█'*anch}]"])
    txt_concat = gestor_temp.registrar(f"temp_concat_list_{num_index}.txt")
    with open(txt_concat, 'w', encoding='utf-8') as f:
        for c in archivos_clips:
            ruta = os.path.abspath(c).replace("'", "'\\''")
            f.write(f"file '{ruta}'\n")

    salida_fondo = gestor_temp.registrar(f"fondo_ensamblado_{num_index}.mp4")
    try:
        ejecutar_comando(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
             "-f", "concat", "-safe", "0", "-i", txt_concat,
             "-c", "copy", salida_fondo],
            "FFmpeg: unión de fondos"
        )
    except RuntimeError:
        return None

    if archivo_valido(salida_fondo):
        actualizar_hud([f"{txt_base} [100.0%] [{'█'*anch}]"], True)
        return salida_fondo
    else:
        actualizar_hud([f"{txt_base} [ Fallo] [{'❌'*anch}]"], True)
        return None

# Vocabulario por género. Se compara sobre texto sin acentos y con límites
# de palabra, así que aquí va todo sin tildes. Cada entrada es una raíz: se
# admite lo que siga pegado (llor -> lloré, llorando, lloraba), por eso las
# raíces tienen que ser lo bastante largas para no cazar otra cosa. "veng"
# cazaría "vengo de casa", así que las formas de vengar van explícitas.
VOCABULARIO_EMOCION = {
    "venganza": [
        "venganza", "vengarme", "vengarse", "vengue", "vengo a cobrar",
        "vengativ", "justicia", "injusticia", "demand", "abogad", "denunci",
        "juicio", "tribunal", "despidieron", "despedid", "karma",
        "se lo merecia", "merecido", "escarmiento", "le salio caro",
        "pago caro", "desenmascar", "represalia", "delante de todos",
        "recuper lo mio", "me las pago", "vuelta la tortilla", "castig",
    ],
    "suspenso": [
        "suspenso", "misterio", "secreto", "ocult", "escondi", "descubri",
        "revelacion", "revelo", "sospech", "inquietante", "escalofri",
        "aterrad", "madrugada", "anonim", "desconocid", "desaparecio",
        "nunca supe", "fraude", "estafa", "mintio", "mentira", "camara de",
        "no me lo esperaba", "algo no encajaba", "sin explicacion",
    ],
    "drama": [
        "drama", "triste", "tristeza", "luto", "duelo", "dolor", "lagrima",
        "llor", "murio", "muerte", "fallecio", "fallecimiento", "funeral",
        "enfermedad", "cancer", "hospital", "abandon", "divorcio",
        "traicion", "humill", "soledad", "arrepent", "perdon", "despedida",
        "nunca volvi a ver", "se me rompio", "no pude despedirme",
    ],
    "comedia": [
        "comedia", "humor", "carcajada", "chiste", "divertid", "gracios",
        "ridicul", "absurd", "comic", "hilarante", "payaso", "torpe",
        "verguenza ajena", "no pude parar de reir", "me parti de risa",
        "risas", "reimos", "se me escapo la risa",
    ],
}

# Lo que Gemini u otra fuente pueda escribir en "# Emocion:" sin ser una de
# las cuatro palabras exactas. Lo que no esté aquí no se descarta: se pasa a
# leer el texto, que es mejor pista que rendirse.
SINONIMOS_EMOCION = {
    "venganza": "venganza", "justicia": "venganza", "revancha": "venganza",
    "desquite": "venganza", "karma": "venganza",
    "suspenso": "suspenso", "misterio": "suspenso", "tension": "suspenso",
    "intriga": "suspenso", "thriller": "suspenso", "terror": "suspenso",
    "drama": "drama", "tristeza": "drama", "melancolia": "drama",
    "emotivo": "drama", "duelo": "drama", "perdida": "drama",
    "comedia": "comedia", "humor": "comedia", "gracioso": "comedia",
    "divertido": "comedia",
}

# Orden de desempate. No es alfabético: refleja lo que pide el canal cuando
# una historia toca varios géneros por igual.
PRIORIDAD_EMOCION = ("venganza", "suspenso", "drama", "comedia")


# La misma normalización que usa narrador.py; se reexporta desde allí para no
# tener dos versiones que puedan separarse.
_sin_acentos = narrador._sin_acentos


def puntuar_emociones(texto):
    """Cuántas veces asoma cada género, con el título pesando más.

    Se devuelve el marcador entero y no solo el ganador para poder explicar
    por qué salió lo que salió: el render lo imprime, que si no una
    clasificación rara no hay manera de discutirla.
    """
    lineas = [
        l.strip() for l in texto.splitlines()
        if l.strip() and not l.strip().startswith(("#", "===", "📌", "🎙️"))
    ]
    # El peso extra del título solo tiene sentido si hay título Y cuerpo. Con
    # una sola línea no hay tal cosa: multiplicarla por tres convertía
    # cualquier palabra suelta en señal suficiente.
    if len(lineas) >= 2:
        titulo = _sin_acentos(lineas[0])
        cuerpo = _sin_acentos(" ".join(lineas[1:]))
    else:
        titulo = ""
        cuerpo = _sin_acentos(lineas[0]) if lineas else ""

    marcador = {}
    for emocion, raices in VOCABULARIO_EMOCION.items():
        puntos, en_titulo = 0, 0
        for raiz in raices:
            # \b delante y nada detrás: la raíz puede llevar terminación
            # (llor -> lloraba) pero no puede empezar a media palabra, que es
            # lo que hacía que "absoluto" contara como luto.
            patron = r"\b" + re.escape(raiz)
            en_titulo += len(re.findall(patron, titulo))
            puntos += len(re.findall(patron, cuerpo))
        # El título es una frase pensada para resumir la historia: lo que
        # aparece ahí pesa más que una palabra suelta en mitad del cuerpo.
        marcador[emocion] = {"total": en_titulo * 3 + puntos,
                             "titulo": en_titulo, "cuerpo": puntos}
    return marcador


def detectar_emocion_historia(texto, explicar=False):
    """El género de la historia: decide la música y el color del montaje.

    Manda lo que declare "# Emocion:" —lo escribe script_writer.py y viene
    de haber leído la historia entera—. Si no hay declaración, o dice algo
    que no reconocemos, se lee el texto en vez de rendirse a 'fondo': un
    guion escrito a mano o recuperado no trae cabecera, y esos son justo los
    que acababan todos con la misma música genérica.
    """
    m = re.search(r"emoci[oó]n\s*[:=]\s*([\w-]+)", texto, re.IGNORECASE)
    if m:
        declarada = _sin_acentos(m.group(1))
        if declarada in SINONIMOS_EMOCION:
            return SINONIMOS_EMOCION[declarada]

    marcador = puntuar_emociones(texto)
    if explicar:
        orden = sorted(marcador.items(), key=lambda kv: -kv[1]["total"])
        print("   Marcador de género: " + "  ".join(
            f"{e}={d['total']}" + (f" (título {d['titulo']})" if d["titulo"] else "")
            for e, d in orden if d["total"]) or "   Marcador de género: vacío")

    mejor = max(
        PRIORIDAD_EMOCION,
        key=lambda e: (marcador[e]["total"], marcador[e]["titulo"],
                       -PRIORIDAD_EMOCION.index(e)),
    )
    # Con un solo acierto suelto no hay señal: una historia de oficina que
    # menciona "abogado" de pasada no es una historia de venganza. Mejor la
    # música neutra que una equivocada, que se nota más.
    return mejor if marcador[mejor]["total"] >= 2 else "fondo"

def seleccionar_fondo_video(es_short):
    return next((f for p in ["fondo_vertical" if es_short else "fondo_horizontal", "fondo_gameplay"] for ext in ['.webm', '.mp4', '.mkv'] if os.path.exists(p+ext)), next((f for f in os.listdir('.') if f.endswith(('.webm', '.mp4', '.mkv'))), None))

def origen_emocion(texto):
    """De dónde salió el género, para que la línea del render se pueda leer.

    Si sale mal, saber si vino de la cabecera del guion (culpa de Gemini al
    escribirlo) o del recuento de palabras (culpa del vocabulario de aquí)
    es la diferencia entre arreglarlo y adivinar.
    """
    m = re.search(r"emoci[oó]n\s*[:=]\s*([\w-]+)", texto, re.IGNORECASE)
    if m and _sin_acentos(m.group(1)) in SINONIMOS_EMOCION:
        return "declarada en el guion"
    marcador = puntuar_emociones(texto)
    vivos = sorted(((d["total"], e) for e, d in marcador.items() if d["total"]), reverse=True)
    if not vivos:
        return "sin pistas en el texto"
    return "del texto: " + ", ".join(f"{e} {t}" for t, e in vivos[:3])


def seleccionar_musica_fondo(emocion='fondo', num=None):
    """Elige al azar entre todas las pistas disponibles para la emoción (p.ej.
    musica_drama_artista_123.mp3, descargadas por actualizar_musica.py), para
    no repetir siempre la misma canción. Si no hay ninguna con ese prefijo
    exacto, cae a musica_fondo_*, y si tampoco hay, a cualquier musica_*.

    Con --musica se puede fijar la pista de antemano (el panel deja oírlas
    antes de renderizar). Es una elección por historia: lo que no se eligió
    sigue saliendo al azar, para que el cron no dependa de que alguien esté
    mirando.
    """
    exts = ('.m4a', '.mp3', '.wav', '.aac')

    def candidatas(prefijo):
        return [
            f for f in os.listdir('.')
            if f.startswith(prefijo) and f.endswith(exts) and archivo_valido(f)
        ]

    forzada = CONFIG.get("_musica_forzada") or {}
    elegida = forzada.get(str(num)) or forzada.get("*")
    if elegida:
        if archivo_valido(elegida):
            return elegida
        print(f" ├─ ⚠️  La música elegida no existe ({elegida}); se usa una al azar.")

    for prefijo in (f"musica_{emocion}", "musica_fondo"):
        opciones = candidatas(prefijo)
        if opciones:
            return random.choice(opciones)

    # Fallback estricto: solamente archivos que empiecen por musica_.
    opciones = candidatas("musica_")
    return random.choice(opciones) if opciones else None

def extraer_fuente_y_autor(texto_raw):
    """Extrae la atribución (# Fuente: / # Autor:) que escribe script_writer.py,
    para que quede constancia del origen y no se presente el contenido como propio."""
    m_fuente = re.search(r'#\s*Fuente:\s*(\S+)', texto_raw, re.IGNORECASE)
    m_autor = re.search(r'#\s*Autor:\s*(.+)', texto_raw, re.IGNORECASE)
    fuente = m_fuente.group(1).strip() if m_fuente else ""
    autor = m_autor.group(1).strip() if m_autor else ""
    return fuente, autor

def decidir_genero_narrador(texto_raw):
    """Con qué voz se narra: la cabecera del guion, comprobada contra el texto.

    La cabecera la escribe Gemini y suele acertar, pero cuando falla nadie la
    corregía y la historia entera salía con la voz cambiada. Lo que se narra
    es el cuerpo, así que si el cuerpo lo contradice con claridad —"me quedé
    callada" no lo dice un narrador masculino— gana el cuerpo.

    Se exige más ventaja para llevar la contraria a la cabecera (3 marcas
    netas) que para decidir cuando no hay cabecera (2): pisar una elección
    deliberada tiene que costar más que rellenar un hueco. Tres concordancias
    independientes apuntando al mismo lado ya no son ruido.
    """
    cabecera = narrador.leer_cabecera_genero(texto_raw)
    del_texto = narrador.detectar_genero_narrador(texto_raw, margen=2)

    if cabecera is None:
        if del_texto:
            print(f" ├─ 🗣️  Sin '# Genero:' en el guion; por el texto se narra en {del_texto}.")
            return del_texto
        print(" ├─ 🗣️  Sin '# Genero:' y el texto no lo aclara; se usa voz masculina.")
        return "masculino"

    contrario = narrador.detectar_genero_narrador(texto_raw, margen=3)
    if contrario and contrario != cabecera:
        marcas = narrador.puntuar_genero(texto_raw)[contrario][:3]
        print(f" ├─ 🗣️  El guion dice {cabecera} pero el texto está en {contrario} "
              f"({', '.join(marcas)}…); se narra en {contrario}.")
        return contrario
    return cabecera


def extraer_titulo_y_cuerpo(texto_raw):
    # VOZ_FORZADA la pone --voz: sirve para rehacer un video con la otra voz
    # sin tener que editar el "# Genero:" del guion a mano.
    forzada = CONFIG.get("_voz_forzada")
    if forzada in ("femenina", "masculina"):
        es_fem = forzada == "femenina"
    else:
        es_fem = decidir_genero_narrador(texto_raw) == "femenino"
    voz_tit, opciones_cue = ("es-MX-DaliaNeural", [("es-MX-DaliaNeural", p) for p in ["-6Hz", "-3Hz", "+0Hz", "+4Hz"]] + [("es-US-PalomaNeural", "+0Hz")]) if es_fem else ("es-MX-JorgeNeural", [("es-MX-JorgeNeural", p) for p in ["-8Hz", "-4Hz", "+0Hz", "+4Hz"]] + [("es-US-AlonsoNeural", "+0Hz")])
    voz_cue, pitch_cue = random.choice(opciones_cue)

    lineas = [l.strip() for l in texto_raw.splitlines() if l.strip() and not l.strip().startswith(('#', '===', '📌', '🎙️'))]
    tit = lineas[0] if lineas else "Historia_de_Reddit"
    fuente_url, autor = extraer_fuente_y_autor(texto_raw)
    return voz_tit, voz_cue, pitch_cue, random.choice(["+15%", "+18%", "+20%"]), detectar_emocion_historia(texto_raw), tit, " ".join(lineas[1:]) if len(lineas) > 1 else tit, re.sub(r'[^\w\s-]', '', tit).strip().replace(' ', '_')[:120] or "Historia", fuente_url, autor

def crear_tarjeta_intro_impecable(titulo, output_png="tarjeta_intro.png", es_short=True):
    if es_short: ancho, alto = 1080, 1920
    else: ancho, alto = 1920, 1080
    
    lienzo = Image.new('RGBA', (ancho, alto), (0, 0, 0, 0))
    plantillas_posibles = ["tarjeta_plantilla.png", "tarjeta_plantilla.jpg", "Tarjeta de inicio.png"]
    plantilla_encontrada = next((p for p in plantillas_posibles if os.path.exists(p)), None)

    if plantilla_encontrada:
        plantilla_original = Image.open(plantilla_encontrada).convert('RGBA')
        w_orig, h_orig = plantilla_original.size
        pantalla_completa = (not es_short) and CONFIG.get("tarjeta_intro_pantalla_completa_en_largos", True)

        if pantalla_completa:
            # Escala para CUBRIR todo el cuadro (como object-fit: cover): se
            # toma el mayor de los dos factores y se recorta lo que sobre, en
            # vez de dejar la tarjeta flotando chiquita en medio del video.
            factor = max(ancho / float(w_orig), alto / float(h_orig))
            target_w, target_h = int(w_orig * factor), int(h_orig * factor)
        else:
            factor_escala = 0.95
            target_w = int(ancho * factor_escala)
            target_h = int(h_orig * (target_w / float(w_orig)))

        plantilla_resized = plantilla_original.resize((target_w, target_h), Image.Resampling.LANCZOS)
        pos_x = (ancho - target_w) // 2
        pos_y = (alto - target_h) // 2 - (120 if es_short else 0)

        lienzo.paste(plantilla_resized, (pos_x, pos_y), plantilla_resized)
        
        draw = ImageDraw.Draw(lienzo)
        titulo_mayus = titulo.upper()
        num_caracteres = len(titulo_mayus)
        
        chars_linea = max(26 if es_short else 38, int(num_caracteres / 3.8))
        lineas_wrap = textwrap.wrap(titulo_mayus, width=chars_linea)
        if len(lineas_wrap) > 4:
            chars_linea = int(num_caracteres / 3.5) + 1
            lineas_wrap = textwrap.wrap(titulo_mayus, width=chars_linea)

        if es_short: font_size = 28 if num_caracteres > 160 else 34 if num_caracteres > 100 else 44
        else: font_size = 36 if num_caracteres > 160 else 46 if num_caracteres > 100 else 56

        espaciado = 8 if es_short else 12
        ancho_borde = 5 if es_short else 7

        if pantalla_completa:
            # Los tamaños de arriba estaban calculados para una tarjeta al 55%
            # del ancho; al ocupar el cuadro entero hay que subirlos en la
            # misma proporción o el título se ve diminuto y perdido.
            escala_texto = target_w / (ancho * 0.55)
            font_size = int(font_size * escala_texto)
            espaciado = int(espaciado * escala_texto)
            ancho_borde = max(ancho_borde, int(ancho_borde * escala_texto))

        # El texto se coloca relativo a la plantilla (ahí está su zona de
        # título), pero si el recorte la dejó fuera del cuadro se centra.
        y_texto = pos_y + int(target_h * 0.58)
        if not (0 < y_texto < alto):
            y_texto = alto // 2

        font_tit = obtener_fuente_bold(font_size)
        draw.multiline_text((ancho // 2, y_texto), "\n".join(lineas_wrap), fill=(255, 255, 255), font=font_tit, anchor="mm", align="center", spacing=espaciado, stroke_width=ancho_borde, stroke_fill=(0, 0, 0))
        lienzo.save(output_png)
    else:
        # Respaldo: no hay tarjeta_plantilla.png. Ojo, esto es lo que se ve si
        # la plantilla se perdió (está en .gitignore por ser *.png, así que no
        # sobrevive a un reclonado del repo) — estado.py lo avisa.
        draw = ImageDraw.Draw(lienzo)
        pantalla_completa = (not es_short) and CONFIG.get("tarjeta_intro_pantalla_completa_en_largos", True)
        titulo_mayus = titulo.upper()

        velo = CONFIG.get("tarjeta_intro_respaldo", "velo_oscuro") == "velo_oscuro"

        if pantalla_completa:
            # A pantalla completa, una tarjeta blanca opaca tapa el gameplay
            # entero y se lee como una hoja en blanco, no como una intro. Un
            # velo oscuro con el título en blanco encima deja ver el fondo y
            # se lee como portada. "hoja_blanca" conserva el comportamiento
            # anterior para quien lo prefiera.
            if velo:
                draw.rectangle([0, 0, ancho, alto], fill=(10, 13, 18, 205))
                color_texto, color_borde = (255, 255, 255), (0, 0, 0)
            else:
                draw.rectangle([0, 0, ancho, alto], fill=(255, 255, 255, 250))
                color_texto, color_borde = (0, 0, 0), (255, 255, 255)
            chars_linea = max(28, int(len(titulo_mayus) / 3.2))
            lineas_wrap = textwrap.wrap(titulo_mayus, width=chars_linea)

            # El tamaño se ajusta midiendo, no estimando por número de
            # caracteres: cuánto ocupa depende de la fuente (Anton es
            # condensada, Montserrat Black no) y de qué letras toquen. Se
            # baja hasta que la línea más ancha quepa con margen.
            ancho_max = int(ancho * 0.88)
            font_size = 124
            while font_size > 40:
                font_tit = obtener_fuente_bold(font_size)
                try:
                    mas_ancha = max(draw.textlength(l, font=font_tit) for l in lineas_wrap)
                except Exception:
                    break
                if mas_ancha <= ancho_max:
                    break
                font_size -= 6
            font_tit = obtener_fuente_bold(font_size)
            draw.multiline_text(
                (ancho // 2, alto // 2), "\n".join(lineas_wrap),
                fill=color_texto, font=font_tit, anchor="mm", align="center",
                spacing=18, stroke_width=6 if velo else 0, stroke_fill=color_borde,
            )
        else:
            card_w = int(ancho * 0.88)
            card_h = int(alto * (0.28 if es_short else 0.35))
            card_x, card_y = (ancho - card_w) // 2, ((alto - card_h) // 2) - 120

            draw.rounded_rectangle([card_x, card_y, card_x + card_w, card_y + card_h], radius=30, fill=(255, 255, 255, 250))
            draw.ellipse([card_x + 40, card_y + 35, card_x + 90, card_y + 85], fill=(255, 69, 0))

            chars_linea = max(24 if es_short else 35, int(len(titulo_mayus) / 3.8))
            font_tit = obtener_fuente_bold(42 if es_short else 56)
            draw.multiline_text((card_x + card_w // 2, card_y + card_h // 2 + 20), "\n".join(textwrap.wrap(titulo_mayus, width=chars_linea)), fill=(0, 0, 0), font=font_tit, anchor="mm", align="center", spacing=10)

        lienzo.save(output_png)

def seleccionar_sonido_transicion():
    """Archivo del efecto que suena al terminar la tarjeta de intro.

    Se busca por nombre en la carpeta del repo. Si hay varios
    (efecto_transicion_1.mp3, _2.mp3...) se elige uno al azar, para que no
    suene idéntico en todos los videos.
    """
    nombre = CONFIG.get("sonido_transicion", "efecto_transicion")
    if not nombre:
        return None

    # Si viene con extensión, se toma tal cual.
    if os.path.splitext(nombre)[1]:
        ruta = nombre if os.path.isabs(nombre) else os.path.join(BASE_DIR, nombre)
        return ruta if archivo_valido(ruta) else None

    candidatos = [
        f for f in glob.glob(os.path.join(BASE_DIR, f"{nombre}*"))
        if f.lower().endswith((".mp3", ".wav", ".m4a", ".ogg", ".aac")) and archivo_valido(f)
    ]
    return random.choice(candidatos) if candidatos else None


def parse_time(time_str):
    pt = datetime.strptime(time_str.replace(',', '.'), "%H:%M:%S.%f")
    return timedelta(hours=pt.hour, minutes=pt.minute, seconds=pt.second, microseconds=pt.microsecond)

def format_ass_time(td):
    ts = int(td.total_seconds())
    return f"{ts // 3600}:{(ts % 3600) // 60:02d}:{ts % 60:02d}.{int(td.microseconds / 10000):02d}"

def _color_ass(hex_rgb):
    """#RRGGBB -> &HAABBGGRR& (ASS invierte el orden a BGR; AA=00 es opaco)."""
    h = (hex_rgb or "").lstrip("#")
    if len(h) != 6:
        return "&H00FFFFFF&"
    return f"&H00{h[4:6]}{h[2:4]}{h[0:2]}&".upper()


def _cfg_subs():
    # CONFIG["subtitulos"] ya viene resuelto por cargar_config (defaults ->
    # preset -> lo puesto a mano); aquí solo se valida el estilo.
    subs = dict(SUBTITULOS_DEFAULT)
    subs.update(CONFIG.get("subtitulos", {}) or {})
    if subs.get("estilo") not in ESTILOS_SUBTITULOS:
        logger.warning(f"Estilo de subtítulo desconocido '{subs.get('estilo')}'; se usa 'frase_activa'.")
        subs["estilo"] = "frase_activa"
    return subs


def listar_estilos():
    """Muestra los presets disponibles y en qué se diferencian del actual."""
    activo = (CONFIG.get("subtitulos") or {}).get("preset", "predeterminado")
    print(f"\nPresets de subtítulos (activo: {activo})\n")
    for nombre, cambios in PRESETS_SUBTITULOS.items():
        marca = "→" if nombre == activo else " "
        r = resolver_subtitulos({}, nombre)
        paleta = r.get("colores_resalte") or []

        if len(paleta) > 1:
            resalte = f"{len(paleta)} colores rotando ({', '.join(paleta)})"
        else:
            resalte = f"siempre {paleta[0] if paleta else r['color_activo']}"

        cuales = (
            f"solo palabras de {r['min_letras_resalte']}+ letras con carga"
            if r.get("resaltar_solo_clave") else "todas las palabras"
        )
        cuantas = "1 palabra" if r["estilo"] == "pop" else f"{r['palabras_por_frase_largo']} palabras"

        print(f" {marca} {nombre}")
        print(f"     {cuantas} en pantalla · {r['fuente']}{', itálica' if r['italica'] else ''}")
        print(f"     resalta {cuales}, {resalte}")
        if not cambios:
            print("     (los valores por defecto)")
    print(
        "\nPara probar uno sin tocar nada:\n"
        "  python generar_video_maestro.py --estilo amarillo_clasico --historias 1\n"
        "Para dejarlo fijo, en config.json:\n"
        '  {"subtitulos": {"preset": "amarillo_clasico"}}\n'
    )


def _header_ass(es_short):
    subs = _cfg_subs()
    PlayResX, PlayResY = (1080, 1920) if es_short else (1920, 1080)
    font_size = subs["tamano_short"] if es_short else subs["tamano_largo"]
    palabras_por_grupo = (
        subs["palabras_por_frase_short"] if es_short else subs["palabras_por_frase_largo"]
    )
    # El estilo "pop" es de una palabra a la vez por definición.
    if subs["estilo"] == "pop":
        palabras_por_grupo = 1
    palabras_por_grupo = max(1, int(palabras_por_grupo))

    c_texto = _color_ass(subs["color_texto"])
    c_activo = _color_ass(subs["color_activo"])
    c_borde = _color_ass(subs["color_borde"])

    # PrimaryColour vs SecondaryColour: el efecto \k anima DE Secondary A
    # Primary. Antes ambos eran amarillo, así que el karaoke no se veía nunca.
    #  - "relleno": Secondary = lo que falta por decir, Primary = lo ya dicho.
    #  - los demás estilos pintan la palabra activa con tags en línea, así que
    #    el color base es simplemente el del texto.
    if subs["estilo"] == "relleno":
        primary, secondary = c_activo, c_texto
    else:
        primary, secondary = c_texto, c_activo

    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {PlayResX}\n"
        f"PlayResY: {PlayResY}\n"
        "WrapStyle: 0\n"
        "ScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Karaoke,{subs['fuente']},{font_size},{primary},{secondary},{c_borde},"
        f"&H80000000&,0,{1 if subs.get('italica') else 0},0,0,100,100,0,0,1,"
        f"{subs['grosor_borde']},{subs['sombra']},5,60,60,0,1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    return header, palabras_por_grupo


def _texto_palabra(p, subs):
    t = p["texto"]
    return t.upper() if subs["mayusculas"] else t


def _tiene_carga(palabra):
    """¿Vale la pena pintar esta palabra? Se descartan las de relleno y las
    muy cortas, que al resaltarse hacen que el efecto parezca un metrónomo
    en vez de un acento."""
    limpia = re.sub(r"[^\wáéíóúñü]", "", palabra.lower())
    return bool(limpia) and limpia not in PALABRAS_SIN_CARGA


def _colores_resalte(subs):
    """Lista de colores ASS por los que rota el resalte."""
    paleta = subs.get("colores_resalte") or [subs["color_activo"]]
    return [_color_ass(c) for c in paleta]


def _plan_resalte(palabras, subs):
    """Decide, para cada palabra, si se pinta y de qué color.

    Devuelve una lista paralela a `palabras` con el color ASS a usar, o None
    si esa palabra no se resalta. El contador de rotación solo avanza con las
    palabras que sí se pintan, para que la secuencia de colores se vea
    deliberada y no dependa de cuántas palabras vacías haya en medio.
    """
    paleta = _colores_resalte(subs)
    solo_clave = bool(subs.get("resaltar_solo_clave"))
    min_letras = int(subs.get("min_letras_resalte", 0) or 0)

    plan, i = [], 0
    for p in palabras:
        texto = p["texto"]
        pintar = True
        if solo_clave:
            limpia = re.sub(r"[^\wáéíóúñü]", "", texto)
            pintar = _tiene_carga(texto) and len(limpia) >= min_letras
        if pintar:
            plan.append(paleta[i % len(paleta)])
            i += 1
        else:
            plan.append(None)
    return plan


def convertir_timing_a_karaoke_ass(palabras, ass_out_path, duracion_intro_sec, es_short=True):
    """Arma el .ass de karaoke a partir del timing REAL por palabra que
    reporta edge-tts (evento WordBoundary), no de un SRT por oración
    repartido en partes iguales — eso causaba el desfase progresivo que se
    notaba sobre todo en oraciones largas.

    El estilo visual sale de CONFIG["subtitulos"]:
      - frase_activa: se lee la frase completa y solo la palabra que se está
        diciendo cambia de color y crece (el look de TikTok/CapCut). Requiere
        una línea Dialogue por palabra, porque en ASS el resaltado de \\k se
        queda pegado en las palabras ya dichas y aquí solo queremos una.
      - relleno: karaoke clásico con \\k, lo ya dicho se queda del color de
        acento. Una sola línea Dialogue por frase.
      - pop: una palabra a la vez, entrando con un rebote de escala.
    """
    subs = _cfg_subs()
    header, palabras_por_grupo = _header_ass(es_short)
    lineas_ass = [header]

    if not palabras:
        with open(ass_out_path, 'w', encoding='utf-8') as f: f.writelines(lineas_ass)
        return

    c_texto = _color_ass(subs["color_texto"])
    escala = max(100, int(subs["escala_activa"]))

    # Color de resalte por palabra: puede rotar entre varios y puede no
    # aplicarse a las palabras de relleno (ver _plan_resalte). Indexado por
    # posición en la lista original, para que grupos y palabras coincidan.
    plan = _plan_resalte(palabras, subs)
    indice_de = {id(p): i for i, p in enumerate(palabras)}

    def color_de(p):
        return plan[indice_de[id(p)]]

    def t_abs(seg):
        return timedelta(seconds=duracion_intro_sec + seg)

    def emitir(t_ini, t_fin, texto):
        if (t_fin - t_ini).total_seconds() <= 0:
            return
        lineas_ass.append(
            f"Dialogue: 0,{format_ass_time(t_ini)},{format_ass_time(t_fin)},"
            f"Karaoke,,0,0,0,,{texto}\n"
        )

    grupos = [palabras[i:i + palabras_por_grupo] for i in range(0, len(palabras), palabras_por_grupo)]

    # Hasta cuándo puede estirarse la última palabra de cada grupo: hasta que
    # arranca el grupo siguiente, para que el relevo entre frases no deje un
    # fotograma en blanco. Solo si la pausa es corta — en un silencio largo
    # (cambio de escena, respiración) es mejor limpiar que dejar colgada una
    # frase que ya no se está diciendo.
    PAUSA_MAXIMA_PUENTE = 0.6
    relevo = {}
    for k, grupo in enumerate(grupos[:-1]):
        fin_natural = grupo[-1]["inicio"] + grupo[-1]["duracion"]
        inicio_siguiente = grupos[k + 1][0]["inicio"]
        if 0 < inicio_siguiente - fin_natural <= PAUSA_MAXIMA_PUENTE:
            relevo[k] = inicio_siguiente

    for k, grupo in enumerate(grupos):
        if subs["estilo"] == "relleno":
            texto = "".join(
                f"{{\\k{max(6, int(p['duracion'] * 100))}}}{_texto_palabra(p, subs)} "
                for p in grupo
            ).strip()
            emitir(
                t_abs(grupo[0]["inicio"]),
                t_abs(grupo[-1]["inicio"] + grupo[-1]["duracion"]),
                texto,
            )
            continue

        if subs["estilo"] == "pop":
            for p in grupo:
                # \t(0,90,...) anima al entrar: arranca al 70% y llega al 100%
                # en 90 ms, que es el rebote que se ve en los videos de hoy.
                col = color_de(p)
                pintura = f"\\c{col}" if col else f"\\c{c_texto}"
                texto = (
                    f"{{{pintura}\\fscx70\\fscy70\\t(0,90,\\fscx{escala}\\fscy{escala})"
                    f"\\t(90,170,\\fscx100\\fscy100)}}{_texto_palabra(p, subs)}"
                )
                emitir(t_abs(p["inicio"]), t_abs(p["inicio"] + p["duracion"]), texto)
            continue

        # frase_activa: una línea por palabra, con la frase entera visible y
        # solo esa palabra resaltada.
        #
        # La palabra activa entra con un rebote (crece, se pasa un poco del
        # tamaño final y se asienta) en vez de saltar de golpe: es lo que
        # hacen los canales del género, medido en ~130 ms. Con
        # escala_activa=100 no hay nada que animar y se omite.
        sobregiro = escala + max(2, (escala - 100) // 2)

        def animacion(dur_seg):
            """El rebote se comprime para caber en la palabra. En español hay
            muchísimas palabras cortas ('mi', 'de', 'que') que duran menos que
            la animación completa; sin esto se quedarían congeladas a medio
            crecer, que es justo lo que se nota como mal hecho."""
            if escala <= 100:
                return ""
            ms = max(40, int(dur_seg * 1000))
            t1 = min(70, int(ms * 0.5))
            t2 = min(140, ms)
            return (
                f"\\fscx100\\fscy100"
                f"\\t(0,{t1},\\fscx{sobregiro}\\fscy{sobregiro})"
                f"\\t({t1},{t2},\\fscx{escala}\\fscy{escala})"
            )

        for i, activa in enumerate(grupo):
            partes = []
            anim = animacion(activa["duracion"])
            for j, p in enumerate(grupo):
                palabra = _texto_palabra(p, subs)
                if j == i:
                    col = color_de(p)
                    if col:
                        partes.append(
                            f"{{\\c{col}{anim}}}{palabra}"
                            f"{{\\c{c_texto}\\fscx100\\fscy100}}"
                        )
                    else:
                        # Palabra sin carga: no se pinta, pero sí crece, para
                        # no perder de vista dónde va la narración.
                        partes.append(
                            f"{{{anim}}}{palabra}{{\\fscx100\\fscy100}}"
                        )
                else:
                    partes.append(palabra)

            # Cada línea dura hasta que empieza la siguiente palabra, no solo
            # lo que dura la palabra en sí. Si terminara con ella, en el
            # silencio entre palabras no habría ninguna línea en pantalla y
            # la frase completa desaparecería por uno o dos fotogramas: un
            # parpadeo constante (medido: 20% de los fotogramas en blanco).
            # Así la frase se queda quieta y lo único que se mueve es el
            # resaltado, que es justamente el efecto que se busca.
            # Y la última palabra del grupo se estira hasta que arranca el
            # grupo siguiente (ver relevo), para que el cambio de frase
            # tampoco deje un fotograma en blanco.
            if i + 1 < len(grupo):
                fin = grupo[i + 1]["inicio"]
            else:
                fin = relevo.get(k, activa["inicio"] + activa["duracion"])

            emitir(t_abs(activa["inicio"]), t_abs(fin), " ".join(partes))

    with open(ass_out_path, 'w', encoding='utf-8') as f: f.writelines(lineas_ass)


def convertir_srt_a_karaoke_ass(srt_in_path, ass_out_path, duracion_intro_sec, es_short=True):
    """Respaldo si no se pudo capturar el timing real por palabra (ver
    convertir_timing_a_karaoke_ass): reparte cada bloque del SRT (por
    oración) en partes iguales entre sus palabras — aproximado, con algo
    de desfase en oraciones largas, pero mejor que nada."""
    header, palabras_por_grupo = _header_ass(es_short)

    if not os.path.exists(srt_in_path):
        with open(ass_out_path, 'w', encoding='utf-8') as f: f.write(header)
        return

    with open(srt_in_path, 'r', encoding='utf-8') as f: contenido = f.read()
    bloques = re.findall(r'(\d+)\n(\d{2}:\d{2}:\d{2}[,\.]\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}[,\.]\d{3})\n(.*?)(?=\n\n|\Z)', contenido, re.DOTALL)
    lineas_ass = [header]

    for _, t_inicio_str, t_fin_str, texto in bloques:
        palabras = texto.strip().replace('\n', ' ').split()
        if not palabras: continue

        t_inicio = parse_time(t_inicio_str) + timedelta(seconds=duracion_intro_sec) - timedelta(seconds=0.32)
        t_fin = parse_time(t_fin_str) + timedelta(seconds=duracion_intro_sec) - timedelta(seconds=0.32)
        
        if t_inicio.total_seconds() < duracion_intro_sec: t_inicio = timedelta(seconds=duracion_intro_sec)

        dur_cs = int((t_fin - t_inicio).total_seconds() * 100)
        if dur_cs <= 0: continue

        grupos = [palabras[i:i+palabras_por_grupo] for i in range(0, len(palabras), palabras_por_grupo)]
        dur_grp = dur_cs // max(1, len(grupos))

        t_act = t_inicio
        for grupo in grupos:
            t_sig = t_act + timedelta(seconds=dur_grp / 100.0)
            texto_karaoke = "".join([f"{{\\k{max(6, dur_grp // len(grupo))}}}{p.upper()} " for p in grupo])
            lineas_ass.append(f"Dialogue: 0,{format_ass_time(t_act)},{format_ass_time(t_sig)},Karaoke,,0,0,0,,{texto_karaoke.strip()}\n")
            t_act = t_sig

    with open(ass_out_path, 'w', encoding='utf-8') as f: f.writelines(lineas_ass)


def generar_cuerpo_con_mejor_timing(t_cue, v_cue, p_cue, r_cue, a_cue, s_raw):
    """Genera el audio de la narración intentando primero el timing real por
    palabra (ver generar_audio_con_timing); si falla o sale sospechosamente
    corto, cae al flujo viejo (CLI + SRT). Devuelve (ok, palabras) — palabras
    es None cuando se usó el flujo de respaldo por SRT."""
    with open(t_cue, "r", encoding="utf-8") as f:
        texto_plano = f.read()
    duracion_minima_esperada = len(texto_plano.split()) / 6.0

    palabras = generar_audio_con_timing(texto_plano, v_cue, p_cue, r_cue, a_cue)
    if palabras:
        duracion_hablada = palabras[-1]["inicio"] + palabras[-1]["duracion"]
        if duracion_hablada >= duracion_minima_esperada:
            return True, palabras
        logger.warning("Audio con timing preciso salió sospechosamente corto; se usa el flujo por SRT en su lugar.")

    ok = generar_audio(t_cue, v_cue, p_cue, r_cue, a_cue, s_raw)
    return ok, None


async def _sintetizar_con_timing_async(texto, voz, pitch, rate, audio_out):
    # boundary='WordBoundary' es obligatorio: por defecto Communicate() usa
    # 'SentenceBoundary', que es justo el nivel de detalle que queremos evitar.
    comunicador = edge_tts.Communicate(texto, voice=voz, rate=rate, pitch=pitch, boundary="WordBoundary")
    palabras = []
    with open(audio_out, "wb") as f:
        async for trozo in comunicador.stream():
            if trozo["type"] == "audio":
                f.write(trozo["data"])
            elif trozo["type"] == "WordBoundary":
                palabras.append({
                    "texto": trozo["text"],
                    "inicio": trozo["offset"] / 10_000_000.0,   # 100ns -> segundos
                    "duracion": trozo["duration"] / 10_000_000.0,
                })
    return palabras


def generar_audio_con_timing(texto_plano, voz, pitch, rate, audio_out):
    """Como generar_audio, pero usando la librería de edge-tts directamente
    en vez del comando de consola: así se captura el timing real por
    palabra (evento WordBoundary) que el CLI no expone bien vía --write-subtitles.
    Devuelve la lista de palabras con su timing, o None si falló (en cuyo
    caso el llamador debe caer al flujo viejo basado en SRT)."""
    if edge_tts is None:
        return None
    for intento in range(2):
        try:
            if os.path.exists(audio_out):
                os.remove(audio_out)
            palabras = asyncio.run(_sintetizar_con_timing_async(texto_plano, voz, pitch, rate, audio_out))
            if archivo_valido(audio_out) and palabras:
                return palabras
        except Exception as exc:
            logger.warning(f"Fallo generando audio con timing preciso (intento {intento + 1}/2): {exc}")
        time.sleep(1)
    return None


def generar_audio(txt, voz, pitch, rate, audio_out, srt_out):
    """Genera TTS con reintentos y nunca reporta éxito si no hay archivo válido."""
    comando_principal = [
        sys.executable, "-m", "edge_tts",
        f"--rate={rate}", f"--pitch={pitch}",
        "--file", txt, "--voice", voz,
        "--write-media", audio_out,
        "--write-subtitles", srt_out
    ]
    comandos = [
        comando_principal,
        comando_principal,  # un segundo intento con la misma voz cubre la mayoría de los cortes por red
        [
            sys.executable, "-m", "edge_tts",
            "--rate=+15%", "--pitch=+0Hz",
            "--file", txt, "--voice", "es-MX-JorgeNeural",
            "--write-media", audio_out,
            "--write-subtitles", srt_out
        ],
    ]

    try:
        with open(txt, "r", encoding="utf-8") as f:
            num_palabras = len(f.read().split())
    except Exception:
        num_palabras = 0
    # Cota mínima conservadora: textos cortos (como el título) se hablan
    # proporcionalmente más rápido, así que se usa un margen amplio
    # (palabras/6.0) para no rechazar tomas válidas por falsos positivos.
    duracion_minima_esperada = num_palabras / 6.0

    for intento, cmd in enumerate(comandos, 1):
        try:
            if os.path.exists(audio_out):
                os.remove(audio_out)
            if os.path.exists(srt_out):
                os.remove(srt_out)

            res = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace"
            )
            if res.returncode == 0 and archivo_valido(audio_out):
                if medir_duracion_media(audio_out) >= duracion_minima_esperada:
                    return True
                logger.warning(
                    f"Audio TTS sospechosamente corto para {os.path.basename(txt)} "
                    f"(intento {intento}/{len(comandos)}); reintentando."
                )

            if intento < len(comandos):
                time.sleep(1)
        except Exception:
            if intento < len(comandos):
                time.sleep(1)

    return False

def renderizar_una_historia(contenido, num=1):
    gestor = GestorTemporales()
    try:
        v_tit, v_cue, p_cue, r_cue, emocion, tit, cue, n_arch, fuente_url, autor_original = extraer_titulo_y_cuerpo(contenido)
        musica = seleccionar_musica_fondo(emocion, num)
        os.makedirs(CARPETA_SALIDA, exist_ok=True)
        ruta_out = os.path.join(CARPETA_SALIDA, f"{num:02d}_{n_arch}.mp4")

        if not CONFIG["reintentar_existentes"] and archivo_valido(ruta_out):
            print(f"\n⏭️  [Video {num}] Ya existe, se omite: {os.path.basename(ruta_out)}")
            logger.info(f"Video {num} omitido (ya existe): {ruta_out}")
            return

        print(f"\n🎬 [Video {num}] Procesando: {n_arch}")
        print(f" ├─ ⚙️  Emoción: {emocion.upper()} ({origen_emocion(contenido)}) | Música: {musica or 'Ninguna'}")
        
        t_tit, t_cue = gestor.registrar(f"t_tit_{num}.txt"), gestor.registrar(f"t_cue_{num}.txt")
        a_tit, a_cue = gestor.registrar(f"a_tit_{num}.m4a"), gestor.registrar(f"a_cue_{num}.m4a")
        s_dum, s_raw = gestor.registrar(f"s_dum_{num}.srt"), gestor.registrar(f"s_raw_{num}.srt")
        
        with open(t_tit, "w", encoding="utf-8") as f: f.write(limpiar_texto_seguro(tit))
        with open(t_cue, "w", encoding="utf-8") as f: f.write(limpiar_texto_seguro(cue))

        term_cols = shutil.get_terminal_size((40, 24)).columns
        anch = max(10, term_cols - 35)

        # FASE 1: TTS
        txt_loc = " ├─ 🎙️ [1/4] Loc:"
        with ThreadPoolExecutor(max_workers=2) as ex:
            fut_tit = ex.submit(generar_audio, t_tit, v_tit, "+0Hz", "+15%", a_tit, s_dum)
            fut_cue = ex.submit(generar_cuerpo_con_mejor_timing, t_cue, v_cue, p_cue, r_cue, a_cue, s_raw)
            futs = [fut_tit, fut_cue]
            while not all(f.done() for f in futs):
                pct = (sum(1 for f in futs if f.done())/2)*100
                bl = int(anch * pct / 100)
                actualizar_hud([f"{txt_loc} [{pct:5.1f}%] [{'█'*bl}{' '*(anch-bl)}]"])
                time.sleep(0.5)

        errores_tts = []
        try:
            ok_tit = fut_tit.result()
        except Exception:
            ok_tit = False
        if not ok_tit:
            errores_tts.append("título")

        palabras_cuerpo = None
        try:
            ok_cue, palabras_cuerpo = fut_cue.result()
        except Exception:
            ok_cue = False
        if not ok_cue:
            errores_tts.append("narración")

        if errores_tts:
            actualizar_hud(
                [f"{txt_loc} [Fallo: {', '.join(errores_tts)}]"],
                True
            )
            raise RuntimeError(
                "No se pudo generar la locución de: " +
                ", ".join(errores_tts)
            )

        actualizar_hud([f"{txt_loc} [100.0%] [{'█'*anch}]"], True)

        d_tit, d_cue = medir_duracion_media(a_tit), medir_duracion_media(a_cue)
        dur_sec = d_tit + d_cue
        if d_tit <= 0 or d_cue <= 0 or dur_sec <= 0:
            raise RuntimeError("La duración de una o ambas locuciones es inválida.")
        
        es_short = (dur_sec <= DURACION_MAX_SHORT_SEC) 
        num_palabras = len(limpiar_texto_seguro(cue).split())
        
        msg_formato = f" ├─ 📐 Formato: {'Vertical (Short/TikTok)' if es_short else 'Horizontal (Largo)'} ({num_palabras} palabras, {dur_sec/60.0:.1f} min)"
        print(msg_formato[:term_cols - 1])
        
        # FASE 2: Fondo
        vid_fondo = crear_fondo_multi_corte(dur_sec, es_short, gestor, num) or seleccionar_fondo_video(es_short)
        if not vid_fondo:
            raise RuntimeError("Sin fondo válido para este video.")

        # FASE 3: Gráficos
        txt_gra = " ├─ 📝 [3/4] Gráficos:"
        def act_gra(p):
            bl = int(anch * p / 100)
            actualizar_hud([f"{txt_gra} [{p:5.1f}%] [{'█'*bl}{' '*(anch-bl)}]"])
            
        act_gra(0.0)
        img_tar = gestor.registrar(f"tar_{num}.png")
        crear_tarjeta_intro_impecable(tit, img_tar, es_short)
        
        act_gra(33.3)
        a_loc = gestor.registrar(f"a_loc_{num}.m4a")
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", a_tit, "-i", a_cue, "-filter_complex", "[0:a][1:a]concat=n=2:v=0:a=1[aout]", "-map", "[aout]", "-c:a", "aac", "-b:a", "192k", a_loc])
        
        act_gra(66.6)
        s_ass = gestor.registrar(f"s_ass_{num}.ass")
        if palabras_cuerpo:
            convertir_timing_a_karaoke_ass(palabras_cuerpo, s_ass, d_tit, es_short)
        else:
            convertir_srt_a_karaoke_ass(s_raw, s_ass, d_tit, es_short)
        act_gra(100.0)
        actualizar_hud([f"{txt_gra} [100.0%] [{'█'*anch}]"], True)

        # FASE 4: Render
        w, h = (1080, 1920) if es_short else (1920, 1080)
        f_ass = s_ass.replace('\\', '\\\\').replace(':', '\\:')
        # fontsdir: sin esto libass busca la fuente en el sistema y, si no
        # está, la sustituye en silencio por otra (así es como "Montserrat
        # Black" terminaba saliendo como DejaVu Sans en peso normal).
        if os.path.isdir(RUTA_FUENTES):
            f_fuentes = RUTA_FUENTES.replace('\\', '\\\\').replace(':', '\\:')
            f_ass = f"{f_ass}:fontsdir={f_fuentes}"
        else:
            logger.warning(
                f"No existe {RUTA_FUENTES}; los subtítulos van a usar la fuente que el "
                f"sistema elija por su cuenta, que puede no ser la configurada."
            )

        # Fondo escalado y recortado, con velo blanco opcional encima.
        #
        # Con velo 0 la cadena tiene que terminar directamente en [bgt]: no
        # basta con omitir el trozo del overlay y dejar [bg][bgt], porque eso
        # le da dos etiquetas de salida a un filtro que solo produce una y
        # ffmpeg rechaza el filter_complex entero.
        escalado = f"[0:v]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}"
        opacidad_velo = float(CONFIG.get("velo_blanco_fondo", 0.0) or 0.0)
        if opacidad_velo > 0:
            cadena_fondo = (
                f"{escalado}[bg];"
                f"color=white@{opacidad_velo}:s={w}x{h}[ow];"
                f"[bg][ow]overlay=0:0[bgt]"
            )
        else:
            cadena_fondo = f"{escalado}[bgt]"

        # Efecto de transición: suena una sola vez, justo al terminar la
        # tarjeta de intro, para marcar el arranque de la historia.
        sonido = seleccionar_sonido_transicion()

        # Entradas de ffmpeg, en orden. Se arman aquí porque los índices que
        # usa el filter_complex ([1:a], [2:a]...) dependen de cuáles existan.
        entradas = ["-stream_loop", "-1", "-i", vid_fondo, "-i", a_loc]
        idx = {"fondo": 0, "loc": 1}
        siguiente = 2
        if musica:
            entradas += ["-stream_loop", "-1", "-i", musica]
            idx["musica"] = siguiente; siguiente += 1
        if sonido:
            entradas += ["-i", sonido]
            idx["sonido"] = siguiente; siguiente += 1
        entradas += ["-i", img_tar]
        idx["tarjeta"] = siguiente

        video_fc = (
            f"{cadena_fondo};[bgt][{idx['tarjeta']}:v]"
            f"overlay=0:0:enable='between(t,0,{d_tit:.2f})'[bgc];[bgc]ass='{f_ass}'[vout]"
        )

        # amix normaliza dividiendo entre el número de entradas, así que
        # sumar una pista bajaría el volumen de todas. Con normalize=0 los
        # niveles se fijan a mano y agregar el efecto no altera la mezcla
        # que ya existía (locución 0.5 + música 0.09 es exactamente lo que
        # producía el amix de dos entradas con normalización).
        vol_loc = float(CONFIG.get("volumen_locucion", 0.5) or 0.0)
        vol_mus = float(CONFIG.get("volumen_musica", 0.09) or 0.0)
        partes_audio, etiquetas = [], []
        partes_audio.append(f"[{idx['loc']}:a]volume={vol_loc}[av]"); etiquetas.append("[av]")
        # (la etiqueta final se decide abajo: con una sola pista no hay que
        # mezclar, y encadenar algo después de [av] sería inválido)
        if musica:
            fade_inicio = max(0.0, dur_sec - 2.0)
            partes_audio.append(
                f"[{idx['musica']}:a]volume={vol_mus},afade=t=out:st={fade_inicio:.2f}:d=2[am]"
            )
            etiquetas.append("[am]")
        if sonido:
            vol = float(CONFIG.get("volumen_sonido_transicion", 0.5) or 0.0)
            adelanto = float(CONFIG.get("adelanto_sonido_transicion", 0.25) or 0.0)
            retraso = int(max(0.0, d_tit - adelanto) * 1000)
            partes_audio.append(
                f"[{idx['sonido']}:a]volume={vol},adelay={retraso}|{retraso}[asfx]"
            )
            etiquetas.append("[asfx]")

        if len(etiquetas) == 1:
            # Sin música ni efecto no hay nada que mezclar: la locución se
            # etiqueta directamente como salida. Encadenar ",anull[aout]"
            # después de "[av]" produce un filtro inválido ("Cannot find a
            # matching stream for unlabeled input pad").
            fc = f"{video_fc};[{idx['loc']}:a]volume={vol_loc}[aout]"
        else:
            fc = (
                f"{video_fc};" + ";".join(partes_audio) + ";"
                + "".join(etiquetas)
                + f"amix=inputs={len(etiquetas)}:duration=first:normalize=0[aout]"
            )

        cmd_ff = ["ffmpeg", "-hide_banner", "-y"] + entradas + [
            "-filter_complex", fc, "-map", "[vout]", "-map", "[aout]"
        ]

        flags_audio_comunes = ["-map_metadata", "-1", "-c:a", "aac", "-b:a", "192k", "-shortest", "-progress", "pipe:1"]
        flags_gpu = ["-hwaccel", "cuda", "-c:v", "h264_nvenc", "-preset", "p4", "-rc", "vbr", "-cq", "19"]
        flags_cpu = ["-c:v", "libx264", "-preset", "ultrafast"]

        txt_ren = " ├─ 🚀 [4/4] Render:"

        def ejecutar_render(flags_encoder):
            cmd = cmd_ff + flags_encoder + flags_audio_comunes + [ruta_out]
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True, encoding='utf-8', errors='ignore')

            current_size = "0.0MB"
            current_speed = 0.0
            eta_str = "--:--"

            # stderr viene mezclado aquí y se consume para sacar el progreso.
            # Guardamos las últimas líneas que NO son progreso: si el render
            # falla, ese es el mensaje real de ffmpeg, y sin él el error que
            # se reporta ("falló tanto en GPU como en CPU") no dice nada.
            ultimas = collections.deque(maxlen=12)

            for ln in proc.stdout:
                if not re.match(r"^(frame|fps|stream_|bitrate|total_size|out_time|dup_|drop_|speed|progress)=", ln.strip()):
                    if ln.strip():
                        ultimas.append(ln.rstrip())
                if m := re.search(r"total_size=(\d+)", ln):
                    current_size = f"{int(m.group(1)) / (1024*1024):.1f}MB"
                elif m := re.search(r"speed=\s*([\d\.]+)x", ln):
                    current_speed = float(m.group(1))
                elif m := re.search(r"out_time_ms=(\d+)", ln):
                    out_sec = int(m.group(1)) / 1000000.0
                    pct = min(100.0, (out_sec / dur_sec) * 100.0)

                    if current_speed > 0:
                        eta_sec = max(0, (dur_sec - out_sec) / current_speed)
                        eta_str = time.strftime('%M:%S', time.gmtime(eta_sec))

                    bl = int(anch * pct / 100)

                    linea_barra = f"{txt_ren} [{pct:5.1f}%] [{'█'*bl}{' '*(anch-bl)}]"
                    linea_metricas = f" ├─ ⏱️ ETA: {eta_str} | 💾 {current_size} | ⚡ {current_speed}x"

                    actualizar_hud([linea_barra, linea_metricas])
            proc.wait()
            ok = proc.returncode == 0 and archivo_valido(ruta_out)
            if not ok and ultimas:
                logger.error(
                    f"ffmpeg falló (código {proc.returncode}) en el video {num}:\n  "
                    + "\n  ".join(ultimas)
                )
            return ok

        exito_render = ejecutar_render(flags_cpu if ES_ANDROID else flags_gpu)
        if not exito_render and not ES_ANDROID:
            logger.warning(f"Render GPU falló para video {num}, reintentando con CPU (libx264).")
            exito_render = ejecutar_render(flags_cpu)

        if not exito_render:
            raise RuntimeError("El render final falló tanto en GPU como en CPU.")

        actualizar_hud([f"{txt_ren} [100.0%] [{'█'*anch}]", ""], True)
        
        if ES_ANDROID: subprocess.run(["am", "broadcast", "-a", "android.intent.action.MEDIA_SCANNER_SCAN_FILE", "-d", f"file://{ruta_out}"], capture_output=True)
        print(f"✅ ¡Video {num} completado!: {os.path.basename(ruta_out)}")
        logger.info(f"Video {num} completado: {ruta_out} ({dur_sec:.1f}s, {emocion})")
        return {
            "numero": num,
            "titulo": tit,
            "ruta": ruta_out,
            "emocion": emocion,
            "cuerpo": cue,
            "duracion_sec": round(dur_sec, 1),
            "es_short": es_short,
            "fuente_url": fuente_url,
            "autor_original": autor_original,
            "musica_archivo": os.path.basename(musica) if musica else None,
        }
    finally:
        gestor.limpiar()

def parsear_seleccion(texto, total):
    """Convierte '1', '1,3,5' o '2-6' (combinables: '1,4-6,9') en índices.

    Devuelve una lista ordenada y sin repetidos, en base 1, que es como se
    numeran las historias en pantalla y en los nombres de archivo."""
    elegidos = set()
    for parte in str(texto).split(","):
        parte = parte.strip()
        if not parte:
            continue
        if "-" in parte:
            desde, _, hasta = parte.partition("-")
            try:
                a, b = int(desde), int(hasta)
            except ValueError:
                raise ValueError(f"Rango inválido: '{parte}'")
            if a > b:
                a, b = b, a
            elegidos.update(range(a, b + 1))
        else:
            try:
                elegidos.add(int(parte))
            except ValueError:
                raise ValueError(f"Número inválido: '{parte}'")

    fuera = [n for n in elegidos if n < 1 or n > total]
    if fuera:
        raise ValueError(
            f"Fuera de rango (hay {total} historia(s)): {sorted(fuera)}"
        )
    return sorted(elegidos)


def listar_historias(archivo="guion.txt"):
    """Imprime el índice de historias del guion, para saber qué número pedir."""
    if not os.path.exists(archivo):
        print(f"❌ No se encontró '{archivo}'.")
        return

    with open(archivo, "r", encoding="utf-8") as f:
        hists = [h.strip() for h in f.read().split("===NUEVA_HISTORIA===") if h.strip()]

    print(f"\n{len(hists)} historia(s) en {archivo}\n")
    for i, h in enumerate(hists, 1):
        lineas = [
            l.strip() for l in h.splitlines()
            if l.strip() and not l.strip().startswith(("#", "===", "📌", "🎙️"))
        ]
        titulo = lineas[0] if lineas else "(sin título)"
        palabras = len(" ".join(lineas[1:]).split()) if len(lineas) > 1 else 0
        emocion = detectar_emocion_historia(h)
        # ~2.6 palabras/segundo es el ritmo típico de la narración generada.
        mins, segs = divmod(int(palabras / 2.6), 60)
        print(f"  {i:2d}. [{emocion[:4]:<4}] {mins}:{segs:02d} aprox  {titulo[:52]}")
    print()


def guardar_resultado_lote(completados, fallidas, avisar=False):
    """Escribe el registro de lo renderizado.

    Se acumula en vez de sobrescribir. Antes cada corrida reemplazaba el
    archivo entero, así que los videos de tandas anteriores que seguían en
    disco quedaban huérfanos: sin registro, publisher.py ni los veía, y no
    había forma de saber de qué historia venían.

    Se llama después de cada historia, así que no puede tirar nunca: un fallo
    escribiendo el registro no debe llevarse por delante un render que ya
    terminó bien.
    """
    ruta_resultado = os.path.join(CARPETA_SALIDA, "resultado_lote.json")
    try:
        previos = []
        if os.path.exists(ruta_resultado):
            try:
                with open(ruta_resultado, "r", encoding="utf-8") as f:
                    previos = json.load(f).get("completados", [])
            except Exception as exc:
                logger.warning(f"No se pudo leer el resultado_lote.json previo ({exc}); se empieza de cero.")

        # Los de esta corrida ganan sobre un registro viejo de la misma ruta
        # (es un re-render), y se descartan los previos cuyo archivo ya no
        # exista, para que el registro no crezca sin fin.
        rutas_nuevas = {v["ruta"] for v in completados}
        conservados = [
            v for v in previos
            if v.get("ruta") not in rutas_nuevas and archivo_valido(v.get("ruta", ""))
        ]

        # Escritura atómica: el panel lee este archivo cada segundo y medio
        # mientras el render corre. Escribiendo encima directamente le tocaría
        # leerlo a medias tarde o temprano, y un JSON truncado le vacía la lista.
        # Por número de historia, no por orden de escritura: si no, rehacer
        # un video lo mandaba al final de la tira y la miniatura que tenías
        # seleccionada se movía debajo de ti.
        todos = sorted(conservados + completados,
                       key=lambda v: (v.get("numero") or 0, v.get("ruta") or ""))

        tmp = ruta_resultado + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({
                "completados": todos,
                "fallidas": [{"numero": n, "error": e} for n, e in fallidas],
            }, f, ensure_ascii=False, indent=2)
        os.replace(tmp, ruta_resultado)

        if avisar and conservados:
            logger.info(f"resultado_lote.json: {len(conservados)} video(s) de corridas anteriores conservados.")
    except Exception as exc:
        logger.warning(f"No se pudo escribir resultado_lote.json: {exc}")


def renderizar_lote_historias(archivo="guion.txt", seleccion=None):
    sys.stdout.write("\033[?25l")
    sys.stdout.flush()
    try:
        print("--------------------------------------------------\n🟢 INICIANDO GENERADOR V2\n--------------------------------------------------")

        comprobar_dependencias()

        if not os.path.exists(archivo):
            print(f"❌ Error: No se encontró '{archivo}'.")
            return

        with open(archivo, 'r', encoding='utf-8') as f:
            hists = [
                h.strip()
                for h in f.read().split("===NUEVA_HISTORIA===")
                if h.strip()
            ]

        if not hists:
            print("❌ No se detectaron historias.")
            return

        print(f"📦 Total de historias detectadas: {len(hists)}")

        # Se conserva la numeración original del guion aunque se renderice un
        # subconjunto: así el nombre del archivo de la historia 7 es el mismo
        # tanto si se hizo el lote entero como si se pidió solo esa.
        if seleccion:
            pares = [(i, hists[i - 1]) for i in seleccion]
            print(f"🎯 Renderizando solo {len(pares)}: {', '.join(str(i) for i in seleccion)}")
        else:
            pares = list(enumerate(hists, 1))

        fallidas = []
        completados = []

        for i, h in pares:
            try:
                resultado = renderizar_una_historia(h, i)
                if resultado:
                    completados.append(resultado)
                    # Se guarda aquí, no solo al final del lote: el panel lee
                    # este archivo para saber qué hay renderizado, y esperando
                    # al final no aparecía nada hasta que terminaban todas.
                    # Con cinco historias son muchos minutos mirando una lista
                    # vacía mientras los videos ya están hechos en disco.
                    guardar_resultado_lote(completados, fallidas)
            except KeyboardInterrupt:
                # Ctrl+C corta el lote pero conserva lo ya terminado: el
                # registro se escribe abajo igual, no se pierde el trabajo.
                print(f"\n\n⏹️  Interrumpido en la historia {i}. Se guarda lo completado.")
                break
            except Exception as exc:
                fallidas.append((i, str(exc)))
                logger.error(f"Video {i} falló: {exc}")
                print(f"\n❌ Video {i} falló: {exc}")

        guardar_resultado_lote(completados, fallidas, avisar=True)

        print("--------------------------------------------------")
        if fallidas:
            print(f"⚠️ Lote terminado con {len(fallidas)} historia(s) fallida(s).")
            for numero, error in fallidas:
                print(f"   • Video {numero}: {error}")
        else:
            print("🎉 ¡PROCESAMIENTO POR LOTE COMPLETADO SIN ERRORES!")
        print("--------------------------------------------------")
    finally:
        sys.stdout.write("\033[?25h")
        sys.stdout.flush()

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Renderiza las historias de guion.txt.",
        epilog=(
            "Ejemplos:\n"
            "  python generar_video_maestro.py --listar        ver el índice\n"
            "  python generar_video_maestro.py --historias 1   solo la primera\n"
            "  python generar_video_maestro.py --historias 1,4-6,9\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--guion", default="guion.txt", help="Archivo de guion a usar.")
    parser.add_argument("--listar", action="store_true", help="Solo listar las historias, sin renderizar.")
    parser.add_argument(
        "--historias",
        help="Cuáles renderizar: '1', '1,3,5' o '2-6' (combinables). Por defecto, todas.",
    )
    parser.add_argument("--estilos", action="store_true", help="Listar los presets de subtítulos.")
    parser.add_argument(
        "--rehacer", action="store_true",
        help="Volver a renderizar aunque el archivo ya exista (si no, se omite).",
    )
    parser.add_argument(
        "--voz", choices=["masculina", "femenina"],
        help="Forzar la voz, ignorando el '# Genero:' del guion.",
    )
    parser.add_argument(
        "--estilo",
        help="Preset de subtítulos para esta corrida, sin tocar config.json.",
    )
    parser.add_argument(
        "--musica",
        help="Fijar la música en vez de sacarla al azar. Un nombre de archivo "
             "la aplica a todas las historias de la corrida; para elegir por "
             "historia usa pares: --musica '3=musica_drama_x.mp3,7=musica_fondo_y.mp3'.",
    )
    parser.add_argument(
        "--volumen-musica", type=float, metavar="0..1",
        help="Volumen de la música solo para esta corrida (por defecto 0.09).",
    )
    parser.add_argument(
        "--fondo",
        help="Usar solo los videos de fondo cuyo nombre contenga este texto.",
    )
    args = parser.parse_args()

    # --estilo aplica solo a esta corrida: se recarga la config con el preset
    # pedido y se refrescan los globales que ya la habían leído al importar.
    if args.estilo:
        CONFIG = cargar_config(preset=args.estilo)
        CARPETA_SALIDA = CONFIG["carpeta_salida"]
        DURACION_MAX_SHORT_SEC = CONFIG["duracion_max_short_sec"]

    if args.estilos:
        listar_estilos()
        sys.exit(0)

    if args.listar:
        listar_historias(args.guion)
        sys.exit(0)

    seleccion = None
    if args.historias:
        if not os.path.exists(args.guion):
            sys.exit(f"❌ No se encontró '{args.guion}'.")
        with open(args.guion, "r", encoding="utf-8") as f:
            total = len([h for h in f.read().split("===NUEVA_HISTORIA===") if h.strip()])
        try:
            seleccion = parsear_seleccion(args.historias, total)
        except ValueError as exc:
            sys.exit(f"❌ {exc}")

    # Rehacer implica reemplazar el archivo existente; si no, el render se
    # salta la historia y el botón "Rehacer" no haría nada.
    if args.rehacer:
        CONFIG["reintentar_existentes"] = True
    if args.voz:
        CONFIG["_voz_forzada"] = args.voz
    if args.fondo:
        CONFIG["_fondo_forzado"] = args.fondo
    if args.volumen_musica is not None:
        CONFIG["volumen_musica"] = max(0.0, min(1.0, args.volumen_musica))
    if args.musica:
        # "archivo.mp3" -> para todas;  "3=a.mp3,7=b.mp3" -> por historia.
        elegidas = {}
        for parte in args.musica.split(","):
            parte = parte.strip()
            if not parte:
                continue
            if "=" in parte:
                n, archivo = parte.split("=", 1)
                elegidas[n.strip()] = archivo.strip()
            else:
                elegidas["*"] = parte
        CONFIG["_musica_forzada"] = elegidas

    renderizar_lote_historias(args.guion, seleccion)
