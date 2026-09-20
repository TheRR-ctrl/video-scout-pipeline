#!/usr/bin/env python3
"""Sube la versión fijada del CLI de HyperFrames en `hyperframes_nucleo.py`.

    python3 subir_version_hyperframes.py 0.8.29 [ruta/al/repo ...]
    python3 subir_version_hyperframes.py 0.8.29 --nota "arregla X" ruta ruta

Sin rutas, trabaja sobre el directorio actual.

## Por qué existe un script para cambiar una línea

Porque no es una línea, son tres cosas que se olvidan juntas:

1. **`hyperframes_nucleo.py` es idéntico byte a byte en este repo y en
   `video_generation`.** Cambiarlo en uno solo los separa otra vez, que es
   justo el problema que ese archivo vino a resolver. Por eso acepta varias
   rutas: se le pasan los dos repos de una vez.
2. **El comentario de encima explica por qué está fijada esa versión.** Si se
   cambia el número y no el comentario, el archivo queda contradiciéndose y
   dentro de unos meses no se sabe cuál manda. Aquí se reescriben los dos.

   Ese comentario se **sustituye**, no se acumula: la razón de la versión
   vieja deja de aplicar en cuanto la versión cambia. Pero es información
   real —la de 0.8.27 eran cuatro corridas de Actions con sus IDs— así que
   pásale `--nota` con el motivo nuevo. Sin `--nota` el texto queda genérico
   y remite a `git log -p hyperframes_nucleo.py`, que es donde sigue estando
   el anterior.
3. **Este motor es de PC.** Desde el teléfono no se puede comprobar que la
   versión nueva renderiza, así que el comentario que deja escrito lo recuerda:
   pruébala con el workflow de fondos antes de que llegue a main.

Después de correrlo, confirma que los dos repos siguen iguales:

    md5sum ruta/al/repo1/hyperframes_nucleo.py ruta/al/repo2/hyperframes_nucleo.py

Y ojo con la caché: la clave de un clip no lleva la versión del CLI dentro, así
que los clips ya renderizados se siguen sirviendo. Si subes de versión para
arreglar un render feo, vacía también `pipeline_state/hyperframes_cache/`.
"""
import os
import re
import sys
import datetime
import textwrap

CABECERA = '''# Versión fijada del CLI: HyperFrames se mueve rápido y una corrida desatendida
# no debería cambiar de motor de render sin que lo decidas. Súbela a mano, en
# los dos repos a la vez.
#
# Fijada a {version} el {fecha}. Este motor es de PC: desde el teléfono no se
# puede probar, así que compruébalo con el workflow de fondos antes de que
# llegue a main.'''

# Sin --nota: se dice dónde quedó la razón anterior, que se acaba de sustituir.
NOTA_POR_DEFECTO = ("El motivo de la versión anterior está en "
                    "`git log -p hyperframes_nucleo.py`.")


def _comentario(version, fecha, nota):
    """El bloque entero: cabecera fija + el motivo, envuelto a 79 columnas."""
    lineas = CABECERA.format(version=version, fecha=fecha).split("\n")
    lineas.append("#")
    lineas += ["# " + t for t in textwrap.wrap(nota, 76)]
    lineas.append(f'VERSION_CLI = "{version}"')
    return "\n".join(lineas)

# Desde el comentario hasta la asignación, todo de una pieza: así no se puede
# cambiar el número dejando atrás la explicación.
BLOQUE = re.compile(
    r'^# Versi.n fijada del CLI:.*?^VERSION_CLI\s*=\s*["\'][^"\']+["\']',
    re.MULTILINE | re.DOTALL,
)


def subir(ruta, version, fecha, nota):
    """Cambia la versión en un repo. Devuelve qué pasó, en una línea.

    Si algo no cuadra no toca nada: vale más no hacer el cambio que hacerlo a
    medias en uno de los dos repos."""
    archivo = os.path.join(ruta, "hyperframes_nucleo.py")
    if not os.path.isfile(archivo):
        return f"NO ESTÁ: {archivo}"

    with open(archivo, encoding="utf-8") as f:
        texto = f.read()

    actual = re.search(r'^VERSION_CLI\s*=\s*["\']([^"\']+)["\']', texto, re.MULTILINE)
    if not actual:
        return f"sin VERSION_CLI: {archivo}"
    if actual.group(1) == version:
        return f"ya estaba en {version}: {ruta}"

    nuevo, n = BLOQUE.subn(
        lambda _: _comentario(version, fecha, nota), texto, count=1
    )
    if n != 1:
        return f"el bloque no coincide, revísalo a mano: {archivo}"

    # tmp + os.replace, la misma regla que `almacen.guardar`: si Android mata el
    # proceso a media escritura, lo que queda es un .tmp que nadie lee, no un
    # módulo truncado que ya no importa.
    parcial = archivo + ".tmp"
    with open(parcial, "w", encoding="utf-8") as f:
        f.write(nuevo)
    os.replace(parcial, archivo)
    return f"{actual.group(1)} -> {version}: {ruta}"


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    version = sys.argv[1]
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise SystemExit(f"'{version}' no parece una versión (X.Y.Z)")
    resto = sys.argv[2:]
    nota = NOTA_POR_DEFECTO
    if "--nota" in resto:
        i = resto.index("--nota")
        if i + 1 >= len(resto):
            raise SystemExit("--nota necesita un texto detrás")
        nota = resto[i + 1]
        resto = resto[:i] + resto[i + 2:]

    fecha = datetime.date.today().isoformat()
    for ruta in resto or ["."]:
        print(subir(ruta, version, fecha, nota))


if __name__ == "__main__":
    main()
