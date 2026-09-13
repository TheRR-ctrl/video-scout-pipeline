"""
Cambia la carpeta de salida y repara las rutas ya guardadas.

Mover la carpeta a mano no basta. El pipeline guarda **rutas absolutas** en
cuatro sitios, y si apuntan a donde ya no hay nada pasan dos cosas malas:

  - resultado_lote.json deja de encontrar los .mp4, así que los pendientes
    desaparecen de la lista y el panel se queda vacío.
  - tiktok_subidos.json deja de reconocer lo ya subido, así que los videos
    que ya están en TikTok vuelven a la cola y se suben otra vez.

Esto reescribe esas rutas para que apunten a la carpeta nueva y actualiza
config.json. No mueve archivos: eso lo haces tú (o ya lo hiciste) desde el
gestor de archivos.

  python mover_salida.py "/storage/emulated/0/Download/Reddicuentos/Videos creados"
  python mover_salida.py "/storage/..." --si     # aplicarlo de verdad

Sin --si solo enseña el recuento. Se puede correr dos veces sin estropear
nada: lo que ya apunta a la carpeta nueva se deja como está.
"""
import os
import sys
import json
import glob
import shutil
import argparse

import publisher

# En Android las dos rutas son el mismo sitio, pero como texto no se parecen:
# sin esto, mudarse de /sdcard/... a /storage/emulated/0/... reescribiria
# rutas que ya estaban bien y el recuento mentiria.
ALIAS = (("/sdcard/", "/storage/emulated/0/"),)


def normaliza(ruta):
    for viejo, nuevo in ALIAS:
        if ruta.startswith(viejo):
            return nuevo + ruta[len(viejo):]
    return ruta


def mismo_sitio(a, b):
    return normaliza(os.path.normpath(a)) == normaliza(os.path.normpath(b))


def archivos_de_estado(carpeta_nueva):
    """Los ficheros con rutas dentro, y en qué clave las llevan.

    resultado_lote.json vive dentro de la propia carpeta de salida, así que
    ya viajó con ella: se busca en la nueva, no en la vieja.
    """
    est = publisher.CARPETA_ESTADO
    return [
        (os.path.join(carpeta_nueva, "resultado_lote.json"), "completados"),
        (os.path.join(est, "publicados.json"), None),
        (os.path.join(est, "rechazados.json"), None),
        (os.path.join(est, "tiktok_subidos.json"), None),
    ]


def entradas(datos, clave):
    """La lista de dicts con 'ruta', venga suelta o dentro de una clave."""
    if clave:
        return datos.get(clave, []) if isinstance(datos, dict) else []
    return datos if isinstance(datos, list) else []


def revisar(ruta_json, clave, vieja, nueva):
    """(por cambiar, ya en su sitio, ajenas, sin archivo en destino)."""
    datos = publisher.cargar_json(ruta_json, None)
    if datos is None:
        return None
    cambian = ya = ajenas = huerfanas = 0
    for e in entradas(datos, clave):
        r = e.get("ruta")
        if not r:
            continue
        carpeta = os.path.dirname(r)
        if mismo_sitio(carpeta, nueva):
            ya += 1
        elif mismo_sitio(carpeta, vieja):
            cambian += 1
            if not os.path.exists(os.path.join(nueva, os.path.basename(r))):
                huerfanas += 1
        else:
            ajenas += 1
    return cambian, ya, ajenas, huerfanas


def reescribir(ruta_json, clave, vieja, nueva):
    datos = publisher.cargar_json(ruta_json, None)
    if datos is None:
        return 0
    hechos = 0
    for e in entradas(datos, clave):
        r = e.get("ruta")
        if not r:
            continue
        # El orden importa: si la carpeta ya es la nueva (segunda pasada, o
        # config ya cambiado a mano), "vieja" y "nueva" son la misma y sin
        # esto reescribiriamos rutas correctas, pisando el .bak bueno.
        if mismo_sitio(os.path.dirname(r), nueva):
            continue
        if mismo_sitio(os.path.dirname(r), vieja):
            e["ruta"] = os.path.join(nueva, os.path.basename(r))
            hechos += 1
    if hechos:
        shutil.copy2(ruta_json, ruta_json + ".bak")
        publisher.guardar_json(ruta_json, datos)
    return hechos


def main(argv=None):
    ap = argparse.ArgumentParser(description="Mueve la carpeta de salida sin romper el estado.")
    ap.add_argument("destino", help="La carpeta nueva, ya con los .mp4 dentro.")
    ap.add_argument("--si", action="store_true", help="Aplicarlo de verdad.")
    args = ap.parse_args(argv if argv is not None else sys.argv[1:])

    nueva = os.path.normpath(os.path.expanduser(args.destino))
    if not os.path.isdir(nueva):
        raise SystemExit(f"No existe esa carpeta: {nueva}\n"
                         "Muévela primero desde el gestor de archivos.")

    ruta_cfg = os.path.join(publisher.BASE_DIR, "config.json")
    cfg = publisher.cargar_config()
    vieja = os.path.normpath(cfg["carpeta_salida"])

    mp4s = glob.glob(os.path.join(nueva, "*.mp4"))
    print(f"\n  de:  {vieja}")
    print(f"  a:   {nueva}")
    print(f"       {len(mp4s)} .mp4 encontrados ahí dentro\n")

    if mismo_sitio(vieja, nueva):
        print("  El config ya apunta ahí. Reviso las rutas guardadas de todos modos.\n")

    total = huerfanas_total = 0
    for ruta_json, clave in archivos_de_estado(nueva):
        nombre = os.path.basename(ruta_json)
        r = revisar(ruta_json, clave, vieja, nueva)
        if r is None:
            print(f"   · {nombre:<24} no existe, nada que hacer")
            continue
        cambian, ya, ajenas, huerfanas = r
        total += cambian
        huerfanas_total += huerfanas
        detalle = [f"{cambian} por corregir"]
        if ya:
            detalle.append(f"{ya} ya bien")
        if ajenas:
            detalle.append(f"{ajenas} de otra carpeta")
        if huerfanas:
            detalle.append(f"⚠ {huerfanas} sin archivo en destino")
        print(f"   · {nombre:<24} {', '.join(detalle)}")

    if huerfanas_total:
        print(f"\n  ⚠ {huerfanas_total} ruta(s) apuntan a un .mp4 que no está en la carpeta")
        print("    nueva. Suele ser normal: son videos ya borrados por antigüedad.")

    if not args.si:
        print(f"\n  Esto era el listado. Para aplicarlo:\n"
              f'    python mover_salida.py "{nueva}" --si\n')
        return 0

    print()
    for ruta_json, clave in archivos_de_estado(nueva):
        hechos = reescribir(ruta_json, clave, vieja, nueva)
        if hechos:
            print(f"   ✓ {os.path.basename(ruta_json)}: {hechos} ruta(s) "
                  f"(copia en {os.path.basename(ruta_json)}.bak)")

    guardado = publisher.cargar_json(ruta_cfg, {})
    guardado["carpeta_salida"] = nueva
    publisher.guardar_json(ruta_cfg, guardado)
    print(f"   ✓ config.json: carpeta_salida = {nueva}")
    print(f"\n  ✓ Listo, {total} ruta(s) corregidas. Reinicia el panel para que lo lea.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
