"""
Previsualiza los presets de subtítulos: renderiza el MISMO momento con cada
uno y arma una hoja de contactos para compararlos de un vistazo.

Por qué existe: elegir un estilo leyendo nombres de colores y tamaños no
funciona; hay que verlo sobre tu propio gameplay, con tu propia fuente y a la
resolución real. Esto lo hace sin gastar un render completo por estilo.

No toca tu configuración: los presets se aplican solo aquí. Cuando decidas,
lo dejas fijo en config.json con {"subtitulos": {"preset": "nombre"}}.

Uso:
  python previsualizar_estilos.py               # hoja de contactos (rápido)
  python previsualizar_estilos.py --video       # además, un clip por estilo
  python previsualizar_estilos.py --texto "Otra frase de ejemplo"
  python previsualizar_estilos.py --short       # en vertical (1080x1920)

Salida: en la carpeta de videos, subcarpeta "previsualizacion_estilos".
"""
import os
import sys
import glob
import shutil
import argparse
import subprocess
import tempfile

import generar_video_maestro as g

# Frase de ejemplo y timing inventado pero realista: ~2.6 palabras/segundo,
# con duraciones desiguales, que es lo que hace visible el efecto de resalte.
TEXTO_POR_DEFECTO = "Le di techo gratis a mi hermano y terminaron tratándome como su sirviente"
DUR_POR_PALABRA = {2: 0.16, 3: 0.22, 4: 0.28, 5: 0.34, 6: 0.40, 7: 0.46}


def timing_falso(texto):
    palabras, t = [], 0.35
    for w in texto.split():
        dur = DUR_POR_PALABRA.get(len(w), 0.52 if len(w) > 7 else 0.14)
        palabras.append({"texto": w, "inicio": round(t, 3), "duracion": dur})
        t += dur + 0.055
    return palabras


def momento_interesante(palabras, es_short, subs):
    """Instante donde ya se ve una frase completa con una palabra resaltada
    a media frase — no la primera ni la última, que son casos degenerados."""
    por_grupo = subs["palabras_por_frase_short"] if es_short else subs["palabras_por_frase_largo"]
    if subs["estilo"] == "pop":
        por_grupo = 1
    idx = min(len(palabras) - 1, max(1, por_grupo // 2 + por_grupo))
    p = palabras[idx]
    return p["inicio"] + p["duracion"] / 2.0


def fondo_de_muestra(w, h, destino):
    """Toma un fotograma del gameplay real; si no hay, un degradado neutro."""
    candidatos = [f for f in glob.glob("fondo_*") if f.lower().endswith((".mp4", ".webm", ".mkv", ".mov"))]
    if candidatos:
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
               "-ss", "12", "-i", candidatos[0], "-frames:v", "1",
               "-vf", f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}", destino]
        if subprocess.run(cmd, capture_output=True).returncode == 0 and os.path.exists(destino):
            return destino
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", f"gradients=s={w}x{h}:c0=0x1c2733:c1=0x3a4a5e:d=1",
         "-frames:v", "1", destino],
        capture_output=True,
    )
    return destino


def render_preset(nombre, texto, es_short, carpeta, hacer_video=False):
    """Devuelve (ruta_png, ruta_mp4_o_None) para un preset."""
    g.CONFIG["subtitulos"] = g.resolver_subtitulos({}, nombre)
    subs = g._cfg_subs()

    w, h = (1080, 1920) if es_short else (1920, 1080)
    palabras = timing_falso(texto)
    dur_total = palabras[-1]["inicio"] + palabras[-1]["duracion"] + 0.4

    with tempfile.TemporaryDirectory() as tmp:
        ass = os.path.join(tmp, "s.ass")
        g.convertir_timing_a_karaoke_ass(palabras, ass, 0.0, es_short)

        fondo = fondo_de_muestra(w, h, os.path.join(tmp, "bg.png"))
        f_ass = ass.replace("\\", "\\\\").replace(":", "\\:")
        if os.path.isdir(g.RUTA_FUENTES):
            f_ass += ":fontsdir=" + g.RUTA_FUENTES.replace("\\", "\\\\").replace(":", "\\:")

        png = os.path.join(carpeta, f"{nombre}.png")
        t = momento_interesante(palabras, es_short, subs)
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
             "-loop", "1", "-t", f"{dur_total:.2f}", "-i", fondo,
             "-vf", f"ass='{f_ass}'", "-ss", f"{t:.2f}", "-frames:v", "1", png],
            capture_output=True,
        )

        mp4 = None
        if hacer_video:
            mp4 = os.path.join(carpeta, f"{nombre}.mp4")
            subprocess.run(
                ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                 "-loop", "1", "-t", f"{dur_total:.2f}", "-i", fondo,
                 "-vf", f"ass='{f_ass}',fps=30", "-c:v", "libx264",
                 "-preset", "ultrafast", "-pix_fmt", "yuv420p", mp4],
                capture_output=True,
            )
            if not g.archivo_valido(mp4):
                mp4 = None

    return (png if g.archivo_valido(png) else None), mp4


