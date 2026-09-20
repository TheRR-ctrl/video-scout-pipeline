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

import almacen   # leer y escribir los .json de estado
import secretos  # carga secretos.env si las claves no están en el entorno
from titulos import recortar_titulo, limpiar_titulo, largo_youtube

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(BASE_DIR, "web")
CARPETA_ESTADO = os.path.join(BASE_DIR, "pipeline_state")
RUTA_GUION = os.path.join(BASE_DIR, "guion.txt")
RUTA_CONFIG = os.path.join(BASE_DIR, "config.json")
RUTA_METADATA = os.path.join(CARPETA_ESTADO, "metadata.json")
CARPETA_MINIATURAS = os.path.join(CARPETA_ESTADO, "miniaturas")

ES_TERMUX = "PREFIX" in os.environ or os.path.exists("/sdcard")

app = Flask(__name__, static_folder=None)


# =========================================================
# TRABAJOS EN SEGUNDO PLANO
# =========================================================
class Trabajo:
    """Un comando corriendo, con su salida en vivo.

    Solo se ejecuta uno a la vez: renderizar y publicar tocan los mismos
    archivos, y dos a la vez se pisarían. Además un teléfono no da para
    dos ffmpeg simultáneos. Lo que se pide mientras tanto no se rechaza:
    espera en TRABAJO["cola"] y arranca solo cuando este termina.
    """

    def __init__(self, nombre, cmd):
        self.nombre = nombre
        self.cmd = cmd
        self.proc = None
        self.lineas = []
        self.estado = "corriendo"   # corriendo | pausado | ok | error | abortado
        self.luego = None           # acción que se encola sola si este acaba bien
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
        # Encadena con lo que estuviera esperando. Va aquí, en el hilo que
        # lee la salida, porque es el único sitio que se entera de que el
        # proceso acabó: nadie garantiza que el panel esté mirando.
        seguir_con_la_cola(self)

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


# "cola" son los que esperan turno; "hechos", los últimos terminados. Los
# hechos existen porque con una cola el panel deja de estar mirando: si al
# arrancar el siguiente se perdiera el anterior, una tanda de cinco dejaría
# de contar cómo fueron los cuatro primeros.
TRABAJO = {"actual": None, "cola": [], "hechos": []}
TOPE_COLA = 20
HECHOS_QUE_SE_RECUERDAN = 12

# Un candado de verdad y no confiar en el GIL: a la cola la tocan el hilo de
# Flask (cuando pulsas un botón) y el que lee la salida del proceso (cuando
# termina), y entre mirar si hay algo corriendo y arrancar lo siguiente hay
# sitio de sobra para que se crucen y arranquen dos.
_CANDADO = threading.Lock()
_SIGUIENTE_ID = [0]


def _arrancar(nombre, cmd, luego=None):
    """Arranca ya. Quien llama tiene el candado."""
    t = Trabajo(nombre, cmd)
    t.luego = luego
    t.arrancar()
    TRABAJO["actual"] = t
    return t


def lanzar(nombre, cmd, luego=None):
    """Arranca el comando, o lo pone a la cola si hay algo corriendo.

    Devuelve (trabajo, encolado, error): uno de los tres con valor y los
    otros dos en None. "encolado" es la entrada que se quedó esperando, con
    su id, para que el panel pueda quitarla luego.

    "luego" es el nombre de una acción que se encola sola cuando esta termina
    bien. La usa el render para rotar la música: lo que decide qué pistas
    apartar es justo lo que se acaba de renderizar.
    """
    with _CANDADO:
        actual = TRABAJO["actual"]
        if actual and actual.estado in ("corriendo", "pausado"):
            if len(TRABAJO["cola"]) >= TOPE_COLA:
                return None, None, f"Ya hay {TOPE_COLA} esperando; quita alguno antes."
            _SIGUIENTE_ID[0] += 1
            entrada = {"id": _SIGUIENTE_ID[0], "nombre": nombre, "cmd": cmd, "luego": luego}
            TRABAJO["cola"].append(entrada)
            return None, {"id": entrada["id"], "nombre": nombre,
                          "posicion": len(TRABAJO["cola"])}, None
        return _arrancar(nombre, cmd, luego), None, None


def lanzar_tanda(tareas):
    """Encola varias de golpe, conservando el orden.

    Con `lanzar` una por una no basta: si la primera acaba entre llamada y
    llamada, la cola se vacía y la tercera arranca antes de que la segunda
    llegue a entrar. Aquí el candado se sostiene durante toda la tanda, así
    que el orden que se pide es el orden que corre.

    Devuelve (encolados, saltados) con el nombre y la posición de cada una.
    """
    encolados, saltados = [], []
    with _CANDADO:
        for nombre, cmd in tareas:
            actual = TRABAJO["actual"]
            ocupado = (actual and actual.estado in ("corriendo", "pausado")) or encolados
            if not ocupado:
                _arrancar(nombre, cmd)
                encolados.append({"nombre": nombre, "posicion": 0})
                continue
            if len(TRABAJO["cola"]) >= TOPE_COLA:
                saltados.append({"nombre": nombre,
                                 "motivo": f"la cola ya tiene {TOPE_COLA} esperando"})
                continue
            _SIGUIENTE_ID[0] += 1
            TRABAJO["cola"].append({"id": _SIGUIENTE_ID[0], "nombre": nombre,
                                    "cmd": cmd, "luego": None})
            encolados.append({"nombre": nombre, "posicion": len(TRABAJO["cola"])})
    return encolados, saltados


def _toca_encadenar(accion):
    """Si la acción encadenada de verdad tiene algo que hacer.

    Encolarla igualmente no rompería nada (el script se salta solo), pero
    dejaría en el panel un trabajo que no hizo nada después de cada render,
    y eso acaba siendo ruido que se ignora.
    """
    if accion != "musica_rotar":
        return False
    if not os.environ.get("JAMENDO_CLIENT_ID"):
        return False
    try:
        import publisher
        return bool(publisher.cargar_config().get("musica_rotacion_automatica", True))
    except Exception:                              # noqa: BLE001 — informativo
        return False


