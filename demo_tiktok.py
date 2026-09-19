"""
Demo de TikTok — el vídeo que pide la auditoría, sin grabarlo dos veces.

Para pasar la revisión de TikTok hay que mandarles una grabación de pantalla
enseñando el flujo entero funcionando (ver AUDITORIA_TIKTOK.md). Son cinco
escenas seguidas, sin cortes entre la elección y el resultado, y una toma
fallida son cuatro minutos tirados.

Lo que esto NO hace: darle al botón de grabar. Termux no tiene una orden para
grabar la pantalla, y Android no deja que una app grabe por otra sin que tú
des el permiso en el momento. La grabadora la arrancas tú desde los ajustes
rápidos (Android 11+ la trae). Eso es un gesto; lo demás es lo que se
olvida, y es lo que hace esto:

  · Antes  (--comprobar): que la toma vaya a salir. Token vivo, permisos
    correctos, la cuenta en privado —que es lo que permite grabar una
    publicación directa sin estar auditado todavía— y un vídeo listo y
    pequeño para que la subida dure poco.
  · Durante (--empezar / --estado): las cinco escenas en orden, marcándose
    solas a medida que ocurren de verdad. También salen en el panel.
  · Después (--revisar grabacion.mp4): que el archivo entre en los 50 MB que
    aceptan, y si no entra, recomprimirlo cuidando que el texto siga legible
    —que es justo lo que el revisor tiene que poder leer.

Uso:
  python demo_tiktok.py --comprobar
  python demo_tiktok.py --empezar
  python demo_tiktok.py --estado
  python demo_tiktok.py --revisar ~/storage/movies/grabacion.mp4
  python demo_tiktok.py --recomprimir ~/storage/movies/grabacion.mp4
"""
import os
import json
import logging
import argparse
import subprocess
from datetime import datetime, timezone

import almacen    # leer y escribir los .json de estado
import secretos   # carga secretos.env si las claves no están en el entorno

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CARPETA_ESTADO = os.path.join(BASE_DIR, "pipeline_state")
RUTA_DEMO = os.path.join(CARPETA_ESTADO, "demo_tiktok.json")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("demo_tiktok")

# TikTok acepta mp4 o mov de 50 MB como mucho. Se apunta a 48 para no jugarse
# la subida por un redondeo.
LIMITE_MB = 50
OBJETIVO_MB = 48

# Por debajo de esto el texto de la pantalla se empasta y el revisor no puede
# leer lo que tiene que comprobar. Si la grabación es tan larga que no cabe a
# este ritmo, el arreglo es cortarla, no comprimir más.
BITRATE_MINIMO_KBPS = 1200

# Un vídeo pequeño sube rápido, y la subida tiene que caber en la misma toma.
MB_COMODOS_PARA_SUBIR = 12

# Las cinco escenas, en el orden en que las tiene que ver el revisor. La
# última no se puede detectar: es abrir TikTok y enseñar el vídeo publicado
# en el perfil, y eso pasa fuera de aquí. Se enseña como recordatorio, no
# como casilla que se marca sola.
PASOS = [
    ("token", "Login Kit: sale la pantalla de permisos de TikTok con tu cuenta",
     "python generar_tiktok_token.py"),
    ("creador", "El panel enseña la cuenta destino y las opciones que admite",
     "Pestaña TikTok → Preparar en un vídeo"),
    ("eleccion", "Eliges privacidad y guardas; se ve el ✓",
     "En ese mismo formulario"),
    ("subida", "La subida corre hasta el final",
     "Pestaña TikTok → Subir"),
    ("perfil", "Abres TikTok y enseñas el vídeo ya publicado en el perfil",
     "A mano, sin cortar la grabación"),
]
PASOS_AUTOMATICOS = {c for c, _, _ in PASOS} - {"perfil"}


def _ahora():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# El guion de la grabación
# ---------------------------------------------------------------------------

def empezar():
    """Pone a cero el guion y marca la hora. Lo que pase a partir de ahora
    cuenta; lo de antes, no."""
    almacen.guardar(RUTA_DEMO, {"empezado_en": _ahora(), "hitos": {}})
    return _ahora()


