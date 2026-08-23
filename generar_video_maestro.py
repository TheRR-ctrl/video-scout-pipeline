import os
import re
import sys
import time
import json
import shutil
import random
import logging
import hashlib
import threading
import subprocess
import textwrap
import tempfile
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from PIL import Image, ImageDraw, ImageFont

# ---------------------------------------------------------
# CONFIGURACIÓN GLOBAL Y VARIABLES DE ESTADO
# ---------------------------------------------------------
ES_ANDROID = 'PREFIX' in os.environ or os.path.exists('/sdcard')

CONFIG_DEFAULT = {
    "carpeta_salida": "/sdcard/DCIM/Videos creados" if ES_ANDROID else os.path.join(os.path.expanduser("~"), "Desktop", "Videos Creados"),
    "duracion_max_short_sec": 180.0,
    "voz_masculina": "es-MX-JorgeNeural",
    "voz_femenina": "es-MX-DaliaNeural",
    "reintentar_existentes": False,
}

def cargar_config(ruta="config.json"):
    cfg = dict(CONFIG_DEFAULT)
    if os.path.exists(ruta):
        try:
            with open(ruta, "r", encoding="utf-8") as f:
                cfg.update(json.load(f))
        except Exception as exc:
            print(f"⚠️ No se pudo leer {ruta}, usando valores por defecto: {exc}")
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

def crear_fondo_multi_corte(duracion_requerida_sec, es_short, gestor_temp, num_index=1):
    exts = ('.webm', '.mp4', '.mkv', '.mov')
    prefijo = "fondo_vertical" if es_short else "fondo_horizontal"
    cands = [f for f in os.listdir('.') if f.endswith(exts) and not f.startswith(('0','1','2','3','temp_','fondo_ensamblado'))]
    vids_base = [c for c in cands if prefijo in c or 'fondo' in c] or cands
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

def detectar_emocion_historia(texto):
    """Detecta emoción explícita y, si no existe, busca palabras clave."""
    m = re.search(r'emoci[óo]n\s*[:=]\s*([\w-]+)', texto, re.IGNORECASE)
    val = m.group(1) if m else texto.lower()

    dic = {
        'venganza': ['venganza', 'justicia', 'despojo', 'demanda', 'abogado'],
        'suspenso': ['suspenso', 'tensión', 'tension', 'fraude', 'secreto'],
        'drama': ['drama', 'triste', 'luto', 'dolor', 'lágrimas', 'lagrimas'],
        'comedia': ['comedia', 'humor', 'risa', 'chiste', 'divertido'],
    }
    val = str(val).lower()
    return next(
        (em for em, kws in dic.items() if any(k in val for k in kws)),
        'fondo'
    )

def seleccionar_fondo_video(es_short):
    return next((f for p in ["fondo_vertical" if es_short else "fondo_horizontal", "fondo_gameplay"] for ext in ['.webm', '.mp4', '.mkv'] if os.path.exists(p+ext)), next((f for f in os.listdir('.') if f.endswith(('.webm', '.mp4', '.mkv'))), None))

def seleccionar_musica_fondo(emocion='fondo'):
    """Elige al azar entre todas las pistas disponibles para la emoción (p.ej.
    musica_drama_artista_123.mp3, descargadas por actualizar_musica.py), para
    no repetir siempre la misma canción. Si no hay ninguna con ese prefijo
    exacto, cae a musica_fondo_*, y si tampoco hay, a cualquier musica_*."""
    exts = ('.m4a', '.mp3', '.wav', '.aac')

    def candidatas(prefijo):
        return [
            f for f in os.listdir('.')
            if f.startswith(prefijo) and f.endswith(exts) and archivo_valido(f)
        ]

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

