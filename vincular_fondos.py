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

Cómo decide cuál va a cada formato:
  1. Si el archivo ya se llama "fondo_vertical..." o "fondo_horizontal...",
     se respeta esa intención — la pusiste tú a propósito.
  2. Si no, se clasifica por sus dimensiones (más alto que ancho = vertical).

Ojo: que un video sea 16:9 NO impide usarlo en shorts. El renderizador
escala y recorta al centro (force_original_aspect_ratio=increase + crop),
que es justo como se hacen los shorts de gameplay. Por eso un video
horizontal marcado como vertical es una decisión válida, no un error.

En Windows (y en un PC cualquiera) la carpeta por omisión es
Descargas\Reddicuentos: si no existe, se crea y se abre en el Explorador
para que dejes ahí los videos. Los enlaces simbólicos piden permisos de
administrador en Windows, así que ahí se hace un enlace duro (mismo disco:
no ocupa el doble) y, si tampoco se puede, una copia.

Uso:
  python vincular_fondos.py                       # usa ~/storage/downloads/Reddicuentos
  python vincular_fondos.py ~/ruta/a/tus/videos   # otra carpeta
  python vincular_fondos.py --ver                 # solo mostrar, sin crear nada
  python vincular_fondos.py --copiar              # copiar en vez de enlazar