def anotar(paso):
    """Marca un paso como hecho, si hay una grabación en curso.

    La llaman los sitios donde el paso ocurre de verdad (el generador de
    token, el panel, el publicador), así que nunca se marca algo que no haya
    pasado. Nunca revienta: un fallo apuntando el guion no puede llevarse por
    delante la publicación que lo provocó.
    """
    try:
        datos = almacen.leer(RUTA_DEMO, None)
        if not datos or not datos.get("empezado_en"):
            return
        datos.setdefault("hitos", {})[paso] = _ahora()
        almacen.guardar(RUTA_DEMO, datos)
    except Exception as exc:
        logger.debug(f"No se pudo anotar el paso {paso}: {exc}")


def estado():
    """Las cinco escenas con su estado, para la terminal y para el panel."""
    datos = almacen.leer(RUTA_DEMO, None) or {}
    hitos = datos.get("hitos") or {}
    return {
        "empezado_en": datos.get("empezado_en"),
        "pasos": [{
            "clave": clave,
            "que": que,
            "donde": donde,
            "hecho": hitos.get(clave),
            "automatico": clave in PASOS_AUTOMATICOS,
        } for clave, que, donde in PASOS],
    }


# ---------------------------------------------------------------------------
# Antes de grabar
# ---------------------------------------------------------------------------

def _ok(que, detalle=""):
    return {"nivel": "ok", "que": que, "detalle": detalle}


def _mal(que, detalle, nivel="fallo"):
    return {"nivel": nivel, "que": que, "detalle": detalle}


def comprobar():
    """Todo lo que tiene que estar en su sitio antes de darle a grabar.

    Cada cosa que se comprueba aquí es una toma que no hay que repetir. La
    que más: sin auditar, una publicación directa a una cuenta pública falla
    con unaudited_client_can_only_post_to_private_accounts, y falla justo en
    la escena que más importa.
    """
    import tiktok_publisher as tk
    puntos = []

    # --- credenciales -------------------------------------------------------
    if os.environ.get("TIKTOK_CLIENT_KEY", "").strip() and \
       os.environ.get("TIKTOK_CLIENT_SECRET", "").strip():
        puntos.append(_ok("Las credenciales están guardadas"))
    else:
        puntos.append(_mal(
            "Faltan TIKTOK_CLIENT_KEY o TIKTOK_CLIENT_SECRET",
            "Están en secretos.env. Sin ellas no se puede refrescar el token."))
        return puntos   # sin esto no tiene sentido seguir preguntando

    # --- token y permisos ---------------------------------------------------
    try:
        token, guardado = tk.token_valido()
    except SystemExit as exc:
        puntos.append(_mal("No hay token válido", str(exc)))
        return puntos

    ambitos = set((guardado.get("scope") or "").replace(",", " ").split())
    puntos.append(_ok("El token está vivo", " ".join(sorted(ambitos)) or "sin ámbitos anotados"))

    for necesario in ("user.info.basic", "video.publish"):
        if necesario not in ambitos:
            puntos.append(_mal(
                f"Al token le falta {necesario}",
                "Quítalo y vuelve a autorizar: rm tiktok_token.json && "
                "python generar_tiktok_token.py"))

    if "video.upload" in ambitos:
        puntos.append(_mal(
            "El token todavía trae video.upload",
            "TikTok rechaza la revisión si pides un permiso que no enseñas "
            "funcionando. Quítalo en Scopes, aplica los cambios y vuelve a "
            "autorizar.", nivel="aviso"))

    # --- modo directo -------------------------------------------------------
    cfg = tk.cargar_config()
    if cfg["modo"] == "directo":
        puntos.append(_ok("El panel está en modo directo"))
    else:
        puntos.append(_mal(
            f"El modo es «{cfg['modo']}», no «directo»",
            "La auditoría es justamente de Direct Post: en borrador no se ve "
            "lo que tienen que revisar."))

    # --- la cuenta, y si está en privado -----------------------------------
    try:
        creador = tk.consultar_creador(token)
    except Exception as exc:
        puntos.append(_mal("No se pudo preguntar por la cuenta", str(exc)))
        return puntos

    privacidades = creador.get("privacy_level_options") or []
    puntos.append(_ok(f"La cuenta responde: @{creador.get('creator_username', '?')}",
                      ", ".join(privacidades)))

    # Una cuenta privada no puede publicar en público, así que TikTok no
    # ofrece esa privacidad. Es la forma de saber desde aquí en qué estado
    # está la cuenta sin preguntárselo a nadie.
    if "PUBLIC_TO_EVERYONE" in privacidades:
        puntos.append(_mal(
            "La cuenta está en público",
            "Sin auditar, publicar en directo a una cuenta pública falla con "
            "unaudited_client_can_only_post_to_private_accounts. Ponla en "
            "privado (Ajustes → Privacidad → Cuenta privada), graba el demo, "
            "y vuelve a ponerla pública después."))
    else:
        puntos.append(_ok("La cuenta está en privado, que es como hay que grabarlo"))

    # --- un vídeo listo -----------------------------------------------------
    try:
        pendientes, _ = tk.videos_pendientes()
    except Exception as exc:
        pendientes = []
        puntos.append(_mal("No se pudo mirar qué vídeos hay listos", str(exc), nivel="aviso"))

    if not pendientes:
        puntos.append(_mal(
            "No hay ningún vídeo preparado para subir",
            "Prepara uno en el panel (pestaña Revisar → Preparar con Gemini) "
            "para tener qué enseñar."))
    else:
        conmb = [(v, os.path.getsize(v["ruta"]) / (1024 * 1024))
                 for v in pendientes if os.path.exists(v["ruta"])]
        conmb.sort(key=lambda par: par[1])
        if conmb:
            v, mb = conmb[0]
            nombre = os.path.basename(v["ruta"])
            if mb <= MB_COMODOS_PARA_SUBIR:
                puntos.append(_ok(f"{len(conmb)} vídeo(s) listos; el más ligero pesa {mb:.0f} MB",
                                  nombre))
            else:
                puntos.append(_mal(
                    f"El vídeo más ligero pesa {mb:.0f} MB",
                    "La subida se va a alargar y la grabación con ella. "
                    "Recomprime antes: python recomprimir.py --si", nivel="aviso"))

    return puntos


