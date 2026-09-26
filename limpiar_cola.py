"""
Saca de guion.txt las historias que ya tienen video hecho.

La cola no se vacía sola: guion.txt guarda todo lo que Gemini escribió, y
renderizar no borra nada de ahí. Después de unas semanas la pestaña Cola del
panel tiene decenas de historias que ya están grabadas, y hay que ir
recordando cuáles eran.

  python limpiar_cola.py         # dice qué quitaría, sin tocar nada
  python limpiar_cola.py --si    # lo hace, guardando antes una copia

OJO CON LA NUMERACION. El video se llama NN_Titulo.mp4, donde NN es la
posicion de la historia dentro de guion.txt. Al quitar historias, las que
quedan se renumeran: la que era 44 puede pasar a ser 1. Los videos ya hechos
conservan su nombre viejo (no se tocan), pero un `--historias 44` de antes
deja de apuntar a lo mismo. Por eso esto se empareja por titulo y no por
numero.

Desde el panel no hay que preocuparse: al repintar suelta lo elegido cuyo
numero cambio de dueño, asi que la seleccion y la musica no se quedan
apuntando a otra historia. Desde la terminal, repasa tu `--historias`.
"""
import io
import os
import re
import sys
import glob
import json
import argparse
import contextlib
from datetime import datetime

import publisher

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RUTA_GUION = os.path.join(BASE_DIR, "guion.txt")
# Las historias que salen de la cola se van acumulando aquí, con la fecha en
# que se quitaron. El respaldo con fecha guarda la cola ENTERA de aquel día,
# así que con varias limpiezas hay que ir abriendo copias hasta dar con la
# que tenía la historia; esto es un solo archivo con todo lo ya grabado, en
# el mismo formato, y sirve para releer un guion viejo sin desenterrar nada.
RUTA_HISTORIAL_GUION = os.path.join(BASE_DIR, "guion_historial.txt")
SEPARADOR = "===NUEVA_HISTORIA==="


def bloques_del_guion(ruta=None):
    ruta = ruta or RUTA_GUION
    if not os.path.exists(ruta):
        return []
    with open(ruta, "r", encoding="utf-8") as f:
        return [b.strip() for b in f.read().split(SEPARADOR) if b.strip()]


def apodo(bloque):
    """El mismo trozo de nombre que generar_video_maestro le pone al .mp4.

    Se usa su propia funcion en vez de copiar la expresion regular: si algun
    dia cambia como se limpian los titulos, esto cambia con ella y no se
    queda emparejando por una regla que ya no rige.
    """
    import generar_video_maestro as gvm
    # Esa funcion va narrando en voz alta que voz elige para cada historia.
    # Util al renderizar; aqui son 51 lineas de ruido tapando el listado.
    with contextlib.redirect_stdout(io.StringIO()):
        return gvm.extraer_titulo_y_cuerpo(bloque)[7]


def titulo_de(bloque):
    lineas = [l.strip() for l in bloque.splitlines()
              if l.strip() and not l.strip().startswith(("#", "===", "📌", "🎙️"))]
    return lineas[0] if lineas else "(sin titulo)"


def ya_renderizados():
    """Apodos de lo que ya se grabo, venga del registro o del disco.

    Las dos fuentes suman: el registro recuerda videos que el borrado de los
    7 dias ya quito del telefono, y el disco tiene los que se renderizaron
    cuando el registro no estaba. Con una sola de las dos se colaria trabajo
    repetido.
    """
    carpeta = publisher.cargar_config()["carpeta_salida"]
    hechos = set()

    lote = os.path.join(carpeta, "resultado_lote.json")
    if os.path.exists(lote):
        with open(lote, "r", encoding="utf-8") as f:
            for v in json.load(f).get("completados", []):
                nombre = os.path.basename(v.get("ruta", ""))
                if nombre:
                    hechos.add(_apodo_de_archivo(nombre))

    for m in glob.glob(os.path.join(carpeta, "*.mp4")):
        hechos.add(_apodo_de_archivo(os.path.basename(m)))

    hechos.discard("")
    return hechos


def _apodo_de_archivo(nombre):
    """De "07_Mi_mama_me_levanto.mp4" saca "Mi_mama_me_levanto"."""
    sin_ext = re.sub(r"\.mp4$", "", nombre)
    return re.sub(r"^\d+_", "", sin_ext)


def archivar_en_historial(bloques, cuando=None, motivo="Quitada de la cola"):
    """Agrega al historial las historias que salen de la cola.

    Se agrega, nunca se reescribe: este archivo es lo único que queda de un
    guion cuando su respaldo con fecha se borra.
    """
    if not bloques:
        return 0
    cuando = cuando or datetime.now().strftime("%Y-%m-%d %H:%M")
    hay_algo = os.path.exists(RUTA_HISTORIAL_GUION) and os.path.getsize(RUTA_HISTORIAL_GUION)
    with open(RUTA_HISTORIAL_GUION, "a", encoding="utf-8") as f:
        for b in bloques:
            if hay_algo:
                f.write("\n" + SEPARADOR + "\n")
            f.write(f"# {motivo}: {cuando}\n{b}")
            hay_algo = True
        f.write("\n")
    return len(bloques)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Quita de la cola lo que ya tiene video.")
    ap.add_argument("--si", action="store_true", help="Hacerlo de verdad.")
    args = ap.parse_args(argv if argv is not None else sys.argv[1:])

    bloques = bloques_del_guion()
    if not bloques:
        raise SystemExit(f"No hay historias en {RUTA_GUION}.")

    hechos = ya_renderizados()
    fuera, quedan = [], []
    for i, b in enumerate(bloques, 1):
        (fuera if apodo(b) in hechos else quedan).append((i, b))

    print(f"\n  {len(bloques)} historia(s) en la cola, {len(hechos)} video(s) conocidos.\n")
    if not fuera:
        print("  Ninguna de las que están en la cola tiene video. No hay nada que quitar.\n")
        return 0

    print(f"  Se quitarían {len(fuera)}:")
    for i, b in fuera:
        print(f"   {i:3d}  {titulo_de(b)[:62]}")
    print(f"\n  Se quedarían {len(quedan)}, renumeradas del 1 al {len(quedan)}.")

    if not args.si:
        print("\n  Esto era el listado. Para hacerlo:  python limpiar_cola.py --si\n")
        return 0

    # Al historial ANTES de tocar guion.txt: si algo falla entre medias,
    # prefiero una historia repetida en el historial que una perdida.
    archivar_en_historial([b for _, b in fuera])

    sello = datetime.now().strftime("%Y%m%d-%H%M%S")
    respaldo = f"{RUTA_GUION}.bak-{sello}"
    os.replace(RUTA_GUION, respaldo)
    with open(RUTA_GUION, "w", encoding="utf-8") as f:
        f.write(("\n" + SEPARADOR + "\n").join(b for _, b in quedan) + "\n")

    print(f"\n   ✓ Las {len(fuera)} quitada(s) quedan en {os.path.basename(RUTA_HISTORIAL_GUION)}")
    print(f"   ✓ Copia de la cola anterior en {os.path.basename(respaldo)}")
    print(f"   ✓ {RUTA_GUION}: quedan {len(quedan)} historia(s).")
    # El servidor relee guion.txt en cada peticion y el panel repinta solo
    # en cuanto ve que el trabajo termino, asi que no hay que pulsar nada.
    # Aqui ponia que habia que darle a refrescar: mandaba a hacer a mano
    # algo que ya pasaba solo.
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