def hoja_de_contactos(pngs, destino, es_short):
    from PIL import Image, ImageDraw
    if not pngs:
        return None

    cols = 2 if es_short else 1
    ancho_celda = 760 if es_short else 1100
    etiqueta = 44

    miniaturas = []
    for nombre, ruta in pngs:
        im = Image.open(ruta).convert("RGB")
        alto_celda = int(im.height * (ancho_celda / im.width))
        im = im.resize((ancho_celda, alto_celda))
        lienzo = Image.new("RGB", (ancho_celda, alto_celda + etiqueta), (18, 21, 26))
        lienzo.paste(im, (0, etiqueta))
        d = ImageDraw.Draw(lienzo)
        d.text((14, 12), nombre, fill=(235, 238, 241), font=g.obtener_fuente_bold(26))
        miniaturas.append(lienzo)

    filas = (len(miniaturas) + cols - 1) // cols
    w = cols * ancho_celda + (cols + 1) * 12
    alto_fila = miniaturas[0].height
    hoja = Image.new("RGB", (w, filas * alto_fila + (filas + 1) * 12), (10, 12, 15))
    for i, m in enumerate(miniaturas):
        x = 12 + (i % cols) * (ancho_celda + 12)
        y = 12 + (i // cols) * (alto_fila + 12)
        hoja.paste(m, (x, y))
    hoja.save(destino)
    return destino


def main():
    parser = argparse.ArgumentParser(description="Compara los presets de subtítulos.")
    parser.add_argument("--texto", default=TEXTO_POR_DEFECTO)
    parser.add_argument("--short", action="store_true", help="Vertical (1080x1920) en vez de horizontal.")
    parser.add_argument("--video", action="store_true", help="Generar también un clip por estilo.")
    args = parser.parse_args()

    if shutil.which("ffmpeg") is None:
        raise SystemExit("Falta ffmpeg. En Termux: pkg install ffmpeg")

    carpeta = os.path.join(g.CARPETA_SALIDA, "previsualizacion_estilos")
    os.makedirs(carpeta, exist_ok=True)

    print(f"\nGenerando {len(g.PRESETS_SUBTITULOS)} previsualizaciones "
          f"({'vertical' if args.short else 'horizontal'})...\n")

    pngs = []
    for nombre in g.PRESETS_SUBTITULOS:
        png, mp4 = render_preset(nombre, args.texto, args.short, carpeta, args.video)
        estado = "✅" if png else "❌"
        extra = " + clip" if mp4 else ""
        print(f"  {estado} {nombre}{extra}")
        if png:
            pngs.append((nombre, png))

    hoja = hoja_de_contactos(pngs, os.path.join(carpeta, "_comparacion.png"), args.short)

    print(f"\nListo. Ábrelo desde la galería:\n  {carpeta}")
    if hoja:
        print(f"\nLa comparación de todos juntos:\n  {os.path.basename(hoja)}")
    print(
        "\nCuando elijas, déjalo fijo en config.json:\n"
        '  {"subtitulos": {"preset": "el_que_quieras"}}\n'
        "O pruébalo en un video real sin tocar nada:\n"
        "  python generar_video_maestro.py --estilo el_que_quieras --historias 1\n"
    )


if __name__ == "__main__":
    main()