# ---------------------------------------------------------------------------
# Después de grabar
# ---------------------------------------------------------------------------

def datos_de(ruta):
    """Duración, tamaño y resolución de la grabación."""
    res = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "format=duration,size:stream=width,height,codec_type",
         "-of", "json", ruta],
        capture_output=True, text=True, timeout=30,
    )
    if res.returncode != 0:
        raise RuntimeError(f"ffprobe falló: {res.stderr.strip()[:200]}")
    d = json.loads(res.stdout)
    video = next((s for s in d.get("streams", []) if s.get("codec_type") == "video"), {})
    return {
        "duracion": float(d.get("format", {}).get("duration") or 0),
        "mb": int(d.get("format", {}).get("size") or 0) / (1024 * 1024),
        "ancho": video.get("width"),
        "alto": video.get("height"),
    }


def revisar_grabacion(ruta):
    """¿Entra tal cual? Devuelve (datos, lista de pegas)."""
    d = datos_de(ruta)
    pegas = []

    if os.path.splitext(ruta)[1].lower() not in (".mp4", ".mov"):
        pegas.append(_mal("No es mp4 ni mov",
                          "Son los dos únicos formatos que aceptan."))

    if d["mb"] > LIMITE_MB:
        # A cuánto habría que bajar el ritmo de datos para que entrara. Si
        # sale por debajo del mínimo legible, comprimir no es la solución.
        kbps = (OBJETIVO_MB * 8 * 1024) / max(d["duracion"], 1)
        if kbps < BITRATE_MINIMO_KBPS:
            pegas.append(_mal(
                f"Pesa {d['mb']:.0f} MB y dura {d['duracion'] / 60:.1f} min",
                f"Para entrar en {LIMITE_MB} MB habría que bajar a "
                f"{kbps:.0f} kbps y el texto de la pantalla se volvería "
                f"ilegible. Corta las esperas de la grabación y vuelve a "
                f"revisarla."))
        else:
            pegas.append(_mal(
                f"Pesa {d['mb']:.0f} MB, y el límite son {LIMITE_MB}",
                f"Entra recomprimiendo a {kbps:.0f} kbps: "
                f"python demo_tiktok.py --recomprimir {os.path.basename(ruta)}",
                nivel="aviso"))

    if d["duracion"] < 30:
        pegas.append(_mal(
            f"Dura {d['duracion']:.0f} s",
            "Las cinco escenas no caben en tan poco. Comprueba que grabaste "
            "desde el Login Kit hasta el vídeo ya publicado.", nivel="aviso"))

    return d, pegas


