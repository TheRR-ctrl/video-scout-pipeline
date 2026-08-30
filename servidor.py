"""
Servidor del panel web — corre en el teléfono y se abre desde el navegador.

Por qué un servidor local y no una app: los videos, los guiones y las
credenciales ya viven en este dispositivo. Un servidor en localhost puede
leerlos y reproducirlos directamente, sin subir nada a ninguna parte.

    pip install flask
    python servidor.py

Luego abre http://127.0.0.1:8770 en Chrome. Déjalo corriendo mientras lo
usas; Ctrl+C lo apaga.

Escucha SOLO en 127.0.0.1 a propósito: este panel ejecuta comandos del
pipeline, así que no debe quedar expuesto a la red. Si algún día lo quieres
abrir desde otro dispositivo, usa --host 0.0.0.0 sabiendo lo que implica.
"""
import os
import re
import sys
import json
import glob
import time
import signal
import shutil
import argparse
import subprocess
import threading
import unicodedata
from datetime import datetime, timezone

try:
    from flask import Flask, Response, request, jsonify, send_file, abort
except ImportError:
    raise SystemExit(
        "\nFalta Flask, que es lo único que este panel necesita aparte del pipeline.\n"
        "Instálalo con:\n\n    pip install flask\n"
    )

import secretos  # carga secretos.env si las claves no están en el entorno
from titulos import recortar_titulo, limpiar_titulo, largo_youtube

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(BASE_DIR, "web")
CARPETA_ESTADO = os.path.join(BASE_DIR, "pipeline_state")
RUTA_GUION = os.path.join(BASE_DIR, "guion.txt")
RUTA_CONFIG = os.path.join(BASE_DIR, "config.json")
RUTA_METADATA = os.path.join(CARPETA_ESTADO, "metadata.json")

ES_TERMUX = "PREFIX" in os.environ or os.path.exists("/sdcard")

app = Flask(__name__, static_folder=None)


# =========================================================
# TRABAJOS EN SEGUNDO PLANO
# =========================================================
class Trabajo:
    """Un comando corriendo, con su salida en vivo.

    Solo se permite uno a la vez: renderizar y publicar tocan los mismos
    archivos, y dos a la vez se pisarían. Además un teléfono no da para
    dos ffmpeg simultáneos.
    """

    def __init__(self, nombre, cmd):
        self.nombre = nombre
        self.cmd = cmd
        self.proc = None
        self.lineas = []
        self.estado = "corriendo"   # corriendo | pausado | ok | error | abortado
        self.inicio = time.time()
        self._lock = threading.Lock()

    def arrancar(self):
        self.proc = subprocess.Popen(
            self.cmd, cwd=BASE_DIR, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
            # Grupo propio: así pausar/abortar alcanza también a ffmpeg,
            # que es hijo del script y es quien realmente hace el trabajo.
            start_new_session=True,
        )
        threading.Thread(target=self._leer, daemon=True).start()

    def _leer(self):
        for linea in self.proc.stdout:
            # El HUD de la terminal repinta con escapes ANSI; en el navegador
            # solo serían basura, así que se limpian y se descartan las
            # líneas que no aportan.
            limpia = re.sub(r"\x1b\[[0-9;?]*[a-zA-Z]", "", linea).rstrip()
            if not limpia.strip():
                continue
            with self._lock:
                self.lineas.append(limpia)
                if len(self.lineas) > 400:
                    del self.lineas[:100]
        self.proc.wait()
        if self.estado not in ("abortado",):
            self.estado = "ok" if self.proc.returncode == 0 else "error"

    def pausar(self):
        if self.proc and self.estado == "corriendo":
            os.killpg(os.getpgid(self.proc.pid), signal.SIGSTOP)
            self.estado = "pausado"

    def reanudar(self):
        if self.proc and self.estado == "pausado":
            os.killpg(os.getpgid(self.proc.pid), signal.SIGCONT)
            self.estado = "corriendo"

    def abortar(self):
        if not self.proc:
            return
        gpid = os.getpgid(self.proc.pid)
        # Si está detenido no reacciona a SIGTERM: primero se reanuda.
        if self.estado == "pausado":
            os.killpg(gpid, signal.SIGCONT)
        self.estado = "abortado"
        os.killpg(gpid, signal.SIGTERM)
        threading.Timer(4.0, self._rematar, [gpid]).start()

    def _rematar(self, gpid):
        try:
            if self.proc.poll() is None:
                os.killpg(gpid, signal.SIGKILL)
        except Exception:
            pass

    def como_dict(self):
        with self._lock:
            lineas = list(self.lineas[-120:])
        return {
            "nombre": self.nombre,
            "estado": self.estado,
            "segundos": int(time.time() - self.inicio),
            "lineas": lineas,
        }


TRABAJO = {"actual": None}


