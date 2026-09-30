"""
Aparta de la cola las historias demasiado largas para un short, y las devuelve.

Mientras los largos estén bloqueados (ver formato.py), una historia que no
cabe en un short se queda en guion.txt sin producir nada: ocupa la Cola, sale
en la lista de grabar, y el render le gasta la voz para acabar aplazándola.
Aquí se apartan a guion_largas.txt, en el mismo formato, sin tocar el texto,
para cuando se abran los largos:

  python archivar_largas.py                # dice qué apartaría, sin tocar nada
  python archivar_largas.py --si           # lo hace, guardando antes una copia
  python archivar_largas.py --devolver     # dice cuántas volverían a la cola
  python archivar_largas.py --devolver --si

Devolver las pone al FINAL de guion.txt: añadir detrás no cambia el número de
ninguna de las que ya están.

OJO CON LA NUMERACIÓN, igual que en partir_historias.py: quitar una historia
de en medio corre el número de las de detrás, y el render reconoce lo ya
grabado por "NN_Titulo.mp4". Si detrás de una larga hay alguna ya grabada,
antes se quitan de la cola las grabadas, como hace «Limpiar».
"""
import os
import sys
import shutil
import argparse
from datetime import datetime

import almacen

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RUTA_GUION = os.path.join(BASE_DIR, "guion.txt")
RUTA_LARGAS = os.path.join(BASE_DIR, "guion_largas.txt")
SEPARADOR = "===NUEVA_HISTORIA==="


def _juntar(bloques):
    return ("\n" + SEPARADOR + "\n").join(bloques) + "\n" if bloques else ""


def archivadas():
    """Los bloques apartados. Lo usa también el panel para contarlos."""
    if not os.path.exists(RUTA_LARGAS):
        return []
    with open(RUTA_LARGAS, "r", encoding="utf-8") as f:
        return [b.strip() for b in f.read().split(SEPARADOR) if b.strip()]


def guardar_para_largos(bloques):
    """Guarda enteras las historias que se acaban de partir en shorts, para
    grabarlas también como video largo cuando se abran los largos
    (devolver_si_se_abrieron). Las que ya estaban guardadas no se repiten.
    Devuelve cuántas se añadieron."""
    guardadas = archivadas()
    nuevas = [b.strip() for b in bloques if b.strip() and b.strip() not in guardadas]
    if nuevas:
        almacen.escribir_texto(RUTA_LARGAS, _juntar(guardadas + nuevas))
    return len(nuevas)


def devolver_si_se_abrieron():
    """Si los largos ya están abiertos (500 suscriptores, ver formato.py),
    devuelve a la cola las guardadas para que el render las grabe enteras y
    el publicador las suba. Lo llama pipeline.py antes de grabar; sin nada
    guardado, o con los largos cerrados, no hace nada. Devuelve cuántas."""
    guardadas = archivadas()
    if not guardadas:
        return 0
    try:
        import formato
        politica = formato.politica()
    except Exception as exc:                       # noqa: BLE001 — sin saberlo, se espera
        print(f"  No se pudo saber si los largos están abiertos ({exc}); las guardadas esperan.")
        return 0
    if not politica.get("permite_largos"):
        return 0
    print(f"\n  Los largos ya están abiertos ({politica.get('motivo', '')}): "
          f"vuelven a la cola {len(guardadas)} historia(s) guardada(s) para grabarlas enteras.")
    devolver(si=True)
    return len(guardadas)


def apartar(si):
    import limpiar_cola
    import partir_historias

    bloques = limpiar_cola.bloques_del_guion(RUTA_GUION)
    if not bloques:
        print(f"\n  No hay historias en {RUTA_GUION}.\n")
        return 0

    hechos = limpiar_cola.ya_renderizados()
    grabadas = {i for i, b in enumerate(bloques, 1) if limpiar_cola.apodo(b) in hechos}
    largas = {i for i, b in enumerate(bloques, 1)
              if i not in grabadas and partir_historias.analizar(b)}

    print(f"\n  {len(bloques)} historia(s) en la cola.\n")
    if not largas:
        print("  Ninguna es demasiado larga para un short. No hay nada que apartar.\n")
        return 0

    print(f"  Se apartarían {len(largas)} a {os.path.basename(RUTA_LARGAS)}:")
    for i in sorted(largas):
        print(f"   {i:3d}  {limpiar_cola.titulo_de(bloques[i - 1])[:60]}")

    quitar = grabadas if any(i > min(largas) for i in grabadas) else set()
    if quitar:
        print(f"\n  Antes se quitan de la cola las {len(quitar)} que ya tienen video, igual que\n"
              "  «Limpiar»: las de detrás cambian de número y el render las volvería a grabar.")

    if not si:
        print("\n  Esto era el listado. Para hacerlo:  python archivar_largas.py --si\n")
        return 0

    apartadas = [bloques[i - 1] for i in sorted(largas)]
    quedan = [b for i, b in enumerate(bloques, 1) if i not in largas and i not in quitar]

    # Primero a donde van, luego se quitan de donde estaban: si Android mata
    # el proceso entre medias, prefiero una historia repetida a una perdida.
    limpiar_cola.archivar_en_historial([bloques[i - 1] for i in sorted(quitar)])
    almacen.escribir_texto(RUTA_LARGAS, _juntar(archivadas() + apartadas))
    respaldo = f"{RUTA_GUION}.bak-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    shutil.copy2(RUTA_GUION, respaldo)
    almacen.escribir_texto(RUTA_GUION, _juntar(quedan))

    print(f"\n   ✓ {len(apartadas)} apartada(s) en {os.path.basename(RUTA_LARGAS)} "
          f"({len(archivadas())} en total)")
    print(f"   ✓ Copia de la cola anterior en {os.path.basename(respaldo)}")
    print(f"   ✓ {RUTA_GUION}: quedan {len(quedan)} historia(s).\n")
    return 0


def devolver(si):
    import limpiar_cola

    guardadas = archivadas()
    if not guardadas:
        print(f"\n  No hay nada apartado en {os.path.basename(RUTA_LARGAS)}.\n")
        return 0

    print(f"\n  Volverían al final de la cola {len(guardadas)}:")
    for b in guardadas:
        print(f"   ·  {limpiar_cola.titulo_de(b)[:60]}")
    if not si:
        print("\n  Esto era el listado. Para hacerlo:  python archivar_largas.py --devolver --si\n")
        return 0

    try:
        import formato
        if not formato.politica()["permite_largos"]:
            print("\n  Ojo: los largos siguen bloqueados, así que volverán a quedarse sin grabar.")
    except Exception:                              # noqa: BLE001 — solo es un aviso
        pass

    bloques = limpiar_cola.bloques_del_guion(RUTA_GUION)
    ya = set(bloques)
    nuevas = [b for b in guardadas if b not in ya]
    # En la cola antes que fuera del archivo, por lo mismo que al apartar.
    almacen.escribir_texto(RUTA_GUION, _juntar(bloques + nuevas))
    almacen.escribir_texto(RUTA_LARGAS, "")
    print(f"\n   ✓ {len(nuevas)} de vuelta en la cola, al final: nada cambió de número.\n")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Aparta de la cola las historias largas, o las devuelve.")
    ap.add_argument("--si", action="store_true", help="Hacerlo de verdad.")
    ap.add_argument("--devolver", action="store_true", help="Devolverlas a la cola.")
    args = ap.parse_args(argv if argv is not None else sys.argv[1:])
    return devolver(args.si) if args.devolver else apartar(args.si)


if __name__ == "__main__":
    sys.exit(main())