def recomprimir(ruta, destino=None):
    """Baja el peso conservando la resolución.

    La resolución se queda como está a propósito: lo que el revisor tiene que
    hacer es LEER la pantalla, y reducir píxeles es justo lo que se lo
    impide. Se baja el ritmo de datos, que en una grabación de interfaz —casi
    toda quieta— cuesta mucho menos calidad visible.
    """
    d = datos_de(ruta)
    kbps = int((OBJETIVO_MB * 8 * 1024) / max(d["duracion"], 1))
    if kbps < BITRATE_MINIMO_KBPS:
        raise SystemExit(
            f"La grabación dura {d['duracion'] / 60:.1f} min: para entrar en "
            f"{LIMITE_MB} MB habría que bajar a {kbps} kbps y el texto no se "
            f"leería. Córtala primero.")

    # Se le deja el audio en 96 kbps y el resto para la imagen.
    video_kbps = max(BITRATE_MINIMO_KBPS, kbps - 96)
    destino = destino or f"{os.path.splitext(ruta)[0]}_para_tiktok.mp4"

    logger.info(f"Recomprimiendo a {video_kbps} kbps (de {d['mb']:.0f} MB)…")
    res = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", ruta,
         "-c:v", "libx264", "-preset", "veryfast",
         "-b:v", f"{video_kbps}k", "-maxrate", f"{int(video_kbps * 1.3)}k",
         "-bufsize", f"{video_kbps * 2}k",
         "-pix_fmt", "yuv420p", "-movflags", "+faststart",
         "-c:a", "aac", "-b:a", "96k", destino],
        capture_output=True, text=True, timeout=3600,
    )
    if res.returncode != 0:
        raise SystemExit(f"ffmpeg falló:\n{res.stderr.strip()[-600:]}")

    nuevo = os.path.getsize(destino) / (1024 * 1024)
    logger.info(f"Listo: {os.path.basename(destino)} — {nuevo:.1f} MB")
    if nuevo > LIMITE_MB:
        logger.warning(f"Sigue pasando de {LIMITE_MB} MB. Corta la grabación.")
    return destino


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _pintar_puntos(puntos):
    for p in puntos:
        icono = {"ok": "✅", "aviso": "⚠️ ", "fallo": "❌"}[p["nivel"]]
        print(f"  {icono} {p['que']}")
        if p["detalle"]:
            print(f"      {p['detalle']}")


def _pintar_estado():
    e = estado()
    if not e["empezado_en"]:
        print("\n  No hay ninguna grabación en curso.")
        print("  Empieza con:  python demo_tiktok.py --empezar\n")
        return
    print(f"\n  Grabación empezada: {e['empezado_en']}\n")
    for i, p in enumerate(e["pasos"], 1):
        if p["hecho"]:
            marca = "✅"
        else:
            marca = "· " if p["automatico"] else "○ "
        print(f"  {marca} {i}. {p['que']}")
        print(f"        {p['donde']}")
    print("\n  ○ = no se puede detectar desde aquí; hazlo y sigue.\n")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Prepara y revisa el vídeo demo de TikTok.")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--comprobar", action="store_true", help="Que la toma vaya a salir.")
    g.add_argument("--empezar", action="store_true", help="Empezar el guion de la grabación.")
    g.add_argument("--estado", action="store_true", help="Qué escenas llevas.")
    g.add_argument("--revisar", metavar="MP4", help="Revisar la grabación ya hecha.")
    g.add_argument("--recomprimir", metavar="MP4", help="Bajarla de los 50 MB.")
    args = ap.parse_args(argv)

    if args.comprobar:
        print("\n  Antes de darle a grabar:\n")
        puntos = comprobar()
        _pintar_puntos(puntos)
        if any(p["nivel"] == "fallo" for p in puntos):
            print("\n  Hay algo que arreglar antes de grabar.\n")
        else:
            print("\n  Todo listo. Arranca la grabadora de pantalla y:")
            print("    python demo_tiktok.py --empezar\n")
        return

    if args.empezar:
        empezar()
        print("\n  Guion empezado. Las escenas se van marcando solas.\n")
        _pintar_estado()
        return

    if args.estado:
        _pintar_estado()
        return

    if args.revisar:
        d, pegas = revisar_grabacion(args.revisar)
        print(f"\n  {os.path.basename(args.revisar)}")
        print(f"    {d['duracion'] / 60:.1f} min · {d['ancho']}x{d['alto']} · {d['mb']:.1f} MB\n")
        if not pegas:
            print("  ✅ Entra tal cual. Súbela en App review.\n")
        else:
            _pintar_puntos(pegas)
            print()
        return

    if args.recomprimir:
        recomprimir(args.recomprimir)


if __name__ == "__main__":
    main()
