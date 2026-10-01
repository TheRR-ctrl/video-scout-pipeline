"""
Un solo archivo con todo lo que git no trae, para reinstalar el teléfono
(o cambiar de teléfono) sin perder nada.

    python respaldo.py                 # claves, estado, guiones, material y
                                       # los videos que aún no se subieron
    python respaldo.py --sin-videos    # igual, pero sin ningún video grabado
    python respaldo.py --todos-los-videos
    python respaldo.py --solo-ajustes  # para pasar al PC: claves, config,
                                       # estado y guiones; sin videos, sin
                                       # fondos ni música (pesa unos KB)

Deja el archivo en Descargas (/sdcard/Download/video-scout-respaldo-FECHA.tar)
para sacarlo del teléfono: Google Drive, un PC o una tarjeta SD. Un reinicio
de fábrica borra también Descargas, así que tiene que salir del teléfono.
Para volver a ponerlo todo: bash restaurar.sh (ver allí).

Qué entra: todo lo que hay en la carpeta del proyecto y git no sigue —
secretos.env, los tokens de YouTube y TikTok, client_secret.json,
config*.json, guion*.txt, pipeline_state/ (lo subido, la metadata, los
tramos excluidos, el horario…), los fondos, la música y la plantilla—, y de
la carpeta de salida resultado_lote.json y los videos. Se saca de git en vez
de ir con una lista: así un archivo nuevo que el proyecto empiece a usar
entra solo, sin acordarse de añadirlo aquí.

Qué no entra: lo que se rehace solo (__pycache__, logs, copias .bak, la
caché de HyperFrames, lo que compila Android).

OJO: lleva las claves. Guárdalo donde solo lo veas tú (tu Drive, sin
compartir). Sin claves, al restaurar habría que volver a sacarlas todas.

Va sin comprimir a propósito: casi todo el peso son videos .mp4, que no se
comprimen más, y comprimir en el teléfono tarda.
"""
import os
import sys
import io
import json
import tarfile
import argparse
import subprocess
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PREFIJO = "video-scout-respaldo-"
RUTA_ULTIMO = os.path.join(BASE_DIR, "pipeline_state", "ultimo_respaldo.json")

# Lo que no vale la pena guardar: se regenera solo o es basura.
# .importando es donde el panel deja el respaldo que le subes al restaurar:
# meterlo dentro del siguiente respaldo sería guardar un respaldo en otro.
FUERA_DIRS = ("__pycache__", ".gradle", "build", "hyperframes_cache", ".git",
              ".importando", ".restaurando")
# Lo que pesa de verdad y no es un ajuste: con --solo-ajustes se queda fuera.
# El material se vuelve a bajar en el otro aparato (Pexels, Jamendo) o se
# copia aparte si es propio.
PESADOS = (".mp4", ".webm", ".mkv", ".mov", ".mp3", ".m4a", ".wav", ".aac", ".ogg")
FUERA_SUFIJOS = (".pyc", ".log", ".parcial", ".tmp")
FUERA_CONTIENE = (".bak",)
# La marca de «este aparato está desactivado» (dispositivo.py) es de este
# aparato, no de los ajustes: al restaurar en otro lo dejaría desactivado.
FUERA_NOMBRES = ("desactivado.json", "pipeline_state/ultimo_respaldo.json",
                 "pipeline_state/fondos_enlazados.json")

# Lo que no puede faltar: si no está, se avisa (no es un fallo).
IMPORTANTES = {
    "secretos.env": "las claves (Gemini, Jamendo, Pexels…)",
    "youtube_token.json": "la sesión de YouTube",
    "client_secret.json": "el permiso de la app de YouTube",
    "pipeline_state/publicados.json": "qué se subió ya (sin esto se volvería a subir todo)",
    "guion.txt": "la cola de historias",
}


