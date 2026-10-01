"""
Baja un video de fondo desde un enlace (YouTube, TikTok, Instagram, y los
cientos de sitios que entiende yt-dlp) a la carpeta de material, y lo deja
listo para «Re-enlazar material».

Es el mismo motor que usa la app Seal (github.com/JunkFood02/Seal): Seal es
una interfaz de Android para yt-dlp. La app no se puede meter en el
pipeline —es Kotlin—, pero yt-dlp es Python puro y corre igual en Termux
y en Windows. Ver docs/repos_revisados.md, sección 19.

Solo el video, sin audio: el fondo va mudo bajo la narración, y así se baja
la mitad. Como mucho 1080p: todo el material del proyecto nace en 1080p a
propósito (ver CLAUDE.md, «Resolución adaptativa»).

Uso:
  python bajar_fondo.py URL                      # decide por las dimensiones
  python bajar_fondo.py URL --forma vertical     # para shorts aunque sea 16:9
  python bajar_fondo.py URL --forma horizontal   # para videos largos
"""
import os
import re
import sys
import argparse

import vincular_fondos

# Más de esto no es un fondo, es una película: en el teléfono llenaría el
# almacenamiento por un clip del que se usan unos minutos.
TOPE_MB = 800
PREFIJOS = {"vertical": "fondo_vertical_", "horizontal": "fondo_horizontal_", "auto": ""}

# YouTube corta las descargas sin sesión con «Sign in to confirm you're not
# a bot». La salida de yt-dlp es usar la sesión de un navegador donde ya
# estés dentro: lee sus cookies en el momento y no las guarda en ningún
# archivo (las credenciales solo viven en secretos.env, ver CLAUDE.md).
# Firefox primero: Chrome y Edge cifran sus cookies desde 2024 de una forma
# que yt-dlp a menudo no puede abrir, y con el navegador abierto el archivo
# está bloqueado.
NAVEGADORES = ("firefox", "edge", "chrome", "brave", "opera", "vivaldi", "chromium")
RE_BOT = re.compile(r"not a bot|Sign in to confirm|confirm your age|cookies-from-browser", re.I)


def _avance(d):
    # Una línea por cada 10 %: la tarjeta del trabajo enseña la última, y
    # una por byte llenaría el registro.
    if d.get("status") == "downloading":
        total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
        if total:
            pct = int(d.get("downloaded_bytes", 0) * 100 / total)
            if pct // 10 != getattr(_avance, "ultimo", -1):
                _avance.ultimo = pct // 10
                print(f"⬇️  Bajando {pct}% de {total / 1e6:.0f} MB", flush=True)
    elif d.get("status") == "finished":
        print("✓ Descargado", flush=True)


class _Callado:
    """yt-dlp escribe sus «ERROR:» por su cuenta aunque vaya en quiet; con
    varios reintentos llenarían la tarjeta. Aquí se guardan los avisos —el
    de «no pude descifrar las cookies» llega como aviso, no como error— y
    se dice lo que pasó una vez."""
    def __init__(self):
        self.avisos = []
    def debug(self, msg):
        if "cookie" in str(msg).lower():
            self.avisos.append(str(msg))
    def info(self, msg): pass
    def warning(self, msg): self.avisos.append(str(msg))
    def error(self, msg): pass


def _por_que(nav, error, avisos):
    """Una frase de por qué no sirvió la sesión de ese navegador."""
    t = (error + " " + " ".join(avisos)).lower()
    if re.search(r"could not find .*(cookie|profile)|no such file|not installed|unsupported", t):
        return "no está instalado (o nunca se abrió)"
    if re.search(r"could not copy|database is locked|permission denied|being used by another", t):
        return ("estaba abierto (y cerrado chocaría con el mismo cifrado que Edge)"
                if nav in ("chrome", "edge", "brave") else "estaba abierto: ciérralo y vuelve a probar")
    if re.search(r"dpapi|app.?bound|failed to decrypt|unable to decrypt|cannot decrypt", t):
        return "cifra su sesión y ningún programa la puede leer (Chrome y Edge desde 2024)"
    if re.search(r"extracted 0 cookies|0 cookies", t):
        return "no tiene ninguna sesión guardada"
    if RE_BOT.search(t):
        return "se leyó, pero sin sesión de YouTube (o YouTube la rechazó)"
    return _corto(error)


def _corto(texto):
    return texto.splitlines()[0][:140] if texto else ""


def _intentar(yt_dlp, opciones, url, avisos=None):
    """(info, ruta, None) si bajó; (None, None, texto del error) si no.
    Los avisos de yt-dlp se dejan en `avisos` si se pasa una lista."""
    callado = _Callado()
    opciones = dict(opciones, logger=callado)
    try:
        with yt_dlp.YoutubeDL(opciones) as ydl:
            info = ydl.extract_info(url, download=True)
            return info, ydl.prepare_filename(info), None
    except Exception as exc:          # DownloadError, y los de leer cookies
        return None, None, re.sub(r"^ERROR:\s*", "", str(exc))
    finally:
        if avisos is not None:
            avisos.extend(callado.avisos)