def lanzar(nombre, cmd):
    actual = TRABAJO["actual"]
    if actual and actual.estado in ("corriendo", "pausado"):
        return None, f"Ya hay algo corriendo: {actual.nombre}"
    t = Trabajo(nombre, cmd)
    t.arrancar()
    TRABAJO["actual"] = t
    return t, None


# =========================================================
# LECTURA DE ESTADO
# =========================================================
def leer_json(ruta, default):
    try:
        with open(ruta, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def cfg_actual():
    import generar_video_maestro as gvm
    return gvm.cargar_config(RUTA_CONFIG)


def historias_del_guion():
    if not os.path.exists(RUTA_GUION):
        return []
    import generar_video_maestro as gvm
    with open(RUTA_GUION, "r", encoding="utf-8") as f:
        bloques = [b.strip() for b in f.read().split("===NUEVA_HISTORIA===") if b.strip()]

    out = []
    for i, b in enumerate(bloques, 1):
        lineas = [
            l.strip() for l in b.splitlines()
            if l.strip() and not l.strip().startswith(("#", "===", "📌", "🎙️"))
        ]
        titulo = lineas[0] if lineas else "(sin título)"
        palabras = len(" ".join(lineas[1:]).split()) if len(lineas) > 1 else 0
        segs = int(palabras / 2.6)   # ritmo típico de la narración generada
        out.append({
            "n": i,
            "titulo": titulo,
            "emocion": gvm.detectar_emocion_historia(b),
            # La voz con la que se va a narrar. Verla antes de renderizar
            # ahorra descubrir en el video ya hecho que salió la contraria.
            "genero": gvm.decidir_genero_narrador(b),
            "duracion": f"{segs // 60}:{segs % 60:02d}",
            "palabras": palabras,
        })
    return out


def videos_renderizados():
    """Los del registro cuyo archivo sigue existiendo, más su estado."""
    cfg = cfg_actual()
    carpeta = cfg["carpeta_salida"]
    lote = leer_json(os.path.join(carpeta, "resultado_lote.json"), {})
    completados = lote.get("completados", [])

    mp4s = glob.glob(os.path.join(carpeta, "*.mp4"))
    reales = {unicodedata.normalize("NFC", os.path.basename(m)): m for m in mp4s}

    publicados = leer_json(os.path.join(CARPETA_ESTADO, "publicados.json"), [])
    rechazados = leer_json(os.path.join(CARPETA_ESTADO, "rechazados.json"), [])
    ya = {p.get("ruta") for p in publicados} | {r.get("ruta") for r in rechazados}
    metadatos = leer_json(RUTA_METADATA, {})

    out = []
    for v in completados:
        ruta = v.get("ruta", "")
        if not os.path.exists(ruta):
            # Mismo rescate por nombre que estado.py: en la SD los acentos
            # pueden quedar normalizados distinto y la ruta no resuelve.
            ruta = reales.get(unicodedata.normalize("NFC", os.path.basename(ruta)), "")
            if not ruta:
                continue
        out.append({
            "numero": v.get("numero"),
            "titulo": v.get("titulo", ""),
            "cuerpo": v.get("cuerpo", ""),
            "emocion": v.get("emocion", ""),
            "duracion_sec": v.get("duracion_sec", 0),
            "es_short": v.get("es_short", True),
            "musica": v.get("musica_archivo"),
            "fuente_url": v.get("fuente_url"),
            "archivo": os.path.basename(ruta),
            # Va a la URL de la miniatura. Sin esto, rehacer un video sin
            # cambiarle el nombre dejaría al navegador enseñando la miniatura
            # vieja durante los siete días de caché.
            "mtime": int(os.path.getmtime(ruta)),
            "publicado": ruta in ya,
            # Lo que publisher.py subirá tal cual. None mientras no se haya
            # preparado: el panel distingue "todavía no existe" de "existe y
            # dice esto", que no es lo mismo para quien va a aprobarlo.
            "meta": metadatos.get(os.path.basename(ruta)),
        })
    return out


def material():
    cfg = cfg_actual()
    vert = [f for p in ("fondo_vertical*", "fondo_gameplay*")
            for f in glob.glob(os.path.join(BASE_DIR, p))]
    horiz = glob.glob(os.path.join(BASE_DIR, "fondo_horizontal*"))
    musica = glob.glob(os.path.join(BASE_DIR, "musica_*.mp3"))
    plantilla = next(
        (p for p in ("tarjeta_plantilla.png", "tarjeta_plantilla.jpg", "Tarjeta de inicio.png")
         if os.path.exists(os.path.join(BASE_DIR, p))), None)
    base_sfx = (cfg.get("sonido_transicion") or "").strip()
    efectos = []
    if base_sfx:
        efectos = [f for f in glob.glob(os.path.join(BASE_DIR, base_sfx + "*"))
                   if f.lower().endswith((".mp3", ".wav", ".m4a", ".ogg", ".aac"))]
    return {
        "fondos_short": len(vert), "fondos_largo": len(horiz),
        "musica": len(musica), "plantilla": plantilla, "efectos": len(efectos),
    }


EXT_AUDIO = (".mp3", ".m4a", ".wav", ".aac", ".ogg")

# Las mismas cuatro de detectar_emocion_historia. "fondo" es el cajón para
# pistas que sirven con cualquier emoción.
EMOCIONES = ("drama", "venganza", "suspenso", "comedia", "fondo")


def pistas_musica():
    """Las pistas del repo, con su emoción y su atribución.

    La emoción sale del nombre del archivo (musica_<emocion>_...) porque es
    justo lo que mira el render al elegir: mostrar otra cosa aquí haría que
    el panel y el video no coincidieran.
    """
    atrib = leer_json(os.path.join(CARPETA_ESTADO, "musica_atribucion.json"), {})
    out = []
    for f in sorted(os.listdir(BASE_DIR)):
        if not f.startswith("musica_") or not f.lower().endswith(EXT_AUDIO):
            continue
        resto = f[len("musica_"):]
        emocion = next((e for e in EMOCIONES if resto.startswith(e + "_")), "fondo")
        a = atrib.get(f) or {}
        out.append({
            "archivo": f,
            "emocion": emocion,
            "artista": a.get("artista") or "",
            "titulo": a.get("titulo") or "",
            "kb": os.path.getsize(os.path.join(BASE_DIR, f)) // 1024,
        })
    return out


@app.get("/api/musica")
def api_musica():
    return jsonify(pistas_musica())


@app.post("/api/musica/emocion")
def api_musica_emocion():
    """Reclasifica una pista renombrándola.

    El render decide por el nombre del archivo, así que mover una pista de
    emoción ES renombrarla; guardar la etiqueta en otro lado dejaría el panel
    diciendo una cosa y el render haciendo otra. Se arrastra la atribución
    para no perder el crédito del autor.
    """
    d = request.json or {}
    archivo = os.path.basename(d.get("archivo") or "")
    emocion = (d.get("emocion") or "").strip().lower()
    if emocion not in EMOCIONES:
        return jsonify({"error": "Emoción desconocida"}), 400
    if not archivo.startswith("musica_") or not archivo.lower().endswith(EXT_AUDIO):
        return jsonify({"error": "No es una pista de música"}), 400

    origen = os.path.join(BASE_DIR, archivo)
    if not os.path.isfile(origen):
        return jsonify({"error": "No existe esa pista"}), 404

    resto = archivo[len("musica_"):]
    for e in EMOCIONES:
        if resto.startswith(e + "_"):
            resto = resto[len(e) + 1:]
            break
    nuevo = f"musica_{emocion}_{resto}"
    if nuevo == archivo:
        return jsonify({"ok": True, "archivo": archivo})
    destino = os.path.join(BASE_DIR, nuevo)
    if os.path.exists(destino):
        return jsonify({"error": f"Ya existe {nuevo}"}), 409

    os.rename(origen, destino)
    ruta_atrib = os.path.join(CARPETA_ESTADO, "musica_atribucion.json")
    atrib = leer_json(ruta_atrib, {})
    if archivo in atrib:
        atrib[nuevo] = atrib.pop(archivo)
        os.makedirs(CARPETA_ESTADO, exist_ok=True)
        with open(ruta_atrib, "w", encoding="utf-8") as f:
            json.dump(atrib, f, ensure_ascii=False, indent=2)
    return jsonify({"ok": True, "archivo": nuevo})


# Ajustes numéricos que el panel puede tocar, con su rango. La lista blanca
# evita que una petición pueda escribir cualquier clave arbitraria en
# config.json — que es el archivo del que depende todo el render.
AJUSTES_NUMERICOS = {
    "volumen_musica": (0.0, 1.0),
    "volumen_locucion": (0.0, 1.0),
    "volumen_sonido_transicion": (0.0, 1.0),
    "velo_blanco_fondo": (0.0, 1.0),
}


@app.post("/api/recorte")
def api_recorte():
    """Cómo quedaría un título tras el recorte.

    El panel lo consulta en vez de repetir el algoritmo en JavaScript: dos
    implementaciones acabarían divergiendo y la vista previa mentiría justo
    cuando más importa. El servidor es local, así que preguntar cuesta nada.
    """
    titulo = (request.json or {}).get("titulo", "")
    recortado = recortar_titulo(titulo)
    return jsonify({
        "titulo": recortado,
        "largo": largo_youtube(recortado),
        "largo_original": largo_youtube(limpiar_titulo(titulo)),
        "recortado": recortado != limpiar_titulo(titulo),
    })


@app.post("/api/metadata/<path:archivo>")
def api_metadata_guardar(archivo):
    """Guarda el título, la descripción y los hashtags corregidos a mano.

    Se marca origen="manual" para que ni publisher.py ni preparar_metadata
    la regeneren después: perder una corrección tuya porque un paso posterior
    volvió a preguntarle a Gemini sería justo lo contrario de poder editarla.
    """
    nombre = os.path.basename(archivo)
    d = request.json or {}

    titulo = (d.get("titulo_youtube") or "").strip()
    if not titulo:
        return jsonify({"error": "El título no puede quedar vacío"}), 400

    # Se recorta por palabras: YouTube corta a 100 y un tajo seco parte la
    # última palabra por la mitad.
    titulo = recortar_titulo(titulo)

    hashtags = []
    for h in (d.get("hashtags") or []):
        limpio = re.sub(r"[^\w]", "", str(h))
        if limpio and limpio not in hashtags:
            hashtags.append(limpio)
    hashtags = hashtags[:6]   # el mismo tope que aplica construir_descripcion

    almacen = leer_json(RUTA_METADATA, {})
    previa = almacen.get(nombre) or {}
    almacen[nombre] = {
        "aprobado": True,          # si lo estás guardando, lo estás aprobando
        "motivo_rechazo": "",
        "titulo_youtube": titulo,
        "descripcion_youtube": (d.get("descripcion_youtube") or "").strip(),
        "hashtags": hashtags,
        "origen": "manual",
        "origen_previo": previa.get("origen", ""),
    }
    os.makedirs(CARPETA_ESTADO, exist_ok=True)
    with open(RUTA_METADATA, "w", encoding="utf-8") as f:
        json.dump(almacen, f, ensure_ascii=False, indent=2)
    return jsonify({"ok": True, "meta": almacen[nombre]})


@app.post("/api/ajuste")
def api_ajuste():
    d = request.json or {}
    clave = d.get("clave")
    if clave not in AJUSTES_NUMERICOS:
        return jsonify({"error": "Ajuste desconocido"}), 400
    try:
        valor = float(d.get("valor"))
    except (TypeError, ValueError):
        return jsonify({"error": "Valor no numérico"}), 400
    lo, hi = AJUSTES_NUMERICOS[clave]
    valor = max(lo, min(hi, valor))

    cfg = leer_json(RUTA_CONFIG, {})
    cfg[clave] = valor
    with open(RUTA_CONFIG, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    return jsonify({"ok": True, "clave": clave, "valor": valor})


def credenciales():
    out = []
    for clave, tiene, origen in secretos.estado():
        out.append({"nombre": clave, "ok": tiene, "origen": origen,
                    "opcional": clave == "JAMENDO_CLIENT_ID"})
    for archivo in ("client_secret.json", "youtube_token.json"):
        out.append({"nombre": archivo, "ok": os.path.exists(os.path.join(BASE_DIR, archivo)),
                    "origen": "", "opcional": False})
    return out


# =========================================================
# API
# =========================================================
@app.get("/api/estado")
def api_estado():
    import generar_video_maestro as gvm
    cfg = cfg_actual()
    publicados = leer_json(os.path.join(CARPETA_ESTADO, "publicados.json"), [])

    ahora = datetime.now(timezone.utc)
    pubs = []
    for p in publicados:
        dias = None
        if p.get("subido_en") and not p.get("_borrado_local"):
            try:
                s = datetime.strptime(p["subido_en"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                dias = max(0, 7 - (ahora - s).days)
            except ValueError:
                pass
        pubs.append({
            "titulo": p.get("titulo_youtube", ""),
            "video_id": p.get("video_id"),
            "publish_at": p.get("publish_at"),
            # Lo que YouTube dice de verdad. Si pedimos ventana de revisión y
            # no la aplicó, quieres verlo aquí y no descubrirlo en el canal.
            "programado_ok": p.get("programado_ok"),
            "privacidad_real": p.get("privacidad_real"),
            "dias_restantes": dias,
            "borrado_local": bool(p.get("_borrado_local")),
        })

    return jsonify({
        "historias": historias_del_guion(),
        "videos": videos_renderizados(),
        "publicados": pubs,
        "material": material(),
        "credenciales": credenciales(),
        "subtitulos": cfg["subtitulos"],
        "presets": list(gvm.PRESETS_SUBTITULOS.keys()),
        "solo_wifi": cfg.get("solo_wifi", True),
        "musica": pistas_musica(),
        "fondos": sorted(
            os.path.basename(f) for p in ("fondo_vertical*", "fondo_horizontal*", "fondo_gameplay*")
            for f in glob.glob(os.path.join(BASE_DIR, p))
            if f.lower().endswith((".mp4", ".webm", ".mkv", ".mov"))
        ),
        "ajustes": {k: cfg.get(k) for k in AJUSTES_NUMERICOS},
        "trabajo": TRABAJO["actual"].como_dict() if TRABAJO["actual"] else None,
    })


# Lo que un botón puede ejecutar. Es una lista blanca a propósito: el
# navegador manda un nombre, nunca un comando — así una pestaña abierta por
# error no puede pedir la ejecución de algo arbitrario.
ACCIONES = {
    "musica":     ("Actualizando música", [sys.executable, "actualizar_musica.py"]),
    "fondos":     ("Enlazando material", [sys.executable, "vincular_fondos.py"]),
    "buscar":     ("Buscando historias", [sys.executable, "trend_scout.py"]),
    # Explica un escaneo que no trajo nada: cuántos posts se leyeron y por qué
    # se descartó cada uno (ya usados, sin texto, muy cortos/largos).
    "diagnostico_busqueda": ("Revisando la búsqueda", [sys.executable, "trend_scout.py", "--diagnostico"]),
    "guiones":    ("Escribiendo guiones", [sys.executable, "script_writer.py"]),
    "publicar":   ("Publicando en YouTube", [sys.executable, "publisher.py"]),
    "publicar_datos": ("Publicando (datos móviles)", [sys.executable, "publisher.py", "--con-datos"]),
    "previsualizar": ("Generando comparación de estilos", [sys.executable, "previsualizar_estilos.py"]),
    "metadata":   ("Preparando títulos y hashtags", [sys.executable, "preparar_metadata.py"]),
}


@app.post("/api/ejecutar/<accion>")
def api_ejecutar(accion):
    if accion == "renderizar":
        d = request.json or {}
        sel = d.get("historias") or ""
        cmd = [sys.executable, "generar_video_maestro.py"]
        if sel:
            cmd += ["--historias", str(sel)]
        # Rehacer reemplaza el archivo; sin esto el render se salta la
        # historia por existir ya, y el botón no haría nada.
        if d.get("rehacer"):
            cmd += ["--rehacer"]
        if d.get("voz") in ("masculina", "femenina"):
            cmd += ["--voz", d["voz"]]
        if d.get("estilo"):
            cmd += ["--estilo", str(d["estilo"])]
        # Música elegida a mano: {"3": "musica_x.mp3"} o {"*": "..."} para
        # todas. Lo que no venga aquí sale al azar, igual que en el cron.
        elegidas = d.get("musica") or {}
        if isinstance(elegidas, dict):
            pares = [f"{k}={v}" for k, v in elegidas.items() if v]
            if pares:
                cmd += ["--musica", ",".join(pares)]
        if d.get("fondo"):
            cmd += ["--fondo", str(d["fondo"])]
        if d.get("volumen_musica") is not None:
            cmd += ["--volumen-musica", str(d["volumen_musica"])]
        t, err = lanzar("Rehaciendo" if d.get("rehacer") else "Renderizando", cmd)
    elif accion == "regenerar_metadata":
        # Volver a preguntarle a Gemini por UN video. --forzar porque el
        # botón solo aparece cuando ya la estás mirando: pedirlo ahí es
        # pedirlo a sabiendas de que reemplaza lo que hay.
        d = request.json or {}
        cmd = [sys.executable, "preparar_metadata.py", "--rehacer", "--forzar"]
        if d.get("numero") is not None:
            cmd += ["--solo", str(d["numero"])]
        t, err = lanzar("Regenerando con Gemini", cmd)
    elif accion in ACCIONES:
        nombre, cmd = ACCIONES[accion]
        t, err = lanzar(nombre, cmd)
    else:
        return jsonify({"error": "Acción desconocida"}), 400

    if err:
        return jsonify({"error": err}), 409
    return jsonify({"ok": True, "trabajo": t.como_dict()})


@app.post("/api/trabajo/<que>")
def api_trabajo(que):
    t = TRABAJO["actual"]
    if not t:
        return jsonify({"error": "No hay nada corriendo"}), 404
    if que == "pausar":
        t.pausar()
    elif que == "reanudar":
        t.reanudar()
    elif que == "abortar":
        t.abortar()
    else:
        return jsonify({"error": "Acción desconocida"}), 400
    return jsonify({"ok": True, "trabajo": t.como_dict()})


@app.post("/api/preset")
def api_preset():
    """Fija el preset de subtítulos en config.json, conservando lo demás."""
    import generar_video_maestro as gvm
    nombre = (request.json or {}).get("preset", "")
    if nombre not in gvm.PRESETS_SUBTITULOS:
        return jsonify({"error": "Preset desconocido"}), 400

    cfg = leer_json(RUTA_CONFIG, {})
    subs = dict(cfg.get("subtitulos") or {})
    subs["preset"] = nombre
    cfg["subtitulos"] = subs
    with open(RUTA_CONFIG, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    return jsonify({"ok": True, "preset": nombre})


@app.post("/api/wifi")
def api_wifi():
    cfg = leer_json(RUTA_CONFIG, {})
    cfg["solo_wifi"] = bool((request.json or {}).get("solo_wifi", True))
    with open(RUTA_CONFIG, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    return jsonify({"ok": True, "solo_wifi": cfg["solo_wifi"]})


# Lo que se puede copiar para pegarlo en los Secrets de GitHub Actions.
# Nombre en GitHub -> de dónde sale el valor.
SECRETOS_COPIABLES = {
    "YOUTUBE_TOKEN": ("archivo", "youtube_token.json"),
    "YOUTUBE_CLIENT_SECRET": ("archivo", "client_secret.json"),
    "GEMINI_API_KEY": ("entorno", "GEMINI_API_KEY"),
    "JAMENDO_CLIENT_ID": ("entorno", "JAMENDO_CLIENT_ID"),
}


@app.get("/api/secretos")
def api_secretos():
    """Qué hay disponible para copiar, SIN los valores.

    Los valores se piden aparte y de uno en uno: así el contenido de un
    token no viaja en cada refresco del panel ni queda en el historial de
    peticiones solo por tener la pestaña abierta.
    """
    out = []
    for nombre, (tipo, ref) in SECRETOS_COPIABLES.items():
        if tipo == "archivo":
            ruta = os.path.join(BASE_DIR, ref)
            existe = os.path.exists(ruta)
            pista = f"{ref} · {os.path.getsize(ruta)} B" if existe else f"falta {ref}"
        else:
            valor = os.environ.get(ref, "")
            existe = bool(valor)
            pista = (valor[:4] + "…" + valor[-4:]) if len(valor) > 10 else ("configurada" if existe else "falta")
        out.append({"nombre": nombre, "disponible": existe, "pista": pista, "tipo": tipo})
    return jsonify(out)


@app.get("/api/secretos/<nombre>")
def api_secreto_valor(nombre):
    if nombre not in SECRETOS_COPIABLES:
        return jsonify({"error": "Secreto desconocido"}), 404
    tipo, ref = SECRETOS_COPIABLES[nombre]

    if tipo == "archivo":
        ruta = os.path.join(BASE_DIR, ref)
        if not os.path.exists(ruta):
            return jsonify({"error": f"No existe {ref}"}), 404
        with open(ruta, "r", encoding="utf-8") as f:
            valor = f.read().strip()
    else:
        valor = os.environ.get(ref, "")
        if not valor:
            return jsonify({"error": f"{ref} no está configurada"}), 404

    return jsonify({"nombre": nombre, "valor": valor})


@app.post("/api/secretos/<nombre>")
def api_secreto_guardar(nombre):
    """Guarda una clave en secretos.env desde el panel.

    Antes solo se podían mirar: si faltaba GEMINI_API_KEY había que volver a
    Termux, escribir un export y reiniciar todo. Se escribe en el archivo
    (que sobrevive a cerrar la terminal) y también en el entorno de este
    proceso, para que los trabajos que lance a continuación ya la vean.
    """
    if nombre not in SECRETOS_COPIABLES:
        return jsonify({"error": "Secreto desconocido"}), 404
    tipo, ref = SECRETOS_COPIABLES[nombre]
    if tipo != "entorno":
        return jsonify({"error": "Los archivos de credenciales no se editan aquí"}), 400

    valor = (request.json or {}).get("valor", "").strip()
    if not valor:
        return jsonify({"error": "Valor vacío"}), 400

    lineas = []
    if os.path.exists(secretos.RUTA_SECRETOS):
        with open(secretos.RUTA_SECRETOS, "r", encoding="utf-8") as f:
            lineas = f.read().splitlines()
    # Se reemplaza la línea existente en su sitio en vez de añadir otra:
    # con dos líneas de la misma clave ganaría la primera, y editar desde el
    # panel parecería no haber hecho nada.
    salida, puesta = [], False
    for linea in lineas:
        if linea.strip().startswith(ref + "="):
            if not puesta:
                salida.append(f"{ref}={valor}")
                puesta = True
        else:
            salida.append(linea)
    if not puesta:
        salida.append(f"{ref}={valor}")

    with open(secretos.RUTA_SECRETOS, "w", encoding="utf-8") as f:
        f.write("\n".join(salida).rstrip() + "\n")
    os.chmod(secretos.RUTA_SECRETOS, 0o600)   # es una credencial, no un config
    os.environ[ref] = valor
    secretos._DESDE_ARCHIVO.add(ref)
    return jsonify({"ok": True, "nombre": nombre})


@app.get("/video/<path:archivo>")
def api_video(archivo):
    """Sirve el .mp4 con soporte de Range, que es lo que permite adelantar
    y retroceder en el reproductor. Sin esto el navegador solo puede
    reproducir de corrido desde el principio."""
    carpeta = cfg_actual()["carpeta_salida"]
    ruta = os.path.join(carpeta, os.path.basename(archivo))
    if not os.path.isfile(ruta):
        abort(404)
    return servir_con_rango(ruta, "video/mp4")


CARPETA_MINIATURAS = os.path.join(CARPETA_ESTADO, "miniaturas")

# El panel pide todas las miniaturas de golpe, y cada una que falte lanza un
# ffmpeg. En un teléfono, cinco a la vez compiten con el render que puede
# estar corriendo. De una en una tardan lo mismo en total y no ahogan nada.
_LOCK_MINIATURAS = threading.Lock()


@app.get("/miniatura/<path:archivo>")
def api_miniatura(archivo):
    """Un fotograma del video, para que la tira de arriba enseñe de qué va
    cada uno en vez de un rectángulo gris con la duración.

    Se saca con ffmpeg y se guarda en disco: extraerlo cuesta un momento y
    el panel pide todas las miniaturas a la vez cada vez que refresca. Se
    rehace solo si el .mp4 es más nuevo que el .jpg, que es lo que pasa
    cuando rehaces un video sin cambiarle el nombre.
    """
    nombre = os.path.basename(archivo)
    carpeta = cfg_actual()["carpeta_salida"]
    video = os.path.join(carpeta, nombre)
    if not os.path.isfile(video):
        abort(404)

    os.makedirs(CARPETA_MINIATURAS, exist_ok=True)
    jpg = os.path.join(CARPETA_MINIATURAS, nombre + ".jpg")

    def hay_que_sacarla():
        return (not os.path.isfile(jpg)
                or os.path.getmtime(jpg) < os.path.getmtime(video))

    if hay_que_sacarla():
        with _LOCK_MINIATURAS:
            # Otra petición pudo sacarla mientras esperábamos el turno.
            if hay_que_sacarla():
                # Segundo 1, no 0: el primer fotograma suele ser el fundido
                # de entrada y sale negro, que no distingue un video de otro.
                tmp = jpg + ".tmp.jpg"
                try:
                    subprocess.run(
                        ["ffmpeg", "-y", "-ss", "1", "-i", video, "-frames:v", "1",
                         "-vf", "scale=-2:220", "-q:v", "6", tmp],
                        check=True, capture_output=True, timeout=25,
                    )
                    os.replace(tmp, jpg)
                except Exception as exc:
                    print(f"  No se pudo sacar la miniatura de {nombre}: {exc}",
                          file=sys.stderr)
                    if os.path.exists(tmp):
                        os.remove(tmp)
                    abort(404)

    # Se puede cachear fuerte porque la URL lleva el nombre del archivo y el
    # panel le añade la fecha de modificación cuando cambia.
    resp = send_file(jpg, mimetype="image/jpeg", conditional=True)
    resp.headers["Cache-Control"] = "public, max-age=604800"
    return resp


@app.get("/audio/<path:archivo>")
def api_audio(archivo):
    """Sirve una pista de música para poder oírla en el panel antes de
    comprometer un render de varios minutos con ella."""
    nombre = os.path.basename(archivo)
    # Solo música del repo: cualquier otra ruta se rechaza en vez de
    # dejar que un nombre con ../ saque archivos de otro sitio.
    if not nombre.startswith("musica_") or not nombre.lower().endswith(EXT_AUDIO):
        abort(404)
    ruta = os.path.join(BASE_DIR, nombre)
    if not os.path.isfile(ruta):
        abort(404)
    tipos = {".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".wav": "audio/wav",
             ".aac": "audio/aac", ".ogg": "audio/ogg"}
    return servir_con_rango(ruta, tipos.get(os.path.splitext(nombre)[1].lower(), "audio/mpeg"))


def servir_con_rango(ruta, mimetype):
    tam = os.path.getsize(ruta)
    rango = request.headers.get("Range")
    if not rango:
        return send_file(ruta, mimetype=mimetype, conditional=True)

    m = re.match(r"bytes=(\d+)-(\d*)", rango)
    if not m:
        return send_file(ruta, mimetype=mimetype, conditional=True)
    ini = int(m.group(1))
    fin = int(m.group(2)) if m.group(2) else min(ini + 1024 * 1024 * 4, tam - 1)
    fin = min(fin, tam - 1)
    largo = fin - ini + 1

    def trozo():
        with open(ruta, "rb") as f:
            f.seek(ini)
            restante = largo
            while restante > 0:
                datos = f.read(min(65536, restante))
                if not datos:
                    break
                restante -= len(datos)
                yield datos

    resp = Response(trozo(), 206, mimetype=mimetype, direct_passthrough=True)
    resp.headers["Content-Range"] = f"bytes {ini}-{fin}/{tam}"
    resp.headers["Accept-Ranges"] = "bytes"
    resp.headers["Content-Length"] = str(largo)
    return resp


# =========================================================
# APAGADO
# =========================================================
# El panel manda un latido mientras la pestaña está abierta. Si deja de
# llegar, es que se cerró — y no tiene sentido dejar el servidor y el wake
# lock consumiendo batería.
#
# El margen es amplio a propósito: Chrome en Android ralentiza los
# temporizadores de las pestañas en segundo plano, así que cambiar de app un
# rato NO debe apagar nada. Y nunca se apaga con un trabajo corriendo: si
# cierras la pestaña a media renderización, lo que quieres es que termine.
MARGEN_SIN_LATIDO = 240      # segundos
ULTIMO_LATIDO = {"t": time.time()}
APAGAR = {"pedido": False}


@app.post("/api/latido")
def api_latido():
    ULTIMO_LATIDO["t"] = time.time()
    return jsonify({"ok": True})


@app.post("/api/apagar")
def api_apagar():
    APAGAR["pedido"] = True
    return jsonify({"ok": True})


def vigilante(margen):
    while True:
        time.sleep(5)
        if APAGAR["pedido"]:
            break
        t = TRABAJO["actual"]
        if t and t.estado in ("corriendo", "pausado"):
            # Hay trabajo en curso: se posterga la cuenta, no se apaga.
            ULTIMO_LATIDO["t"] = time.time()
            continue
        if time.time() - ULTIMO_LATIDO["t"] > margen:
            print("\n  Panel cerrado y sin trabajo pendiente — apagando el servidor.")
            break
    os.kill(os.getpid(), signal.SIGINT)


@app.get("/manifest.webmanifest")
def api_manifest():
    """Permite instalar el panel desde Chrome ("Agregar a pantalla de
    inicio"): queda con su propio icono y abre sin barra de navegador."""
    return jsonify({
        "name": "Mesa de Revisión",
        "short_name": "Mesa",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#12151a",
        "theme_color": "#12151a",
        "icons": [
            {"src": "/icono-192.png", "sizes": "192x192", "type": "image/png"},
            {"src": "/icono-512.png", "sizes": "512x512", "type": "image/png",
             "purpose": "any maskable"},
        ],
    })


@app.get("/icono-<int:tam>.png")
def api_icono(tam):
    ruta = os.path.join(WEB_DIR, f"icono-{tam}.png")
    if not os.path.exists(ruta):
        abort(404)
    return send_file(ruta, mimetype="image/png")


@app.get("/")
def index():
    ruta = os.path.join(WEB_DIR, "index.html")
    if not os.path.exists(ruta):
        return "Falta web/index.html", 500
    return send_file(ruta)


def main():
    parser = argparse.ArgumentParser(description="Panel web del pipeline.")
    parser.add_argument("--puerto", type=int, default=8770)
    parser.add_argument(
        "--host", default="127.0.0.1",
        help="Por defecto solo este dispositivo. 0.0.0.0 lo abre a la red local "
             "— el panel ejecuta comandos, así que hazlo solo si sabes lo que implica.",
    )
    parser.add_argument("--sin-wakelock", action="store_true",
                        help="No pedir el wake lock de Termux al arrancar.")
    parser.add_argument("--no-apagar", action="store_true",
                        help="No apagarse solo al cerrar el panel (útil si lo dejas de fondo).")
    parser.add_argument("--abrir", action="store_true",
                        help="Abrir el navegador automáticamente al arrancar.")
    args = parser.parse_args()

    # Android suspende los procesos en segundo plano. Al cambiar de Termux a
    # Chrome el servidor se congela y el navegador ve "conexión rechazada",
    # que es exactamente el síntoma que hay que evitar aquí: el panel solo
    # sirve si sigue vivo mientras miras otra app. El wake lock lo impide.
    import socket
    with socket.socket() as s_prueba:
        s_prueba.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s_prueba.bind((args.host, args.puerto))
        except OSError:
            raise SystemExit(
                f"\nEl puerto {args.puerto} ya está ocupado — probablemente por otro\n"
                f"panel que quedó corriendo. Ciérralo con:\n\n"
                f"    pkill -f servidor.py\n\n"
                f"o usa otro puerto:  python servidor.py --puerto 8771\n"
            )

    wakelock = False
    if not args.sin_wakelock and shutil.which("termux-wake-lock"):
        try:
            subprocess.run(["termux-wake-lock"], timeout=5, capture_output=True)
            wakelock = True
        except Exception:
            pass

    print(f"\n  Panel listo en:  http://127.0.0.1:{args.puerto}")
    print( "  Ábrelo en Chrome. Ctrl+C aquí para apagarlo.")
    if wakelock:
        print( "  Wake lock activo: Termux no se dormirá mientras esto corra.")
    elif ES_TERMUX:
        print( "  ⚠️  Sin termux-wake-lock (falta el paquete termux-api).")
        print( "      Android puede congelar el servidor al cambiarte a Chrome.")
        print( "      Instálalo con:  pkg install termux-api")
    print()

    if not args.no_apagar:
        ULTIMO_LATIDO["t"] = time.time()
        threading.Thread(target=vigilante, args=(MARGEN_SIN_LATIDO,), daemon=True).start()
        print(f"  Se apaga solo si cierras el panel (y no hay nada corriendo).")

    if args.abrir and shutil.which("termux-open-url"):
        # Un momento para que Flask levante antes de que el navegador pida.
        threading.Timer(1.5, lambda: subprocess.run(
            ["termux-open-url", f"http://127.0.0.1:{args.puerto}"],
            capture_output=True)).start()

    try:
        app.run(host=args.host, port=args.puerto, threaded=True)
    except KeyboardInterrupt:
        pass
    finally:
        if wakelock:
            try:
                subprocess.run(["termux-wake-unlock"], timeout=5, capture_output=True)
            except Exception:
                pass


if __name__ == "__main__":
    main()
