"""
Vuelve a comprimir videos ya renderizados que pesan de más.

Los videos hechos antes del cambio de encoder salieron con `-preset ultrafast`
y sin `-crf`: verticales de dos minutos de 400 MB. Eso son horas de subida por
video, y la app de TikTok ni se los descarga para abrir el editor.

Esto NO vuelve a renderizar: no toca la voz, ni los subtítulos, ni el montaje.
Coge el .mp4 que ya existe y lo pasa otra vez por ffmpeg con una compresión
decente. En un teléfono tarda un par de minutos por video en vez de la media
hora que costaría rehacerlo entero.

  python recomprimir.py              # dice qué haría, sin tocar nada
  python recomprimir.py --si         # lo hace
  python recomprimir.py --umbral 150 # solo los de más de 150 MB
  python recomprimir.py --limpiar    # borra restos de una tanda cortada

No lo pases dos veces sobre los mismos archivos: recomprimir algo ya
comprimido apenas quita peso y sí quita calidad. El umbral por defecto de
100 MB está para eso, para que solo entren los que de verdad sobran.
"""
import os
import sys
import json
import glob
import shutil
import argparse
import subprocess

import publisher

MB = 1024 * 1024

# El temporal acaba en .mp4, asi que cae dentro del glob que busca candidatos:
# sin filtrarlo, un resto de una tanda interrumpida se recomprimiria como si
# fuera un video de verdad, y ademas seguiria ocupando sitio en silencio.
SUFIJO_TMP = ".recomprimiendo.mp4"


def candidatos(umbral_mb):
    cfg = publisher.cargar_config()
    patron = os.path.join(cfg["carpeta_salida"], "*.mp4")
    fuera = []
    for ruta in sorted(glob.glob(patron)):
        if ruta.endswith(SUFIJO_TMP):
            continue
        tam = os.path.getsize(ruta)
        if tam >= umbral_mb * MB:
            fuera.append((ruta, tam))
    return fuera


def recomprimir(ruta, preset, crf):
    """Devuelve (tamaño antes, tamaño después). Deja el original si algo falla."""
    antes = os.path.getsize(ruta)
    tmp = ruta + SUFIJO_TMP
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-i", ruta,
        "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
        # El audio ya está en aac y no es lo que abulta: recodificarlo solo
        # gastaría tiempo y calidad.
        "-c:a", "copy",
        "-movflags", "+faststart",
        tmp,
    ]
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise

    despues = os.path.getsize(tmp)
    if despues >= antes:
        # Ya estaba bien comprimido: cambiarlo solo perdería calidad.
        os.remove(tmp)
        return antes, antes

    # os.replace es atómico: o queda el nuevo entero, o queda el viejo. Un
    # corte de luz a media escritura no puede dejar un .mp4 a medias donde
    # antes había uno que funcionaba.
    os.replace(tmp, ruta)
    return antes, despues


def restos():
    """Temporales de tandas que se cortaron a medias. No valen nada."""
    cfg = publisher.cargar_config()
    patron = os.path.join(cfg["carpeta_salida"], "*" + SUFIJO_TMP)
    return [(r, os.path.getsize(r)) for r in sorted(glob.glob(patron))]


def main(argv=None):
    ap = argparse.ArgumentParser(description="Recomprime videos que pesan de más.")
    ap.add_argument("--si", action="store_true", help="Hacerlo de verdad.")
    ap.add_argument("--umbral", type=int, default=100,
                    help="Solo los de más de estos MB (por defecto 100).")
    ap.add_argument("--preset", default="veryfast")
    ap.add_argument("--crf", type=int, default=23)
    ap.add_argument("--limpiar", action="store_true",
                    help="Borrar los temporales de tandas interrumpidas. "
                         "No lo uses con una recompresión en marcha.")
    args = ap.parse_args(argv if argv is not None else sys.argv[1:])

    if not shutil.which("ffmpeg"):
        raise SystemExit("No encuentro ffmpeg.")

    sobras = restos()
    if sobras:
        peso = sum(t for _, t in sobras) / MB
        print(f"\n  {len(sobras)} temporal(es) de una tanda interrumpida, {peso:.0f} MB:")
        for r, t in sobras:
            print(f"   {t / MB:7.1f} MB  {os.path.basename(r)[:60]}")
        if args.limpiar:
            for r, _ in sobras:
                os.remove(r)
            print(f"  ✓ Borrados, {peso:.0f} MB libres.\n")
        else:
            print("  No sirven para nada. Para borrarlos:  python recomprimir.py --limpiar")
            print("  (pero no mientras haya una recompresión corriendo).\n")

    lista = candidatos(args.umbral)
    if not lista:
        print(f" Ningún video pasa de {args.umbral} MB. No hay nada que hacer.")
        return 0

    total = sum(t for _, t in lista)
    print(f"\n {len(lista)} video(s) de más de {args.umbral} MB, {total / MB:.0f} MB en total:\n")
    for ruta, tam in lista:
        print(f"   {tam / MB:7.1f} MB  {os.path.basename(ruta)[:60]}")

    if not args.si:
        print("\n Esto solo era el listado. Para hacerlo:  python recomprimir.py --si")
        return 0

    print(f"\n Recomprimiendo con -preset {args.preset} -crf {args.crf}.\n")
    ahorrado = 0
    for i, (ruta, _) in enumerate(lista, 1):
        nombre = os.path.basename(ruta)[:50]
        print(f"  [{i}/{len(lista)}] {nombre}", flush=True)
        try:
            antes, despues = recomprimir(ruta, args.preset, args.crf)
        except Exception as exc:
            print(f"      ✗ falló, lo dejo como estaba: {exc}")
            continue
        if despues == antes:
            print("      · ya estaba bien comprimido")
            continue
        ahorrado += antes - despues
        print(f"      {antes / MB:.1f} MB → {despues / MB:.1f} MB "
              f"({100 - despues * 100 // antes}% menos)")

    print(f"\n ✓ Liberados {ahorrado / MB:.0f} MB.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