def bajar(url, forma="auto", carpeta=None):
    try:
        import yt_dlp
    except ImportError:
        raise SystemExit("Falta yt-dlp. En Termux: pip install yt-dlp "
                         "(en Windows lo instala solo iniciar_windows.bat al reabrirlo).")

    carpeta = carpeta or vincular_fondos.CARPETA_POR_DEFECTO
    if not os.path.isdir(carpeta):
        if vincular_fondos.ES_TERMUX:
            raise SystemExit(f"No existe {carpeta}. Si es la primera vez, corre "
                             f"termux-setup-storage y acepta el permiso.")
        os.makedirs(carpeta, exist_ok=True)

    opciones = {
        # Solo video, hasta 1080p; mp4 si lo hay (lo lee cualquier ffmpeg sin
        # sorpresas). Si el sitio no separa video y audio, el combinado.
        "format": "bv*[height<=1080][ext=mp4]/bv*[height<=1080]/b[height<=1080]/b",
        "outtmpl": os.path.join(carpeta, PREFIJOS[forma] + "%(title).60B_%(id)s.%(ext)s"),
        "restrictfilenames": True,
        "noplaylist": True,
        "max_filesize": TOPE_MB * 1024 * 1024,
        "progress_hooks": [_avance],
        "quiet": True,
        "noprogress": True,
        "no_warnings": True,
    }
    print(f"Enlace: {url}", flush=True)
    info, ruta, error = _intentar(yt_dlp, opciones, url)
    probados = []
    if error and RE_BOT.search(error) and not vincular_fondos.ES_TERMUX:
        # En el teléfono no hay navegador del que leer nada.
        print("YouTube pide demostrar que no eres un robot; pruebo con la sesión de "
              "tus navegadores…", flush=True)
        for nav in NAVEGADORES:
            avisos = []
            info, ruta, error_nav = _intentar(
                yt_dlp, dict(opciones, cookiesfrombrowser=(nav,)), url, avisos)
            if not error_nav:
                print(f"   ✓ con la sesión de {nav}", flush=True)
                error = None
                break
            motivo = _por_que(nav, error_nav, avisos)
            if "no está instalado" not in motivo:
                probados.append(f"{nav}: {motivo}")
            print(f"   · {nav}: {motivo}", flush=True)
    if error:
        if RE_BOT.search(error):
            raise SystemExit(
                "❌ YouTube no deja bajar este video sin una sesión iniciada"
                + ((" — " + "; ".join(probados) + ".\n") if probados
                   else (" — no encontré ningún navegador instalado del que leer la sesión.\n"
                         if not vincular_fondos.ES_TERMUX else ".\n"))
                + ("   Lo que funciona en el PC: instalar Firefox (si no lo tienes), entrar en "
                   "youtube.com con tu cuenta, cerrarlo y volver a pulsar «Bajar». Su sesión sí "
                   "se deja leer y no se guarda en ningún archivo. Chrome y Edge cifran la suya "
                   "desde 2024 y no hay forma de usarla.\n"
                   if not vincular_fondos.ES_TERMUX else
                   "   En el teléfono: prueba desde el PC, o baja el video con la app Seal "
                   "(con tu cuenta) a Download/Reddicuentos y pulsa «Re-enlazar material».\n")
                + "   Si pasa con todos los videos, pon al día yt-dlp: pip install -U yt-dlp")
        raise SystemExit(f"❌ No se pudo bajar: {error}")

    if not os.path.exists(ruta):
        raise SystemExit(f"❌ No se bajó nada: el video pasa de {TOPE_MB} MB, o el sitio no "
                         f"dejó descargarlo. Prueba con otro enlace.")
    alto, ancho = info.get("height"), info.get("width")
    print(f"✅ {os.path.basename(ruta)}"
          + (f" ({ancho}x{alto})" if alto and ancho else "")
          + f"\n   en {carpeta}", flush=True)
    return ruta


def main(argv=None):
    ap = argparse.ArgumentParser(description="Baja un video de fondo desde un enlace.")
    ap.add_argument("url")
    ap.add_argument("--forma", choices=sorted(PREFIJOS), default="auto",
                    help="vertical (shorts), horizontal (largos) o auto (por dimensiones).")
    args = ap.parse_args(argv)
    if not re.match(r"^https?://", args.url):
        raise SystemExit("Eso no es un enlace (tiene que empezar por http:// o https://).")
    bajar(args.url, args.forma)


if __name__ == "__main__":
    sys.exit(main())
