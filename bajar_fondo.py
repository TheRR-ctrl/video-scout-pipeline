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
    with yt_dlp.YoutubeDL(opciones) as ydl:
        try:
            info = ydl.extract_info(url, download=True)
        except yt_dlp.utils.DownloadError as exc:
            texto = re.sub(r"^ERROR:\s*", "", str(exc))
            raise SystemExit(f"❌ No se pudo bajar: {texto}")
        ruta = ydl.prepare_filename(info)

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