def seguir_con_la_cola(terminado):
    """Apunta el que acaba de terminar y arranca el siguiente, si lo hay."""
    with _CANDADO:
        # Solo manda el trabajo que de verdad está en curso. Sin esto, un
        # proceso viejo que tarde en morir (el SIGKILL de _rematar llega 4s
        # después de abortar) podría arrancar la cola por segunda vez.
        if TRABAJO["actual"] is not terminado:
            return
        hecho = {"nombre": terminado.nombre, "estado": terminado.estado,
                 "segundos": int(time.time() - terminado.inicio)}
        # De los que fallaron se guarda el final de la salida: es lo que hay
        # que leer para saber por qué, y al arrancar el siguiente deja de
        # estar a la vista.
        if terminado.estado == "error":
            hecho["lineas"] = terminado.como_dict()["lineas"][-20:]
        TRABAJO["hechos"].append(hecho)
        del TRABAJO["hechos"][:-HECHOS_QUE_SE_RECUERDAN]

        # Lo que va detrás de este trabajo, si salió bien. Va al final de la
        # cola y no delante: lo que tú pulsaste manda sobre lo que se encola
        # solo. Se encola aquí dentro, con el candado puesto, porque un
        # instante después ya hay otro proceso corriendo.
        luego = getattr(terminado, "luego", None)
        if luego and terminado.estado == "ok" and _toca_encadenar(luego):
            nombre_luego, cmd_luego = ACCIONES[luego]
            ya_esperando = any(e["cmd"] == cmd_luego for e in TRABAJO["cola"])
            if not ya_esperando and len(TRABAJO["cola"]) < TOPE_COLA:
                _SIGUIENTE_ID[0] += 1
                TRABAJO["cola"].append({"id": _SIGUIENTE_ID[0], "nombre": nombre_luego,
                                        "cmd": cmd_luego, "luego": None})

        if not TRABAJO["cola"]:
            return
        # Sacar de la cola y arrancar van dentro del mismo candado. Si se
        # soltara entre las dos, un botón pulsado en ese hueco vería el
        # trabajo anterior ya terminado, arrancaría el suyo, y acabarían
        # dos procesos a la vez, que es justo lo que no cabe en el teléfono.
        entrada = TRABAJO["cola"].pop(0)
        _arrancar(entrada["nombre"], entrada["cmd"], entrada.get("luego"))


def quitar_de_la_cola(id_entrada):
    with _CANDADO:
        antes = len(TRABAJO["cola"])
        TRABAJO["cola"][:] = [e for e in TRABAJO["cola"] if e["id"] != id_entrada]
        return antes - len(TRABAJO["cola"])


def vaciar_la_cola():
    with _CANDADO:
        cuantos = len(TRABAJO["cola"])
        TRABAJO["cola"].clear()
        return cuantos


def cola_como_lista():
    with _CANDADO:
        return [{"id": e["id"], "nombre": e["nombre"]} for e in TRABAJO["cola"]]


# =========================================================
# LECTURA DE ESTADO
# =========================================================
# El panel solo enseña: un json ilegible es una tarjeta vacía, no un error.
leer_json = almacen.leer
guardar_json = almacen.guardar


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
    # Dos cosas distintas que antes iban juntas. "Subido" es lo que está en
    # YouTube; "visto" es lo que publisher.py ya procesó, y ahí dentro también
    # están los rechazados, que NO se subieron. La diferencia importa al
    # borrar: de uno subido queda copia en YouTube, de uno rechazado no queda
    # ninguna.
    subidas = {p.get("ruta") for p in publicados}
    vistas = subidas | {r.get("ruta") for r in rechazados}
    metadatos = leer_json(RUTA_METADATA, {})
    # La revisión de calidad se lee, no se calcula aquí: medirla es
    # descodificar el video entero y el panel repinta cada segundo y medio.
    revisiones = leer_json(os.path.join(CARPETA_ESTADO, "calidad.json"), {})

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
            "publicado": ruta in vistas,
            "subido": ruta in subidas,
            # Lo que publisher.py subirá tal cual. None mientras no se haya
            # preparado: el panel distingue "todavía no existe" de "existe y
            # dice esto", que no es lo mismo para quien va a aprobarlo.
            "meta": metadatos.get(os.path.basename(ruta)),
            # None = no se ha revisado nunca, que no es lo mismo que
            # revisado y sin defectos.
            "calidad": revisiones.get(os.path.basename(ruta)),
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
        guardar_json(ruta_atrib, atrib)
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


# Lo que el panel puede tocar de un estilo de subtítulos, con su tipo y sus
# límites. Lista blanca por el mismo motivo que AJUSTES_NUMERICOS: lo que
# llegue del navegador acaba en config.json, del que depende todo el render.
# Los topes no son gusto: por debajo de 40 px no se lee en el móvil, por
# encima de 160 no cabe una palabra larga; un borde de más de 20 come la
# letra; y "palabras por frase" es lo que decide si hay frase que resaltar.
def _entero(minimo, maximo):
    def valida(v):
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise ValueError("tiene que ser un número")
        return max(minimo, min(maximo, int(v)))
    return valida


def _color(v):
    if not isinstance(v, str) or not re.fullmatch(r"#[0-9A-Fa-f]{6}", v.strip()):
        raise ValueError("tiene que ser un color tipo #RRGGBB")
    return v.strip().upper()


def _booleano(v):
    if not isinstance(v, bool):
        raise ValueError("tiene que ser sí o no")
    return v


def _paleta(v):
    if not isinstance(v, list):
        raise ValueError("tiene que ser una lista de colores")
    if len(v) > 8:
        raise ValueError("como mucho 8 colores")
    return [_color(c) for c in v]


def _de_la_lista(opciones):
    def valida(v):
        if v not in opciones:
            raise ValueError(f"tiene que ser uno de: {', '.join(opciones)}")
        return v
    return valida


def _fuente(v):
    # Sin lista cerrada: FUENTES_INCLUIDAS es lo que viaja en el repo, pero
    # quien tenga otra instalada puede escribirla. Si no existe, libass
    # sustituye y se ve en el render, no aquí.
    if not isinstance(v, str) or not v.strip() or len(v) > 60:
        raise ValueError("nombre de fuente vacío o demasiado largo")
    return v.strip()


