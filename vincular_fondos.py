"""
Enlaza los videos de fondo desde donde los tengas guardados hasta la carpeta
del repo, con los nombres que espera generar_video_maestro.py.

Por qué hace falta: el renderizador busca los videos de fondo en la carpeta
donde se corre (os.listdir('.')), y elige entre ellos por el nombre —los que
contienen "fondo_vertical" se usan para shorts y los de "fondo_horizontal"
para videos largos. Si tus archivos viven en otra carpeta o con otros
nombres, no los encuentra.

Crea enlaces simbólicos, no copias: los videos de gameplay pesan cientos de
MB y no tiene sentido tener dos veces lo mismo en un teléfono. El enlace
vive en la carpeta del repo (sistema de archivos de Termux, que sí soporta
enlaces) y apunta al archivo original en la SD. Con --copiar hace copias de
verdad, por si prefieres eso.

La orientación se detecta con ffprobe, no se adivina por el nombre: si el
video es más alto que ancho va como vertical, si no, como horizontal.

Uso:
  python vincular_fondos.py                       # usa ~/storage/downloads/Reddicuentos
  python vincular_fondos.py ~/ruta/a/tus/videos   # otra carpeta
  python vincular_fondos.py --ver                 # solo mostrar, sin crear nada
  python vincular_fondos.py --copiar              # copiar en vez de enlazar
"""
import os
import sys
import glob
import json
import shutil
import argparse
import subprocess

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
EXTENSIONES = (".mp4", ".webm", ".mkv", ".mov")
CARPETA_POR_DEFECTO = os.path.expanduser("~/storage/downloads/Reddicuentos")


def dimensiones(ruta):
    """(ancho, alto) con ffprobe, o None si no se pudo leer."""
    try:
        res = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "json", ruta],
            capture_output=True, text=True, timeout=30,
        )
        if res.returncode != 0:
            return None
        flujos = json.loads(res.stdout).get("streams", [])
        if not flujos:
            return None
        return int(flujos[0]["width"]), int(flujos[0]["height"])
    except Exception:
        return None


def limpiar_enlaces_previos():
    """Quita enlaces de corridas anteriores para no acumular duplicados.
    Solo toca enlaces simbólicos con nuestro prefijo: nunca archivos reales,
    para no borrar por accidente un video que hayas puesto a mano."""
    quitados = 0
    for patron in ("fondo_vertical_*", "fondo_horizontal_*"):
        for ruta in glob.glob(os.path.join(BASE_DIR, patron)):
            if os.path.islink(ruta):
                os.unlink(ruta)
                quitados += 1
    return quitados


def main():
    parser = argparse.ArgumentParser(description="Enlaza los videos de fondo al repo.")
    parser.add_argument("carpeta", nargs="?", default=CARPETA_POR_DEFECTO)
    parser.add_argument("--ver", action="store_true", help="Solo mostrar, sin crear nada.")
    parser.add_argument("--copiar", action="store_true", help="Copiar en vez de enlazar.")
    args = parser.parse_args()

    carpeta = os.path.abspath(os.path.expanduser(args.carpeta))
    if not os.path.isdir(carpeta):
        raise SystemExit(
            f"No existe la carpeta:\n  {carpeta}\n\n"
            f"Si es la primera vez que Termux accede a tu almacenamiento, corre:\n"
            f"  termux-setup-storage\n"
            f"y acepta el permiso. Luego vuelve a intentar."
        )

    if shutil.which("ffprobe") is None:
        raise SystemExit("Falta ffprobe (viene con ffmpeg). Instálalo con: pkg install ffmpeg")

    videos = sorted(
        f for f in os.listdir(carpeta)
        if f.lower().endswith(EXTENSIONES) and not f.startswith(".")
    )
    if not videos:
        raise SystemExit(f"No encontré videos ({', '.join(EXTENSIONES)}) en:\n  {carpeta}")

    print(f"\n{len(videos)} video(s) en {carpeta}\n")

    verticales, horizontales, ilegibles = [], [], []
    for nombre in videos:
        origen = os.path.join(carpeta, nombre)
        dims = dimensiones(origen)
        if dims is None:
            ilegibles.append(nombre)
            print(f"  ⚠️  {nombre[:46]:<46} no se pudo leer")
            continue
        w, h = dims
        destino_lista = verticales if h > w else horizontales
        destino_lista.append(origen)
        forma = "vertical  " if h > w else "horizontal"
        tam = os.path.getsize(origen) / (1024 * 1024)
        print(f"  {forma}  {w}x{h:<5}  {tam:6.0f} MB  {nombre[:38]}")

    print(f"\n  → {len(verticales)} vertical(es) para shorts")
    print(f"  → {len(horizontales)} horizontal(es) para videos largos")

    if not verticales:
        print("\n  ⚠️  Sin videos verticales no se pueden renderizar shorts.")
    if not horizontales:
        print("  ⚠️  Sin videos horizontales no se pueden renderizar videos largos.")

    if args.ver:
        print("\n(--ver: no se creó nada)")
        return

    quitados = limpiar_enlaces_previos()
    if quitados:
        print(f"\n  Se quitaron {quitados} enlace(s) de una corrida anterior.")

    creados = 0
    for prefijo, lista in (("fondo_vertical", verticales), ("fondo_horizontal", horizontales)):
        for i, origen in enumerate(lista, 1):
            ext = os.path.splitext(origen)[1].lower()
            destino = os.path.join(BASE_DIR, f"{prefijo}_{i}{ext}")
            try:
                if args.copiar:
                    shutil.copy2(origen, destino)
                else:
                    os.symlink(origen, destino)
                creados += 1
            except OSError as exc:
                # Algunos sistemas de archivos no soportan enlaces; ahí copiamos.
                print(f"  No se pudo enlazar ({exc}); copiando {os.path.basename(origen)}...")
                try:
                    shutil.copy2(origen, destino)
                    creados += 1
                except Exception as exc2:
                    print(f"  ❌ Tampoco se pudo copiar: {exc2}")

    verbo = "copiado(s)" if args.copiar else "enlazado(s)"
    print(f"\n✅ {creados} video(s) {verbo} en la carpeta del repo.")
    print("   Comprueba con:  python estado.py")


if __name__ == "__main__":
    main()