"""
import os
import glob
import json
import shutil
import argparse
import subprocess

import almacen

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
EXTENSIONES = (".mp4", ".webm", ".mkv", ".mov")
ES_TERMUX = "PREFIX" in os.environ and "com.termux" in os.environ.get("PREFIX", "")
ES_WINDOWS = os.name == "nt"


def _carpeta_por_defecto():
    if ES_TERMUX or not ES_WINDOWS and os.path.isdir(os.path.expanduser("~/storage")):
        return os.path.expanduser("~/storage/downloads/Reddicuentos")
    # En un PC, la misma carpeta que en el teléfono pero en sus Descargas.
    return os.path.join(os.path.expanduser("~"), "Downloads", "Reddicuentos")


CARPETA_POR_DEFECTO = _carpeta_por_defecto()
# Lo que este script dejó en la carpeta del repo que no es un enlace
# simbólico (enlaces duros y copias, en Windows). Sin apuntarlo, la siguiente
# corrida no sabría distinguirlo de un video que pusiste tú a mano, y no lo
# podría quitar.
RUTA_HECHOS = os.path.join(BASE_DIR, "pipeline_state", "fondos_enlazados.json")
# fondo_vertical_3.mp4 → el archivo de verdad. Los enlaces se renumeran en
# cada corrida; el render apunta el original en cada video para que, si
# YouTube lo quita por un clip, se sepa cuál fue (diagnosticar_youtube.py).
RUTA_NOMBRES = os.path.join(BASE_DIR, "pipeline_state", "fondos_nombres.json")


def _hechos():
    try:
        import almacen
        datos = almacen.leer(RUTA_HECHOS, [])
        return [d for d in datos if isinstance(d, str)] if isinstance(datos, list) else []
    except Exception:
        return []


def _apuntar_hechos(nombres):
    import almacen
    almacen.guardar(RUTA_HECHOS, sorted(set(nombres)))


def poner(origen, destino, copiar=False):
    """Deja el archivo en el repo: enlace simbólico, si no enlace duro, si no
    copia. Devuelve cómo lo hizo, o lanza OSError si no pudo de ninguna."""
    if not copiar:
        try:
            os.symlink(origen, destino)
            return "enlazado"
        except OSError:
            pass
        try:
            os.link(origen, destino)   # mismo disco: no ocupa el doble
            return "enlazado"
        except OSError:
            pass
    shutil.copy2(origen, destino)
    return "copiado"


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
    hechos = set(_hechos())
    for patron in ("fondo_vertical_*", "fondo_horizontal_*"):
        for ruta in glob.glob(os.path.join(BASE_DIR, patron)):
            if os.path.islink(ruta) or os.path.basename(ruta) in hechos:
                os.unlink(ruta)
                quitados += 1
    if hechos:
        _apuntar_hechos([])
    return quitados


def vincular_otros_assets(carpeta, copiar=False):
    """Enlaza el resto del material que el renderizador espera encontrar en
    la carpeta del repo: la plantilla de la tarjeta de intro, la música y los
    efectos de sonido.

    Es el mismo problema que con los videos: el código los busca por nombre
    en el directorio donde se corre, así que si viven en tu carpeta de
    descargas no los encuentra y usa los respaldos sin avisar.
    """
    patrones = [
        # (patrón a buscar, nombre que debe tener en el repo o None = igual)
        ("tarjeta_plantilla.png", None),
        ("tarjeta_plantilla.jpg", None),
        ("Tarjeta de inicio.png", None),
        ("musica_*.mp3", None),
        ("efecto_transicion*", None),
    ]

    hechos = 0
    for patron, _ in patrones:
        for origen in glob.glob(os.path.join(carpeta, patron)):
            if not os.path.isfile(origen):
                continue
            destino = os.path.join(BASE_DIR, os.path.basename(origen))

            # Si ya hay un archivo real ahí (no un enlace nuestro), no se toca:
            # puede ser algo que pusiste a mano y sería una sorpresa perderlo.
            if os.path.exists(destino) and not os.path.islink(destino):
                print(f"  ya existe, se respeta: {os.path.basename(destino)}")
                continue
            if os.path.islink(destino):
                os.unlink(destino)

            try:
                como = poner(origen, destino, copiar)
                hechos += 1
                print(f"  {como}: {os.path.basename(origen)}")
            except OSError as exc:
                print(f"  ❌ {os.path.basename(origen)}: {exc}")
    return hechos


def _abrir_carpeta(carpeta):
    try:
        if ES_WINDOWS:
            os.startfile(carpeta)   # solo existe en Windows
        elif shutil.which("xdg-open"):
            subprocess.Popen(["xdg-open", carpeta], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
    except OSError:
        pass


def main():
    parser = argparse.ArgumentParser(description="Enlaza los videos de fondo al repo.")
    parser.add_argument("carpeta", nargs="?", default=CARPETA_POR_DEFECTO)
    parser.add_argument("--ver", action="store_true", help="Solo mostrar, sin crear nada.")
    parser.add_argument("--copiar", action="store_true", help="Copiar en vez de enlazar.")
    args = parser.parse_args()

    carpeta = os.path.abspath(os.path.expanduser(args.carpeta))
    if not os.path.isdir(carpeta):
        if ES_TERMUX:
            raise SystemExit(
                f"No existe la carpeta:\n  {carpeta}\n\n"
                f"Si es la primera vez que Termux accede a tu almacenamiento, corre:\n"
                f"  termux-setup-storage\n"
                f"y acepta el permiso. Luego vuelve a intentar."
            )
        # En el PC no hay permiso que pedir: se crea y se abre, y lo único
        # que falta es dejar ahí los videos.
        os.makedirs(carpeta, exist_ok=True)
        _abrir_carpeta(carpeta)
        raise SystemExit(
            f"Creé la carpeta de material y la abrí en el Explorador:\n  {carpeta}\n\n"
            f"Si tienes música (musica_*.mp3) o la plantilla de la tarjeta, van ahí también.\n"
            f"Pon tus videos de fondo en {carpeta} y vuelve a pulsar «Re-enlazar material».")

    if shutil.which("ffprobe") is None:
        raise SystemExit("Falta ffprobe (viene con ffmpeg). "
                         + ("Instálalo con: pkg install ffmpeg" if ES_TERMUX
                            else "Cierra y vuelve a abrir iniciar_windows.bat, que lo instala."))

    videos = sorted(
        f for f in os.listdir(carpeta)
        if f.lower().endswith(EXTENSIONES) and not f.startswith(".")
    )
    if not videos:
        if not ES_TERMUX:
            _abrir_carpeta(carpeta)
        raise SystemExit(f"No hay videos ({', '.join(EXTENSIONES)}) en la carpeta de material. "
                         f"Pon tus videos de fondo en {carpeta} y vuelve a pulsar «Re-enlazar material».")

    print(f"\n{len(videos)} video(s) en {carpeta}\n")

    verticales, horizontales, pesados = [], [], []
    for nombre in videos:
        origen = os.path.join(carpeta, nombre)
        dims = dimensiones(origen)
        if dims is None:
            print(f"  ⚠️  {nombre[:44]:<44} no se pudo leer")
            continue
        w, h = dims
        bajo = nombre.lower()

        # El nombre que ya le pusiste manda sobre las dimensiones: marcar un
        # 16:9 como "fondo_vertical" es una decisión válida (se recorta), no
        # un error que haya que corregir.
        if "fondo_vertical" in bajo:
            destino, motivo = verticales, "por su nombre"
        elif "fondo_horizontal" in bajo:
            destino, motivo = horizontales, "por su nombre"
        else:
            destino, motivo = (verticales if h > w else horizontales), "por dimensiones"
        destino.append(origen)

        forma = "shorts" if destino is verticales else "largos"
        tam = os.path.getsize(origen) / (1024 * 1024)
        if tam > 5000 or w >= 3840:
            pesados.append((nombre, tam, w, h))
        print(f"  → {forma:<7} {w}x{h:<5} {tam:7.0f} MB  {nombre[:30]:<30} ({motivo})")

    print(f"\n  {len(verticales)} para shorts · {len(horizontales)} para videos largos")

    # Un 16:9 sirve para shorts: el renderizador escala y recorta al centro
    # (force_original_aspect_ratio=increase + crop), que es como se hacen los
    # shorts de gameplay. Solo hay problema si no hay NADA.
    if not verticales and horizontales:
        print("\n  Sin material marcado para shorts: se usarán los horizontales,")
        print("  recortados al centro a 1080x1920. Es lo normal con gameplay.")
    if not horizontales and verticales:
        print("\n  Sin material marcado para largos: se usarán los verticales,")
        print("  recortados a 1920x1080.")
    if not verticales and not horizontales:
        print("\n  ⚠️  No quedó ningún video utilizable.")

    if pesados:
        print("\n  Aviso de rendimiento — estos son muy grandes para renderizar en un teléfono:")
        for nombre, tam, w, h in pesados:
            print(f"    {nombre[:38]:<38} {w}x{h}  {tam:.0f} MB")
        print("  Cada corte se recodifica: en 4K puede tardar varias veces más que en 1080p.")

    if args.ver:
        otros = []
        for patron in ("tarjeta_plantilla.png", "tarjeta_plantilla.jpg",
                       "Tarjeta de inicio.png", "musica_*.mp3", "efecto_transicion*"):
            otros += [os.path.basename(f) for f in glob.glob(os.path.join(carpeta, patron))
                      if os.path.isfile(f)]
        if otros:
            print(f"\n  Además se enlazarían {len(otros)} archivo(s):")
            for n in otros[:12]:
                print(f"    {n}")
            if len(otros) > 12:
                print(f"    ... y {len(otros) - 12} más")
        else:
            print("\n  No hay plantilla, música ni efectos en esa carpeta.")
        print("\n(--ver: no se creó nada)")
        return

    quitados = limpiar_enlaces_previos()
    if quitados:
        print(f"\n  Se quitaron {quitados} enlace(s) de una corrida anterior.")

    creados, no_simbolicos, copiados = 0, [], 0
    nombres = {}
    for prefijo, lista in (("fondo_vertical", verticales), ("fondo_horizontal", horizontales)):
        for i, origen in enumerate(lista, 1):
            ext = os.path.splitext(origen)[1].lower()
            destino = os.path.join(BASE_DIR, f"{prefijo}_{i}{ext}")
            if os.path.exists(destino) and not os.path.islink(destino):
                print(f"  ya existe, se respeta: {os.path.basename(destino)}")
                continue
            try:
                como = poner(origen, destino, args.copiar)
                nombres[os.path.basename(destino)] = os.path.basename(origen)
                creados += 1
                copiados += como == "copiado"
                if not os.path.islink(destino):
                    no_simbolicos.append(os.path.basename(destino))
            except OSError as exc:
                print(f"  ❌ {os.path.basename(origen)}: {exc}")
    if no_simbolicos:
        _apuntar_hechos(no_simbolicos)
    almacen.guardar(RUTA_NOMBRES, nombres)

    verbo = "copiado(s)" if args.copiar or copiados else "enlazado(s)"
    print(f"\n✅ {creados} video(s) {verbo} en la carpeta del repo.")

    otros = vincular_otros_assets(carpeta, args.copiar)
    if otros:
        print(f"✅ {otros} archivo(s) más {verbo} (plantilla, música, efectos).")

    print("   Comprueba con:  python estado.py")


if __name__ == "__main__":
    main()