def campos_estilo():
    import generar_video_maestro as gvm
    return {
        "estilo": _de_la_lista(gvm.ESTILOS_SUBTITULOS),
        "fuente": _fuente,
        "palabras_por_frase_short": _entero(1, 8),
        "palabras_por_frase_largo": _entero(1, 10),
        "tamano_short": _entero(40, 160),
        "tamano_largo": _entero(40, 200),
        "color_texto": _color,
        "color_activo": _color,
        "color_borde": _color,
        "grosor_borde": _entero(0, 20),
        "sombra": _entero(0, 10),
        "mayusculas": _booleano,
        "italica": _booleano,
        "escala_activa": _entero(100, 160),
        "colores_resalte": _paleta,
        "resaltar_solo_clave": _booleano,
        "min_letras_resalte": _entero(1, 12),
        "reparto_respaldo": _de_la_lista(gvm.REPARTOS_RESPALDO),
    }


def limpiar_valores_estilo(valores):
    """Devuelve (valores_validados, errores). Lo que no esté en la lista se
    ignora sin ruido; lo que esté pero venga mal se nombra, porque eso sí es
    un fallo del panel que conviene ver."""
    campos = campos_estilo()
    limpios, errores = {}, []
    for clave, valor in (valores or {}).items():
        if clave not in campos:
            continue
        try:
            limpios[clave] = campos[clave](valor)
        except ValueError as exc:
            errores.append(f"{clave}: {exc}")
    return limpios, errores


NOMBRE_ESTILO = re.compile(r"^[a-z0-9_]{1,32}$")


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
    guardar_json(RUTA_METADATA, almacen)
    return jsonify({"ok": True, "meta": almacen[nombre]})


@app.post("/api/borrar/<path:archivo>")
def api_borrar_video(archivo):
    """Borra del teléfono el .mp4 de un video ya renderizado.

    Solo el archivo (y su miniatura). NO se toca resultado_lote.json a
    propósito: ese registro es lo que hace que limpiar_cola.py sepa que esa
    historia ya se grabó. Borrando la anotación, la historia volvería a la
    cola en la siguiente pasada y se renderizaría otra vez, que es justo lo
    contrario de lo que pide quien borra un video.

    Lo mismo con publicados.json: lo que se subió a YouTube se subió, y el
    historial no cambia porque el archivo local ya no esté. Lo que sí se
    marca es _borrado_local, que es como publisher.py anota los que se llevó
    la limpieza de los siete días; sin eso la pestaña Publicados seguiría
    diciendo que el video está en el teléfono hasta que venciera el plazo.
    """
    nombre = os.path.basename(archivo)
    kb, error = borrar_de_salida(nombre)
    if error:
        return jsonify({"error": error}), (404 if kb is None else 500)
    marcar_borrados_localmente([nombre])
    return jsonify({"ok": True, "nombre": nombre, "kb": kb})


@app.post("/api/borrar-subidos")
def api_borrar_subidos():
    """Borra de golpe todos los que ya están en YouTube.

    Solo los subidos de verdad: los rechazados también los ha visto
    publisher.py, pero de esos no hay copia en ningún otro sitio.
    """
    subidos = [v["archivo"] for v in videos_renderizados() if v["subido"]]
    if not subidos:
        return jsonify({"error": "No hay ninguno subido en el teléfono"}), 404

    borrados, kb_total, fallos = [], 0, []
    for nombre in subidos:
        kb, error = borrar_de_salida(nombre)
        if error:
            fallos.append(nombre)
            continue
        borrados.append(nombre)
        kb_total += kb
    marcar_borrados_localmente(borrados)
    return jsonify({"ok": True, "borrados": len(borrados), "kb": kb_total,
                    "fallos": len(fallos)})


def borrar_de_salida(nombre):
    """Borra un .mp4 de la carpeta de salida y su miniatura.

    Devuelve (kb liberados, None) o (None, motivo). No toca ningún registro:
    de eso se encarga marcar_borrados_localmente, que sabe cuáles hay que
    anotar y escribe publicados.json una sola vez.
    """
    carpeta = cfg_actual()["carpeta_salida"]
    ruta = os.path.join(carpeta, nombre)
    if not os.path.isfile(ruta):
        return None, "Ese video ya no está en el teléfono"

    kb = os.path.getsize(ruta) // 1024
    try:
        os.remove(ruta)
    except Exception as exc:
        return 0, f"No se pudo borrar: {exc}"

    # La miniatura se rehace sola con ffmpeg si el video vuelve; dejarla
    # ocupando sitio por un archivo que ya no existe no ayuda a nadie.
    jpg = os.path.join(CARPETA_MINIATURAS, nombre + ".jpg")
    if os.path.exists(jpg):
        try:
            os.remove(jpg)
        except Exception:
            pass
    return kb, None


def marcar_borrados_localmente(nombres):
    """Anota en publicados.json que esos archivos ya no están en el teléfono.

    Es la misma marca que pone publisher.py al hacer la limpieza de los siete
    días. Sin ella, la pestaña Publicados seguiría diciendo que el video se
    puede ver aquí hasta que venciera el plazo.
    """
    if not nombres:
        return
    ruta_pub = os.path.join(CARPETA_ESTADO, "publicados.json")
    publicados = leer_json(ruta_pub, [])
    quedan = set(nombres)
    tocado = False
    for pub in publicados:
        if os.path.basename(pub.get("ruta", "")) in quedan and not pub.get("_borrado_local"):
            pub["_borrado_local"] = True
            tocado = True
    if tocado:
        guardar_json(ruta_pub, publicados)


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
    guardar_json(RUTA_CONFIG, cfg)
    return jsonify({"ok": True, "clave": clave, "valor": valor})