def extraer_titulo_y_cuerpo(texto_raw):
    es_fem = bool(re.search(r'g[é e]nero:\s*(femenino|mujer)', texto_raw, re.IGNORECASE))
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
        factor_escala = 0.95 if es_short else 0.55
        target_w = int(ancho * factor_escala)
        w_orig, h_orig = plantilla_original.size
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

        font_tit = obtener_fuente_bold(font_size)
        draw.multiline_text((ancho // 2, pos_y + int(target_h * 0.58)), "\n".join(lineas_wrap), fill=(255, 255, 255), font=font_tit, anchor="mm", align="center", spacing=espaciado, stroke_width=ancho_borde, stroke_fill=(0, 0, 0))
        lienzo.save(output_png)
    else:
        draw = ImageDraw.Draw(lienzo)
        card_w = int(ancho * 0.88)
        card_h = int(alto * (0.28 if es_short else 0.35))
        card_x, card_y = (ancho - card_w) // 2, ((alto - card_h) // 2) - 120
        
        draw.rounded_rectangle([card_x, card_y, card_x + card_w, card_y + card_h], radius=30, fill=(255, 255, 255, 250))
        draw.ellipse([card_x + 40, card_y + 35, card_x + 90, card_y + 85], fill=(255, 69, 0))
        
        titulo_mayus = titulo.upper()
        chars_linea = max(24 if es_short else 35, int(len(titulo_mayus) / 3.8))
        font_tit = obtener_fuente_bold(42 if es_short else 56)
        draw.multiline_text((card_x + card_w // 2, card_y + card_h // 2 + 20), "\n".join(textwrap.wrap(titulo_mayus, width=chars_linea)), fill=(0, 0, 0), font=font_tit, anchor="mm", align="center", spacing=10)
        lienzo.save(output_png)

def parse_time(time_str):
    pt = datetime.strptime(time_str.replace(',', '.'), "%H:%M:%S.%f")
    return timedelta(hours=pt.hour, minutes=pt.minute, seconds=pt.second, microseconds=pt.microsecond)

def format_ass_time(td):
    ts = int(td.total_seconds())
    return f"{ts // 3600}:{(ts % 3600) // 60:02d}:{ts % 60:02d}.{int(td.microseconds / 10000):02d}"

def convertir_srt_a_karaoke_ass(srt_in_path, ass_out_path, duracion_intro_sec, es_short=True):
    font_size, PlayResX, PlayResY, palabras_por_grupo = (92, 1080, 1920, 1) if es_short else (120, 1920, 1080, 2)
    header = f"[Script Info]\nScriptType: v4.00+\nPlayResX: {PlayResX}\nPlayResY: {PlayResY}\n\n[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\nStyle: Karaoke,Montserrat Black,{font_size},&H0000FFFF&,&H0000FFFF&,&H00000000&,&H80000000&,1,0,0,0,100,100,0,0,1,6,2,5,0,0,0,1\n\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    
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
        musica = seleccionar_musica_fondo(emocion)
        os.makedirs(CARPETA_SALIDA, exist_ok=True)
        ruta_out = os.path.join(CARPETA_SALIDA, f"{num:02d}_{n_arch}.mp4")

        if not CONFIG["reintentar_existentes"] and archivo_valido(ruta_out):
            print(f"\n⏭️  [Video {num}] Ya existe, se omite: {os.path.basename(ruta_out)}")
            logger.info(f"Video {num} omitido (ya existe): {ruta_out}")
            return

        print(f"\n🎬 [Video {num}] Procesando: {n_arch}")
        print(f" ├─ ⚙️  Emoción: {emocion.upper()} | Música: {musica or 'Ninguna'}")
        
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
            futs = [ex.submit(generar_audio, t_tit, v_tit, "+0Hz", "+15%", a_tit, s_dum), ex.submit(generar_audio, t_cue, v_cue, p_cue, r_cue, a_cue, s_raw)]
            while not all(f.done() for f in futs):
                pct = (sum(1 for f in futs if f.done())/2)*100
                bl = int(anch * pct / 100)
                actualizar_hud([f"{txt_loc} [{pct:5.1f}%] [{'█'*bl}{' '*(anch-bl)}]"])
                time.sleep(0.5)
        errores_tts = []
        for fut, etiqueta in zip(futs, ("título", "narración")):
            try:
                ok = fut.result()
            except Exception:
                ok = False
            if not ok:
                errores_tts.append(etiqueta)

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
        convertir_srt_a_karaoke_ass(s_raw, s_ass, d_tit, es_short)
        act_gra(100.0)
        actualizar_hud([f"{txt_gra} [100.0%] [{'█'*anch}]"], True)

        # FASE 4: Render
        w, h = (1080, 1920) if es_short else (1920, 1080)
        f_ass = s_ass.replace('\\', '\\\\').replace(':', '\\:')
        if musica:
            fade_inicio = max(0.0, dur_sec - 2.0)
            fc = f"[0:v]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}[bg];color=white@0.3:s={w}x{h}[ow];[bg][ow]overlay=0:0[bgt];[bgt][3:v]overlay=0:0:enable='between(t,0,{d_tit:.2f})'[bgc];[bgc]ass='{f_ass}'[vout];[1:a]volume=1.0[av];[2:a]volume=0.18,afade=t=out:st={fade_inicio:.2f}:d=2[am];[av][am]amix=inputs=2:duration=first[aout]"
            cmd_ff = ["ffmpeg", "-hide_banner", "-y", "-stream_loop", "-1", "-i", vid_fondo, "-i", a_loc, "-stream_loop", "-1", "-i", musica, "-i", img_tar, "-filter_complex", fc, "-map", "[vout]", "-map", "[aout]"]
        else:
            fc = f"[0:v]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}[bg];color=white@0.3:s={w}x{h}[ow];[bg][ow]overlay=0:0[bgt];[bgt][2:v]overlay=0:0:enable='between(t,0,{d_tit:.2f})'[bgc];[bgc]ass='{f_ass}'[vout]"
            cmd_ff = ["ffmpeg", "-hide_banner", "-y", "-stream_loop", "-1", "-i", vid_fondo, "-i", a_loc, "-i", img_tar, "-filter_complex", fc, "-map", "[vout]", "-map", "1:a:0"]

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

            for ln in proc.stdout:
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
            return proc.returncode == 0 and archivo_valido(ruta_out)

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

def renderizar_lote_historias(archivo="guion.txt"):
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
        fallidas = []
        completados = []

        for i, h in enumerate(hists, 1):
            try:
                resultado = renderizar_una_historia(h, i)
                if resultado:
                    completados.append(resultado)
            except Exception as exc:
                fallidas.append((i, str(exc)))
                logger.error(f"Video {i} falló: {exc}")
                print(f"\n❌ Video {i} falló: {exc}")

        ruta_resultado = os.path.join(CARPETA_SALIDA, "resultado_lote.json")
        try:
            with open(ruta_resultado, "w", encoding="utf-8") as f:
                json.dump({
                    "completados": completados,
                    "fallidas": [{"numero": n, "error": e} for n, e in fallidas],
                }, f, ensure_ascii=False, indent=2)
        except Exception as exc:
            logger.warning(f"No se pudo escribir resultado_lote.json: {exc}")

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
    renderizar_lote_historias()