def _cargar_json(ruta, defecto):
    try:
        with open(ruta, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return defecto


def archivos_del_proyecto():
    """Los archivos de la carpeta que git no sigue, sin la basura."""
    try:
        salida = subprocess.run(["git", "ls-files", "--others", "-z"], cwd=BASE_DIR,
                                capture_output=True, check=True).stdout
        rutas = [r for r in salida.decode("utf-8", "replace").split("\0") if r]
    except (OSError, subprocess.CalledProcessError):
        # Sin git (una copia bajada en zip): todo lo que haya, menos el código.
        rutas = []
        for raiz, dirs, archivos in os.walk(BASE_DIR):
            dirs[:] = [d for d in dirs if d not in FUERA_DIRS]
            for a in archivos:
                if not a.endswith((".py", ".md", ".sh")):
                    rutas.append(os.path.relpath(os.path.join(raiz, a), BASE_DIR))
    quedan = []
    for r in rutas:
        partes = r.split("/")
        if any(p in FUERA_DIRS for p in partes[:-1]):
            continue
        if (r.endswith(FUERA_SUFIJOS) or any(c in partes[-1] for c in FUERA_CONTIENE)
                or r in FUERA_NOMBRES or partes[-1].startswith(PREFIJO)):
            continue
        if os.path.isfile(os.path.join(BASE_DIR, r)):
            quedan.append(r)
    return sorted(quedan)


def carpeta_salida():
    cfg = _cargar_json(os.path.join(BASE_DIR, "config.json"), {})
    if cfg.get("carpeta_salida"):
        return cfg["carpeta_salida"]
    try:
        import publisher
        return publisher.cargar_config()["carpeta_salida"]
    except Exception:                              # noqa: BLE001 — sin salida, sin videos
        return None


def videos_a_guardar(salida, cuales):
    """Los .mp4 de la salida que entran: ninguno, los que aún no se subieron
    a YouTube, o todos."""
    if cuales == "ninguno" or not salida or not os.path.isdir(salida):
        return []
    todos = sorted(os.path.join(salida, f) for f in os.listdir(salida) if f.endswith(".mp4"))
    if cuales == "todos":
        return todos
    estado = os.path.join(BASE_DIR, "pipeline_state")
    ya = {p.get("ruta") for p in _cargar_json(os.path.join(estado, "publicados.json"), [])}
    ya |= {r.get("ruta") for r in _cargar_json(os.path.join(estado, "rechazados.json"), [])}
    ya_nombres = {os.path.basename(r) for r in ya if r}
    return [v for v in todos if os.path.basename(v) not in ya_nombres]


def destino_por_omision():
    for d in ("/sdcard/Download", os.path.expanduser("~/storage/downloads"),
              os.path.join(os.path.expanduser("~"), "Downloads")):
        if os.path.isdir(d) and os.access(d, os.W_OK):
            return d
    return BASE_DIR


def _mb(n):
    return f"{n / (1024 * 1024):,.0f} MB"


# ---------------------------------------------------------
# Restaurar (el camino de Windows y del PC; en Termux, restaurar.sh)
# ---------------------------------------------------------
def buscar_respaldo():
    """El respaldo más reciente de Descargas, o None."""
    import glob
    candidatos = []
    for d in ("/sdcard/Download", os.path.expanduser("~/storage/downloads"),
              os.path.join(os.path.expanduser("~"), "Downloads"),
              os.path.join(os.path.expanduser("~"), "Descargas"), BASE_DIR):
        candidatos += glob.glob(os.path.join(d, PREFIJO + "*.tar"))
    return max(candidatos, key=os.path.getmtime) if candidatos else None


def _cambiar_rutas(valor, vieja, nueva):
    """Cambia el principio de las rutas que empiecen por `vieja` (la carpeta
    de salida del teléfono) por `nueva`, en cualquier parte de un JSON."""
    if isinstance(valor, str) and vieja and valor.startswith(vieja):
        resto = valor[len(vieja):].lstrip("/\\")
        return os.path.join(nueva, *resto.replace("\\", "/").split("/")) if resto else nueva
    if isinstance(valor, list):
        return [_cambiar_rutas(v, vieja, nueva) for v in valor]
    if isinstance(valor, dict):
        return {k: _cambiar_rutas(v, vieja, nueva) for k, v in valor.items()}
    return valor


def _salida_de_aqui(vieja):
    """Dónde van los videos en esta máquina. La del respaldo si se puede
    usar aquí; si era del teléfono (/sdcard… o /storage/emulated/…, que es
    la misma memoria con otro nombre) y esto no es un teléfono, la de
    siempre en este sistema (Escritorio/Videos Creados). En Windows una ruta
    del teléfono no da error: se crea en C:\\storage\\… sin avisar."""
    del_telefono = vieja and vieja.startswith(("/sdcard", "/storage/"))
    if vieja and not (del_telefono and not os.path.isdir("/sdcard")):
        return vieja
    return os.path.join(os.path.expanduser("~"), "Desktop", "Videos Creados")


def es_respaldo(archivo):
    """¿Es un .tar hecho por respaldo.py? Mira dentro, no el nombre: el
    navegador o WhatsApp pueden haberlo renombrado."""
    try:
        with tarfile.open(archivo) as tar:
            nombres = tar.getnames()
    except (OSError, tarfile.TarError):
        return False
    return "respaldo.json" in nombres or any(n.startswith("proyecto/") for n in nombres)


def _ruta_segura(nombre):
    partes = nombre.replace("\\", "/").split("/")
    return not (nombre.startswith(("/", "\\")) or ":" in partes[0] or ".." in partes)


def restaurar(archivo=None, borrar=False):
    import shutil
    import almacen

    archivo = archivo or buscar_respaldo()
    if not archivo or not os.path.isfile(archivo):
        print("\n  No encontré ningún video-scout-respaldo-*.tar en Descargas.")
        print("  Bájalo de donde lo guardaste (Google Drive…) a Descargas y repite,")
        print("  o dame la ruta:  python respaldo.py --restaurar RUTA\\AL\\ARCHIVO.tar\n")
        return 1
    if not es_respaldo(archivo):
        print(f"\n  {os.path.basename(archivo)} no es un respaldo de Video Scout (o está cortado).")
        print("  Tiene que ser el video-scout-respaldo-FECHA.tar que deja «Respaldo» en el teléfono.\n")
        return 1
    print(f"\n  Restaurando {os.path.basename(archivo)}…")

    tmp = os.path.join(BASE_DIR, ".restaurando")
    shutil.rmtree(tmp, ignore_errors=True)
    enlaces = []

    def solo_archivos(miembro, destino=None):
        # Los enlaces se saltan: en el teléfono «Enlazar material» deja la
        # plantilla, la música y los fondos como enlaces a Descargas, y un
        # respaldo viejo los lleva así. Apuntan a rutas del teléfono que
        # aquí no existen, y con filter="data" uno solo tumbaba la
        # restauración entera (LinkOutsideDestinationError).
        if miembro.issym() or miembro.islnk():
            enlaces.append(miembro.name)
            return None
        if destino is None:                       # Python < 3.12: sin data_filter
            return miembro if _ruta_segura(miembro.name) else None
        return tarfile.data_filter(miembro, destino)

    with tarfile.open(archivo) as tar:
        # data_filter: no deja escribir fuera de la carpeta ni permisos raros.
        if hasattr(tarfile, "data_filter"):
            tar.extractall(tmp, filter=solo_archivos)
        else:
            tar.extractall(tmp, members=[m for m in tar.getmembers() if solo_archivos(m)])
    if enlaces:
        print(f"   ○ {len(enlaces)} enlace(s) del teléfono no se restauran (apuntan a sus carpetas):")
        for e in enlaces[:6]:
            print(f"       {e.removeprefix('proyecto/')}")
        print("     Es el material (fondos, música, plantilla): ponlo en la carpeta de material")
        print("     y pulsa «Re-enlazar material» en Ajustes → Música y video.")

    proyecto = os.path.join(tmp, "proyecto")
    if os.path.isdir(proyecto):
        shutil.copytree(proyecto, BASE_DIR, dirs_exist_ok=True)
        n = sum(len(f) for _, _, f in os.walk(proyecto))
        print(f"   ✓ {n} archivo(s) del proyecto (claves, estado, guiones, material)")
    for f in ("secretos.env", "youtube_token.json", "tiktok_token.json", "client_secret.json",
              "youtube_cookies.txt"):
        try:
            os.chmod(os.path.join(BASE_DIR, f), 0o600)
        except OSError:
            pass

    info = _cargar_json(os.path.join(tmp, "respaldo.json"), {})
    vieja = info.get("carpeta_salida")
    nueva = _salida_de_aqui(vieja)
    os.makedirs(nueva, exist_ok=True)
    salida_tmp = os.path.join(tmp, "salida")
    n_videos = 0
    if os.path.isdir(salida_tmp):
        for f in os.listdir(salida_tmp):
            shutil.copy2(os.path.join(salida_tmp, f), os.path.join(nueva, f))
            n_videos += f.endswith(".mp4")
    print(f"   ✓ carpeta de salida: {nueva} ({n_videos} video(s))")

    if vieja and nueva != vieja:
        # La carpeta cambió (teléfono → PC): config.json y los registros
        # guardan rutas absolutas. Sin cambiarlas, el publicador no
        # encontraría los videos pendientes y los daría por rechazados.
        ruta_cfg = os.path.join(BASE_DIR, "config.json")
        cfg = _cargar_json(ruta_cfg, {})
        cfg["carpeta_salida"] = nueva
        almacen.guardar(ruta_cfg, cfg)
        estado = os.path.join(BASE_DIR, "pipeline_state")
        tocados = 0
        for ruta in ([os.path.join(nueva, "resultado_lote.json")]
                     + [os.path.join(estado, f) for f in (os.listdir(estado)
                                                          if os.path.isdir(estado) else [])
                        if f.endswith(".json")]):
            datos = _cargar_json(ruta, None)
            if datos is None:
                continue
            cambiado = _cambiar_rutas(datos, vieja, nueva)
            if cambiado != datos:
                almacen.guardar(ruta, cambiado)
                tocados += 1
        print(f"   ✓ rutas de {vieja} cambiadas a {nueva} en config.json y {tocados} registro(s)")

    shutil.rmtree(tmp, ignore_errors=True)
    if borrar:
        # El que subió el panel: lleva las claves y ya no hace falta.
        try:
            os.remove(archivo)
        except OSError:
            pass
    print("\n  Listo. Abre el panel y revisa Ajustes → 🔑 Servicios: todo debería salir conectado.\n")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Guarda en un archivo todo lo que git no trae.")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--sin-videos", action="store_true", help="Sin ningún video grabado.")
    g.add_argument("--todos-los-videos", action="store_true",
                   help="También los ya subidos (pueden ser varios GB).")
    g.add_argument("--solo-ajustes", action="store_true",
                   help="Solo claves, config, estado y guiones: sin videos, fondos ni música.")
    ap.add_argument("--destino", help="Carpeta donde dejarlo (por omisión, Descargas).")
    ap.add_argument("--restaurar", nargs="?", const="", metavar="ARCHIVO",
                    help="Al revés: pone de vuelta un respaldo (el más reciente de "
                         "Descargas si no se dice cuál). En Termux, mejor restaurar.sh.")
    ap.add_argument("--borrar", action="store_true",
                    help="Con --restaurar: borra el archivo al terminar bien.")
    args = ap.parse_args(argv if argv is not None else sys.argv[1:])
    if args.restaurar is not None:
        return restaurar(args.restaurar or None, borrar=args.borrar)
    cuales = "ninguno" if (args.sin_videos or args.solo_ajustes) else "todos" if args.todos_los_videos else "sin_subir"

    proyecto = archivos_del_proyecto()
    if args.solo_ajustes:
        # Tampoco los enlaces: son el material que enlaza «Enlazar material»
        # (fondos, música, plantilla), no ajustes.
        proyecto = [r for r in proyecto if not r.lower().endswith(PESADOS)
                    and not os.path.islink(os.path.join(BASE_DIR, r))]
    salida = carpeta_salida()
    videos = videos_a_guardar(salida, cuales)
    lote = os.path.join(salida, "resultado_lote.json") if salida else None

    destino = args.destino or destino_por_omision()
    os.makedirs(destino, exist_ok=True)
    nombre = (f"{PREFIJO}{datetime.now().strftime('%Y%m%d-%H%M')}"
              f"{'-ajustes' if args.solo_ajustes else ''}.tar")
    ruta = os.path.join(destino, nombre)
    parcial = ruta + ".parcial"

    peso = sum(os.path.getsize(os.path.join(BASE_DIR, r)) for r in proyecto)
    peso += sum(os.path.getsize(v) for v in videos)
    print(f"\n  Guardando {len(proyecto)} archivo(s) del proyecto"
          + (f" y {len(videos)} video(s)" if videos else "") + f" (~{_mb(peso)})…")

    # Un .parcial que se renombra al final: si Android mata el proceso a
    # medias, no queda un respaldo cortado con cara de bueno.
    # dereference: un enlace entra con el contenido del archivo al que
    # apunta. Guardado como enlace no serviría de nada en otro aparato (ni
    # en este tras un reinicio de fábrica, que borra Descargas).
    with tarfile.open(parcial, "w", dereference=True) as tar:
        for r in proyecto:
            tar.add(os.path.join(BASE_DIR, r), arcname=f"proyecto/{r}")
        if lote and os.path.isfile(lote):
            tar.add(lote, arcname="salida/resultado_lote.json")
        for v in videos:
            tar.add(v, arcname=f"salida/{os.path.basename(v)}")
        # Dónde estaba la salida, para devolver los videos a su sitio.
        info = json.dumps({"carpeta_salida": salida, "fecha": datetime.now().isoformat(
            timespec="seconds"), "videos": cuales}, ensure_ascii=False).encode("utf-8")
        ti = tarfile.TarInfo("respaldo.json")
        ti.size = len(info)
        tar.addfile(ti, io.BytesIO(info))
    os.replace(parcial, ruta)
    try:
        os.chmod(ruta, 0o600)
    except OSError:
        pass
    # Para el panel: dónde quedó, y así enseñar la ruta y el botón de enviarlo
    # sin tener que leer la salida del trabajo.
    try:
        import almacen
        almacen.guardar(RUTA_ULTIMO, {"ruta": ruta, "bytes": os.path.getsize(ruta),
                                      "fecha": datetime.now().isoformat(timespec="seconds"),
                                      "tipo": ("ajustes" if args.solo_ajustes else cuales)})
    except Exception:                              # noqa: BLE001 — el respaldo ya está hecho
        pass

    print(f"\n   ✓ {ruta}  ({_mb(os.path.getsize(ruta))})")
    faltan = [f"{k} — {v}" for k, v in IMPORTANTES.items() if k not in proyecto]
    if faltan:
        print("\n  No estaban (no se pudieron guardar):")
        for f in faltan:
            print(f"   ○ {f}")
    if cuales == "sin_subir":
        print(f"\n  Videos: {len(videos)} sin subir a YouTube. Los ya subidos no van "
              "(para incluirlos: --todos-los-videos).")
    print("\n  ⚠ Lleva tus claves y tokens: guárdalo donde solo lo veas tú.")
    print("  Sácalo del teléfono ANTES de reiniciar (Google Drive, un PC o una SD):")
    print("  el reinicio de fábrica borra también Descargas.")
    if args.solo_ajustes:
        print("\n  Para el PC: mándatelo (Drive, WhatsApp a ti mismo, correo) y en el panel")
        print("  de Windows: Ajustes → 🧰 Tareas → «Traer todo del teléfono» → elegir el archivo.\n")
    else:
        print("\n  Para restaurar, en el teléfono nuevo:  bash restaurar.sh  (ver INSTALAR.md)")
        print("  En el PC: Ajustes → 🧰 Tareas → «Traer todo del teléfono».\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