def credenciales():
    out = []
    for clave, tiene, origen in secretos.estado():
        # Opcionales: sin ellas el pipeline entero sigue corriendo. Jamendo
        # solo añade música nueva, y Pexels/Pixabay solo añaden fondos de
        # archivo; el material que ya está en el teléfono no depende de
        # ninguna. Y lo que hayas añadido tú también es opcional por
        # definición: el proyecto de serie no lo usa.
        opcional = (clave in ("JAMENDO_CLIENT_ID", "PEXELS_API_KEY", "PIXABAY_API_KEY")
                    or clave not in secretos.CLAVES_CONOCIDAS)
        out.append({"nombre": clave, "ok": tiene, "origen": origen,
                    "opcional": opcional})
    for archivo in ("client_secret.json", "youtube_token.json"):
        out.append({"nombre": archivo, "ok": os.path.exists(os.path.join(BASE_DIR, archivo)),
                    "origen": "", "opcional": False})
    # Opcional: sin él el pipeline entero sigue funcionando, solo que sin TikTok.
    out.append({"nombre": "tiktok_token.json",
                "ok": os.path.exists(os.path.join(BASE_DIR, "tiktok_token.json")),
                "origen": "", "opcional": True})
    return out


# =========================================================
# API
# =========================================================
def resumen_canal():
    """Cómo le va al canal, sin tocar la red.

    Todo sale de archivos: las vistas de pipeline_state/vistas.json (las dejó
    ahí el último `relanzar.py`), los suscriptores de la caché de formato.py,
    y lo borrado de relanzados.json. Pintar una pestaña no puede depender de
    que haya cobertura, y la API de YouTube tiene cuota diaria: si el panel
    preguntara en cada refresco, un rato con el panel abierto la agotaría.

    Por eso hay un botón de «releer»: el número que se ve es de cuando se
    leyó, y la pestaña dice cuándo fue.
    """
    import relanzar
    import formato

    vistas, cuando = relanzar.vistas_guardadas()
    registros = relanzar.subidos_con_vistas_guardadas()

    repes = relanzar.sobrantes_de_los_repetidos(
        registros, relanzar.MAX_VISTAS_DEFECTO, relanzar.DIAS_MINIMOS_DEFECTO)
    rehacibles = relanzar.sin_vistas(
        registros, relanzar.MAX_VISTAS_DEFECTO, relanzar.DIAS_MINIMOS_DEFECTO,
        relanzar.MAX_INTENTOS_DEFECTO)
    ids_repes = {id(p) for p in repes}
    ids_rehacibles = {id(p) for p in rehacibles}

    def como_fila(p, marca):
        return {
            "titulo": p.get("titulo_youtube") or "(sin título)",
            "video_id": p.get("video_id"),
            "vistas": p.get("vistas"),
            "dias": (lambda d: None if d is None else int(d))(
                relanzar.dias_desde_subida(p)),
            "marca": marca,
        }

    filas = []
    for p in sorted(registros, key=lambda x: -(x.get("vistas") if x.get("vistas") is not None else -1)):
        if id(p) in ids_repes:
            marca = "repetida"
        elif id(p) in ids_rehacibles:
            marca = "rehacible"
        elif p.get("vistas") is None:
            marca = "sin_dato"
        else:
            marca = ""
        filas.append(como_fila(p, marca))

    historial = leer_json(relanzar.RUTA_HISTORIAL, [])
    ultima = historial[-1]["borrado_en"] if historial else None

    try:
        politica = formato.politica()
    except Exception as exc:                       # noqa: BLE001 — informativo
        politica = {"permite_largos": False, "suscriptores": None,
                    "umbral": None, "motivo": f"no se pudo mirar ({exc})"}

    with_vistas = [f["vistas"] for f in filas if f["vistas"] is not None]
    return {
        "cuando": cuando,
        "puede_borrar": relanzar.PERMISO_BORRADO in (relanzar.permisos_del_token() or set()),
        "videos": filas,
        "total": len(filas),
        "vistas_totales": sum(with_vistas),
        "mediana": sorted(with_vistas)[len(with_vistas) // 2] if with_vistas else None,
        "repetidas": len(repes),
        "rehacibles": len(rehacibles),
        "sin_dato": sum(1 for f in filas if f["vistas"] is None),
        "dias_minimos": relanzar.DIAS_MINIMOS_DEFECTO,
        "max_intentos": relanzar.MAX_INTENTOS_DEFECTO,
        "ultima_revision": ultima,
        "borrados_en_total": len(historial),
        "largos": politica,
    }


def tiktok_resumen():
    """Lo que el panel enseña de TikTok: registro, pendientes y días de disco.

    Los días importan mas de lo que parece: publisher.py borra el .mp4 a los 7
    días de subirlo a YouTube, y tiktok_publisher se salta lo que ya no está en
    disco. Con una tanda pequeña, la mitad de la lista puede evaporarse antes
    de que le toque el turno, y eso hay que verlo.
    """
    import tiktok_publisher as tk
    import demo_tiktok

    cfg = tk.cargar_config()
    subidos = leer_json(tk.RUTA_SUBIDOS, [])
    opciones = leer_json(tk.RUTA_OPCIONES, {})
    try:
        pendientes, _ = tk.videos_pendientes()
    except Exception:
        pendientes = []

    ahora = datetime.now(timezone.utc)
    dias_por_ruta = {}
    for p in leer_json(os.path.join(CARPETA_ESTADO, "publicados.json"), []):
        if not p.get("subido_en") or p.get("_borrado_local"):
            continue
        try:
            s = datetime.strptime(p["subido_en"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        dias_por_ruta[p["ruta"]] = max(0, 7 - (ahora - s).days)

    return {
        "activo": cfg["activo"],
        "modo": cfg["modo"],
        "max_por_corrida": cfg["max_por_corrida"],
        "token": os.path.exists(tk.RUTA_TOKEN),
        # El guion del vídeo demo de la auditoría, si hay una grabación en
        # curso. Fuera de esos diez minutos no se enseña nada.
        "demo": demo_tiktok.estado(),
        "subidos": [{
            "nombre": os.path.basename(s["ruta"]),
            "titulo": s.get("pie", ""),
            "estado": s.get("estado", ""),
            "fecha": s.get("fecha", ""),
            "manual": not s.get("publish_id"),
        } for s in reversed(subidos)],
        "pendientes": [{
            "ruta": v["ruta"],
            "nombre": os.path.basename(v["ruta"]),
            "titulo": m.get("titulo_youtube") or os.path.basename(v["ruta"]),
            # El pie ya montado (titulo + hashtags, recortado al limite de
            # TikTok), para que el boton de copiar del panel pegue exactamente
            # lo mismo que pegaria la API. Montarlo en el navegador seria
            # repetir la regla de recorte en otro idioma.
            "pie": tk.construir_pie(m),
            "dias": dias_por_ruta.get(v["ruta"]),
            "opciones": opciones.get(os.path.basename(v["ruta"])),
        } for v, m in pendientes],
    }


@app.get("/api/estado")
def api_estado():
    import generar_video_maestro as gvm
    cfg = cfg_actual()
    publicados = leer_json(os.path.join(CARPETA_ESTADO, "publicados.json"), [])

    ahora = datetime.now(timezone.utc)
    # Las vistas de la última lectura, para enseñarlas junto a cada subida.
    # Un video que no esté en la caché sale sin número, no con cero: son
    # cosas distintas y confundirlas haría pensar que un video fracasó.
    import relanzar
    vistas_guardadas, _ = relanzar.vistas_guardadas()
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
            "vistas": vistas_guardadas.get(p.get("video_id")),
        })

    return jsonify({
        "historias": historias_del_guion(),
        "videos": videos_renderizados(),
        "publicados": pubs,
        "material": material(),
        "credenciales": credenciales(),
        "subtitulos": cfg["subtitulos"],
        "presets": list(gvm.PRESETS_SUBTITULOS.keys()),
        # Los estilos propios y con qué se arma el formulario del panel. Las
        # opciones salen de las constantes del motor y no repetidas en el
        # JavaScript: dos listas de estilos válidos acabarían divergiendo.
        "presets_propios": {n: v for n, v in (cfg.get("presets_propios") or {}).items()
                            if isinstance(v, dict)},
        "previsualizaciones": previsualizaciones(),
        "estilo_opciones": {
            "estilos": list(gvm.ESTILOS_SUBTITULOS),
            "repartos": list(gvm.REPARTOS_RESPALDO),
            "fuentes": list(gvm.FUENTES_INCLUIDAS),
        },
        "solo_wifi": cfg.get("solo_wifi", True),
        "musica_auto": cfg.get("musica_rotacion_automatica", True),
        "musica_hay_clave": bool(os.environ.get("JAMENDO_CLIENT_ID")),
        "musica": pistas_musica(),
        "fondos": sorted(
            os.path.basename(f) for p in ("fondo_vertical*", "fondo_horizontal*", "fondo_gameplay*")
            for f in glob.glob(os.path.join(BASE_DIR, p))
            if f.lower().endswith((".mp4", ".webm", ".mkv", ".mov"))
        ),
        "ajustes": {k: cfg.get(k) for k in AJUSTES_NUMERICOS},
        "tiktok": tiktok_resumen(),
        "canal": resumen_canal(),
        "trabajo": TRABAJO["actual"].como_dict() if TRABAJO["actual"] else None,
        "cola": cola_como_lista(),
        "hechos": list(TRABAJO["hechos"]),
    })


# Lo que un botón puede ejecutar. Es una lista blanca a propósito: el
# navegador manda un nombre, nunca un comando — así una pestaña abierta por
# error no puede pedir la ejecución de algo arbitrario.
ACCIONES = {
    "musica":     ("Actualizando música", [sys.executable, "actualizar_musica.py"]),
    # Rellenar solo trae lo que falta para llegar a 3 por emoción; rotar
    # cambia las que ya sonaron. Esta es la que corre sola tras cada render.
    "musica_rotar": ("Rotando la música ya usada", [sys.executable, "actualizar_musica.py", "--rotar"]),
    "fondos":     ("Enlazando material", [sys.executable, "vincular_fondos.py"]),
    "ver_fondos_pexels": ("Mirando qué hay en Pexels", [sys.executable, "descargar_fondos.py", "--ver"]),
    "bajar_fondos_pexels": ("Bajando fondos de Pexels", [sys.executable, "descargar_fondos.py"]),
    "buscar":     ("Buscando historias", [sys.executable, "trend_scout.py"]),
    # Explica un escaneo que no trajo nada: cuántos posts se leyeron y por qué
    # se descartó cada uno (ya usados, sin texto, muy cortos/largos).
    "diagnostico_busqueda": ("Revisando la búsqueda", [sys.executable, "trend_scout.py", "--diagnostico"]),
    "buscar_youtube": ("Buscando historias en YouTube", [sys.executable, "youtube_scout.py"]),
    "diagnostico_youtube": ("Revisando la búsqueda en YouTube", [sys.executable, "youtube_scout.py", "--diagnostico"]),
    "probar_clave_youtube": ("Probando la clave de YouTube", [sys.executable, "youtube_scout.py", "--probar-clave"]),
    "guiones":    ("Escribiendo guiones", [sys.executable, "script_writer.py"]),
    "publicar":   ("Publicando en YouTube", [sys.executable, "publisher.py"]),
    "publicar_datos": ("Publicando (datos móviles)", [sys.executable, "publisher.py", "--con-datos"]),
    "previsualizar": ("Generando comparación de estilos", [sys.executable, "previsualizar_estilos.py"]),
    "metadata":   ("Preparando títulos y hashtags", [sys.executable, "preparar_metadata.py"]),
    "calidad":    ("Revisando los videos renderizados", [sys.executable, "calidad.py"]),
    "calidad_todos": ("Revisando otra vez todos los videos", [sys.executable, "calidad.py", "--todos"]),
    "calidad_ia": ("Preguntándole a Gemini qué falla", [sys.executable, "calidad_ia.py"]),
    "calidad_ia_video": ("Gemini, subiendo el video entero", [sys.executable, "calidad_ia.py", "--video-entero"]),
    "tiktok":     ("Subiendo a TikTok", [sys.executable, "tiktok_publisher.py"]),
    "tiktok_datos": ("Subiendo a TikTok (datos móviles)", [sys.executable, "tiktok_publisher.py", "--con-datos"]),
    "tiktok_revisar": ("Consultando estados en TikTok", [sys.executable, "tiktok_publisher.py", "--revisar"]),
    "demo_comprobar": ("Comprobando que la toma va a salir", [sys.executable, "demo_tiktok.py", "--comprobar"]),
    "demo_empezar": ("Empezando el guion de la grabación", [sys.executable, "demo_tiktok.py", "--empezar"]),

    # Mantenimiento. Son las órdenes que si no habría que escribir a mano en
    # Termux, y de las que uno no se acuerda cuando hacen falta. Las que
    # cambian algo van en pareja: primero la que solo enseña qué haría.
    "estado_cola": ("Mirando la cola", [sys.executable, "trend_scout.py", "--estado"]),
    "probar_clave_gemini": ("Probando la clave de Gemini", [sys.executable, "script_writer.py", "--probar-clave"]),
    "ver_limpiar_cola": ("Viendo qué sobra en la cola", [sys.executable, "limpiar_cola.py"]),
    "limpiar_cola": ("Quitando de la cola lo ya grabado", [sys.executable, "limpiar_cola.py", "--si"]),
    "ver_recomprimir": ("Buscando videos que pesan de más", [sys.executable, "recomprimir.py"]),
    "recomprimir": ("Recomprimiendo videos", [sys.executable, "recomprimir.py", "--si"]),
    "recomprimir_limpiar": ("Borrando temporales de recompresión", [sys.executable, "recomprimir.py", "--limpiar"]),
    "formato": ("Mirando si toca hacer largos", [sys.executable, "formato.py"]),
    "ver_relanzar": ("Leyendo las vistas del canal", [sys.executable, "relanzar.py"]),
    "ver_relanzar_dup": ("Buscando copias repetidas sin vistas", [sys.executable, "relanzar.py", "--duplicados"]),
    "relanzar_dup": ("Borrando las copias repetidas", [sys.executable, "relanzar.py", "--duplicados", "--si"]),
    "ver_relanzar_sin": ("Buscando videos que no vio nadie", [sys.executable, "relanzar.py", "--sin-vistas"]),
    "relanzar_sin": ("Borrando y devolviendo a la cola", [sys.executable, "relanzar.py", "--sin-vistas", "--si"]),
    "vistas": ("Releyendo las vistas del canal", [sys.executable, "relanzar.py", "--refrescar-vistas"]),
    "ver_revision": ("Revisión del canal (solo mirar)", ["bash", "revision_quincenal.sh", "--ver"]),
    "revision": ("Revisión del canal: borrar y rehacer", ["bash", "revision_quincenal.sh"]),
    "tiktok_estado": ("Estado de TikTok", [sys.executable, "tiktok_publisher.py", "--estado"]),
    "tiktok_simular": ("Simulando la subida a TikTok", [sys.executable, "tiktok_publisher.py", "--simular"]),
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
        t, encolado, err = lanzar("Rehaciendo" if d.get("rehacer") else "Renderizando",
                                  cmd, luego="musica_rotar")
    elif accion == "regenerar_metadata":
        # Volver a preguntarle a Gemini por UN video. --forzar porque el
        # botón solo aparece cuando ya la estás mirando: pedirlo ahí es
        # pedirlo a sabiendas de que reemplaza lo que hay.
        d = request.json or {}
        cmd = [sys.executable, "preparar_metadata.py", "--rehacer", "--forzar"]
        if d.get("numero") is not None:
            cmd += ["--solo", str(d["numero"])]
        t, encolado, err = lanzar("Regenerando con Gemini", cmd)
    elif accion in ACCIONES:
        nombre, cmd = ACCIONES[accion]
        t, encolado, err = lanzar(nombre, cmd)
    else:
        return jsonify({"error": "Acción desconocida"}), 400

    if err:
        return jsonify({"error": err}), 409
    if encolado:
        return jsonify({"ok": True, "encolado": encolado})
    return jsonify({"ok": True, "trabajo": t.como_dict()})


# La tanda de mantenimiento: lo que hay que hacer de vez en cuando y que, por
# separado, se olvida. Van en este orden a propósito — medir antes de tocar
# nada, limpiar la cola después (usa lo medido para saber qué está grabado) y
# rotar la música al final, que es lo único que baja megas.
TANDA_MANTENIMIENTO = ["calidad", "limpiar_cola", "musica_rotar"]


@app.post("/api/mantenimiento")
def api_mantenimiento():
    """Encola de una vez las tareas de mantenimiento.

    No es un comando nuevo: son los mismos de la lista blanca, puestos en la
    cola uno detrás de otro. Así se ven pasar en el panel de siempre y se
    puede quitar cualquiera a mitad, en vez de ser una caja negra que tarda
    diez minutos.
    """
    tareas, saltados = [], []
    for accion in TANDA_MANTENIMIENTO:
        if accion == "musica_rotar" and not _toca_encadenar("musica_rotar"):
            saltados.append({"accion": accion, "motivo":
                             "sin JAMENDO_CLIENT_ID o con la rotación apagada"})
            continue
        nombre, cmd = ACCIONES[accion]
        tareas.append((nombre, cmd))

    encolados, no_cupieron = lanzar_tanda(tareas)
    por_nombre = {ACCIONES[a][0]: a for a in TANDA_MANTENIMIENTO}
    for e in encolados:
        e["accion"] = por_nombre.get(e["nombre"], "")
    for x in no_cupieron:
        saltados.append({"accion": por_nombre.get(x["nombre"], ""), "motivo": x["motivo"]})
    return jsonify({"ok": True, "encolados": encolados, "saltados": saltados})


@app.post("/api/cola/<que>")
def api_cola(que):
    """Quita una entrada de la cola de espera, o la vacía entera.

    No toca lo que ya está corriendo: para eso está /api/trabajo/abortar.
    """
    if que == "vaciar":
        return jsonify({"ok": True, "quitados": vaciar_la_cola()})
    if que == "quitar":
        id_entrada = (request.json or {}).get("id")
        if not isinstance(id_entrada, int):
            return jsonify({"error": "Falta el id"}), 400
        if not quitar_de_la_cola(id_entrada):
            # Lo normal no es un error: se ha puesto a correr mientras
            # mirabas la lista. El panel se entera al refrescar.
            return jsonify({"ok": True, "quitados": 0})
        return jsonify({"ok": True, "quitados": 1})
    return jsonify({"error": "Acción desconocida"}), 400


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
    cfg = leer_json(RUTA_CONFIG, {})
    propios = cfg.get("presets_propios") or {}
    if nombre not in gvm.PRESETS_SUBTITULOS and nombre not in propios:
        return jsonify({"error": "Preset desconocido"}), 400

    subs = dict(cfg.get("subtitulos") or {})
    subs["preset"] = nombre
    cfg["subtitulos"] = subs
    guardar_json(RUTA_CONFIG, cfg)
    return jsonify({"ok": True, "preset": nombre})


@app.post("/api/estilo/guardar")
def api_estilo_guardar():
    """Guarda un estilo propio en config.json y lo deja activo.

    Los del repo no se tocan: un estilo propio que se llame igual que uno de
    ellos se rechaza, porque resolver_subtitulos da preferencia al del repo y
    el panel enseñaría un estilo que el render no usa.
    """
    import generar_video_maestro as gvm
    d = request.json or {}
    nombre = str(d.get("nombre", "")).strip().lower().replace(" ", "_")
    if not NOMBRE_ESTILO.fullmatch(nombre):
        return jsonify({"error": "El nombre va en minúsculas, sin acentos, hasta 32 letras."}), 400
    if nombre in gvm.PRESETS_SUBTITULOS:
        return jsonify({"error": f"«{nombre}» es uno de los que trae el repo. Ponle otro nombre."}), 400

    valores, errores = limpiar_valores_estilo(d.get("valores"))
    if errores:
        return jsonify({"error": "; ".join(errores)}), 400
    if not valores:
        return jsonify({"error": "No llegó ningún ajuste que guardar."}), 400

    cfg = leer_json(RUTA_CONFIG, {})
    propios = dict(cfg.get("presets_propios") or {})
    propios[nombre] = valores
    cfg["presets_propios"] = propios
    if d.get("activar", True):
        subs = dict(cfg.get("subtitulos") or {})
        subs["preset"] = nombre
        cfg["subtitulos"] = subs
    guardar_json(RUTA_CONFIG, cfg)
    return jsonify({"ok": True, "nombre": nombre, "activo": bool(d.get("activar", True))})


@app.post("/api/estilo/borrar")
def api_estilo_borrar():
    """Quita un estilo propio. Si era el activo, vuelve al predeterminado:
    dejarlo apuntando a un preset que ya no existe haría que cada render
    avisara y se cayera al predeterminado igual, pero sin decirlo aquí."""
    nombre = str((request.json or {}).get("nombre", ""))
    cfg = leer_json(RUTA_CONFIG, {})
    propios = dict(cfg.get("presets_propios") or {})
    if nombre not in propios:
        return jsonify({"error": "Ese estilo no está guardado."}), 404

    del propios[nombre]
    cfg["presets_propios"] = propios
    subs = dict(cfg.get("subtitulos") or {})
    volvio = False
    if subs.get("preset") == nombre:
        subs["preset"] = "predeterminado"
        cfg["subtitulos"] = subs
        volvio = True
    guardar_json(RUTA_CONFIG, cfg)
    return jsonify({"ok": True, "nombre": nombre, "volvio_al_predeterminado": volvio})


@app.post("/api/wifi")
def api_wifi():
    cfg = leer_json(RUTA_CONFIG, {})
    cfg["solo_wifi"] = bool((request.json or {}).get("solo_wifi", True))
    guardar_json(RUTA_CONFIG, cfg)
    return jsonify({"ok": True, "solo_wifi": cfg["solo_wifi"]})


@app.post("/api/musica/auto")
def api_musica_auto():
    """Enciende o apaga la rotación automática de música tras cada render."""
    cfg = leer_json(RUTA_CONFIG, {})
    cfg["musica_rotacion_automatica"] = bool((request.json or {}).get("auto", True))
    guardar_json(RUTA_CONFIG, cfg)
    return jsonify({"ok": True, "auto": cfg["musica_rotacion_automatica"]})


@app.post("/api/tiktok/marcar")
def api_tiktok_marcar():
    """Da por subidos los videos que el usuario ya publicó a mano en TikTok."""
    import tiktok_publisher as tk

    rutas = (request.json or {}).get("rutas") or []
    if not isinstance(rutas, list):
        return jsonify({"ok": False, "error": "rutas debe ser una lista"}), 400

    # El navegador manda rutas; solo se aceptan las que ahora mismo estan
    # pendientes. Asi una peticion inventada no puede escribir nada raro en el
    # registro.
    pendientes, _ = tk.videos_pendientes()
    validas = {v["ruta"]: (m.get("titulo_youtube") or "") for v, m in pendientes}
    pares = [(r, validas[r]) for r in rutas if r in validas]
    if not pares:
        return jsonify({"ok": False, "error": "ninguna de esas rutas está pendiente"}), 400

    return jsonify({"ok": True, "marcados": tk.marcar_rutas(pares)})


@app.get("/api/tiktok/creador")
def api_tiktok_creador():
    """Lo que TikTok permite hoy a esta cuenta, para pintar la pantalla.

    No va dentro de /api/estado a proposito: eso se pide cada pocos segundos y
    esto es una llamada a la API de TikTok. Se pide al abrir el formulario.
    """
    import tiktok_publisher as tk
    try:
        token, _ = tk.token_valido()
        creador = tk.consultar_creador(token)
        import demo_tiktok
        demo_tiktok.anotar("creador")   # segunda escena del demo, si lo estás grabando
        return jsonify({"ok": True, "creador": creador})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 502


@app.post("/api/tiktok/opciones/<path:archivo>")
def api_tiktok_opciones(archivo):
    """Guarda lo que la persona eligió para un video: privacidad y permisos."""
    import tiktok_publisher as tk

    nombre = os.path.basename(archivo)
    d = request.json or {}
    privacidad = d.get("privacidad")
    # Sin privacidad no hay publicacion: TikTok exige que la marque una
    # persona y prohibe que la app ponga una por defecto.
    if privacidad not in ("PUBLIC_TO_EVERYONE", "MUTUAL_FOLLOW_FRIENDS",
                          "FOLLOWER_OF_CREATOR", "SELF_ONLY"):
        return jsonify({"ok": False, "error": "Falta elegir la privacidad"}), 400

    comercial = d.get("comercial") or {}
    activo = bool(comercial.get("activo"))
    marca = bool(comercial.get("marca_propia"))
    patro = bool(comercial.get("patrocinado"))
    if activo and not (marca or patro):
        return jsonify({"ok": False,
                        "error": "Si declaras contenido comercial, marca al menos una casilla"}), 400
    # Regla de TikTok: el contenido de marca no puede quedar en privado.
    if activo and patro and privacidad == "SELF_ONLY":
        return jsonify({"ok": False,
                        "error": "El contenido patrocinado no puede ser privado"}), 400

    todas = leer_json(tk.RUTA_OPCIONES, {})
    todas[nombre] = {
        "privacidad": privacidad,
        "comentarios": bool(d.get("comentarios", True)),
        "dueto": bool(d.get("dueto", True)),
        "stitch": bool(d.get("stitch", True)),
        "comercial": {"activo": activo, "marca_propia": marca, "patrocinado": patro},
        "elegido_en": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    guardar_json(tk.RUTA_OPCIONES, todas)
    import demo_tiktok
    demo_tiktok.anotar("eleccion")   # tercera escena del demo
    return jsonify({"ok": True, "opciones": todas[nombre]})


@app.post("/api/tiktok/activo")
def api_tiktok_activo():
    cfg = leer_json(RUTA_CONFIG, {})
    seccion = dict(cfg.get("tiktok") or {})
    seccion["activo"] = bool((request.json or {}).get("activo"))
    seccion.setdefault("modo", "borrador")
    seccion.setdefault("max_por_corrida", 1)
    cfg["tiktok"] = seccion
    guardar_json(RUTA_CONFIG, cfg)
    return jsonify({"ok": True, "activo": seccion["activo"]})


# Lo que se puede copiar para pegarlo en los Secrets de GitHub Actions.
# Nombre en GitHub -> de dónde sale el valor.
SECRETOS_COPIABLES = {
    "YOUTUBE_TOKEN": ("archivo", "youtube_token.json"),
    "YOUTUBE_CLIENT_SECRET": ("archivo", "client_secret.json"),
    "GEMINI_API_KEY": ("entorno", "GEMINI_API_KEY"),
    "JAMENDO_CLIENT_ID": ("entorno", "JAMENDO_CLIENT_ID"),
    "YOUTUBE_API_KEY": ("entorno", "YOUTUBE_API_KEY"),
    "PEXELS_API_KEY": ("entorno", "PEXELS_API_KEY"),
    "PIXABAY_API_KEY": ("entorno", "PIXABAY_API_KEY"),
}


def copiables():
    """Los de arriba más los que se hayan añadido desde el panel.

    Se calcula cada vez y no una sola al arrancar: una clave añadida en la
    pestaña Ajustes tiene que poder copiarse a los secrets de GitHub sin
    reiniciar el servidor."""
    todos = dict(SECRETOS_COPIABLES)
    for clave in secretos.claves_extra():
        todos.setdefault(clave, ("entorno", clave))
    return todos


@app.get("/api/secretos")
def api_secretos():
    """Qué hay disponible para copiar, SIN los valores.

    Los valores se piden aparte y de uno en uno: así el contenido de un
    token no viaja en cada refresco del panel ni queda en el historial de
    peticiones solo por tener la pestaña abierta.
    """
    out = []
    for nombre, (tipo, ref) in copiables().items():
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
    # Contra la lista, nunca contra os.environ directamente: si no, pedir
    # /api/secretos/PATH devolvería cualquier variable del entorno.
    disponibles = copiables()
    if nombre not in disponibles:
        return jsonify({"error": "Secreto desconocido"}), 404
    tipo, ref = disponibles[nombre]

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

    Admite un nombre que no esté en SECRETOS_COPIABLES: así se puede añadir
    la API de un servicio nuevo sin tocar código ni abrir Termux. El nombre
    lo valida secretos.guardar, que es quien sabe qué rompe el archivo.
    """
    conocido = copiables().get(nombre)
    if conocido and conocido[0] != "entorno":
        return jsonify({"error": "Los archivos de credenciales no se editan aquí"}), 400
    ref = conocido[1] if conocido else nombre

    valor = (request.json or {}).get("valor", "")
    try:
        # secretos.guardar escribe con tmp + os.replace y borra las líneas
        # repetidas de la misma clave; hacerlo aquí a mano era duplicarlo peor.
        secretos.guardar(ref, valor)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except OSError as exc:
        return jsonify({"error": f"No se pudo escribir secretos.env: {exc}"}), 500
    return jsonify({"ok": True, "nombre": nombre})


# La subcarpeta donde previsualizar_estilos.py deja las muestras. El panel
# necesita su propia ruta: /video/ y /miniatura/ hacen basename() y solo
# alcanzan la raíz de la carpeta de salida, así que estas quedaban invisibles
# desde el teléfono aunque el trabajo dijera OK.
CARPETA_PREVIS = "previsualizacion_estilos"


def previsualizaciones():
    """Las muestras de estilo que hay en disco, la hoja de contactos primero."""
    carpeta = os.path.join(cfg_actual()["carpeta_salida"], CARPETA_PREVIS)
    if not os.path.isdir(carpeta):
        return []
    hay = sorted(f for f in os.listdir(carpeta) if f.lower().endswith(".png"))
    return sorted(hay, key=lambda f: (not f.startswith("_comparacion"), f))


@app.get("/previsualizacion/<path:archivo>")
def api_previsualizacion(archivo):
    """Sirve un PNG de la comparación de estilos.

    basename() y la comprobación de extensión van juntas a propósito: es una
    carpeta cuyo nombre sale de la config, y sin las dos una petición con
    ../.. leería cualquier archivo del teléfono.
    """
    nombre = os.path.basename(archivo)
    if not nombre.lower().endswith(".png"):
        abort(404)
    ruta = os.path.join(cfg_actual()["carpeta_salida"], CARPETA_PREVIS, nombre)
    if not os.path.isfile(ruta):
        abort(404)
    return send_file(ruta, mimetype="image/png", conditional=True)


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
        print("  Se apaga solo si cierras el panel (y no hay nada corriendo).")

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
