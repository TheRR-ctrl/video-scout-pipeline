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

from flask import Flask, Response, request, jsonify, send_file, abort

import secretos  # carga secretos.env si las claves no están en el entorno

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(BASE_DIR, "web")
CARPETA_ESTADO = os.path.join(BASE_DIR, "pipeline_state")
RUTA_GUION = os.path.join(BASE_DIR, "guion.txt")
RUTA_CONFIG = os.path.join(BASE_DIR, "config.json")

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
            "publicado": ruta in ya,
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
        "trabajo": TRABAJO["actual"].como_dict() if TRABAJO["actual"] else None,
    })


# Lo que un botón puede ejecutar. Es una lista blanca a propósito: el
# navegador manda un nombre, nunca un comando — así una pestaña abierta por
# error no puede pedir la ejecución de algo arbitrario.
ACCIONES = {
    "musica":     ("Actualizando música", [sys.executable, "actualizar_musica.py"]),
    "fondos":     ("Enlazando material", [sys.executable, "vincular_fondos.py"]),
    "buscar":     ("Buscando historias", [sys.executable, "trend_scout.py"]),
    "guiones":    ("Escribiendo guiones", [sys.executable, "script_writer.py"]),
    "publicar":   ("Publicando en YouTube", [sys.executable, "publisher.py"]),
    "publicar_datos": ("Publicando (datos móviles)", [sys.executable, "publisher.py", "--con-datos"]),
    "previsualizar": ("Generando comparación de estilos", [sys.executable, "previsualizar_estilos.py"]),
}


@app.post("/api/ejecutar/<accion>")
def api_ejecutar(accion):
    if accion == "renderizar":
        sel = (request.json or {}).get("historias") or ""
        cmd = [sys.executable, "generar_video_maestro.py"]
        if sel:
            cmd += ["--historias", str(sel)]
        t, err = lanzar("Renderizando", cmd)
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


@app.get("/video/<path:archivo>")
def api_video(archivo):
    """Sirve el .mp4 con soporte de Range, que es lo que permite adelantar
    y retroceder en el reproductor. Sin esto el navegador solo puede
    reproducir de corrido desde el principio."""
    carpeta = cfg_actual()["carpeta_salida"]
    ruta = os.path.join(carpeta, os.path.basename(archivo))
    if not os.path.isfile(ruta):
        abort(404)

    tam = os.path.getsize(ruta)
    rango = request.headers.get("Range")
    if not rango:
        return send_file(ruta, mimetype="video/mp4", conditional=True)

    m = re.match(r"bytes=(\d+)-(\d*)", rango)
    if not m:
        return send_file(ruta, mimetype="video/mp4", conditional=True)
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

    resp = Response(trozo(), 206, mimetype="video/mp4", direct_passthrough=True)
    resp.headers["Content-Range"] = f"bytes {ini}-{fin}/{tam}"
    resp.headers["Accept-Ranges"] = "bytes"
    resp.headers["Content-Length"] = str(largo)
    return resp


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
    args = parser.parse_args()

    print(f"\n  Panel listo en:  http://127.0.0.1:{args.puerto}")
    print( "  Ábrelo en Chrome. Ctrl+C aquí para apagarlo.\n")
    app.run(host=args.host, port=args.puerto, threaded=True)


if __name__ == "__main__":
    main()
