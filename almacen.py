"""Leer y escribir los .json de estado sin perderlos a medias.

Todo el estado del proyecto vive en archivos json sueltos (publicados.json,
resultado_lote.json, tiktok_subidos.json…) y hay varios procesos mirándolos a
la vez: el pipeline escribe mientras el panel pinta. Dos reglas salen de ahí,
y antes estaban copiadas —a veces mal— en cinco archivos:

1. Escribir aparte y renombrar. Android mata procesos cuando le hace falta
   memoria, y un json cortado a la mitad se lee como corrupto. os.replace es
   atómico: o está el archivo viejo entero, o está el nuevo entero.

2. Distinguir «no existe» de «no se pudo leer». Para eso hay dos lectores,
   y la diferencia importa de verdad: si publicados.json se corrompe y se lee
   como lista vacía, el publisher cree que no ha subido nada y vuelve a subir
   el canal entero. Ahí conviene que reviente. En el panel, en cambio, un
   archivo ilegible es una tarjeta vacía y no una pantalla de error.
"""
import os
import json


def escribir_texto(ruta, texto, privado=False):
    """Escritura atómica de un archivo de texto. Crea la carpeta si hace falta.

    privado=True deja el archivo en 600 ANTES de ponerlo en su sitio: con un
    chmod después del os.replace habría un instante con la credencial legible
    por cualquier app.
    """
    carpeta = os.path.dirname(ruta)
    if carpeta:
        os.makedirs(carpeta, exist_ok=True)
    tmp = ruta + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(texto)
    if privado:
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
    os.replace(tmp, ruta)


def guardar(ruta, datos, privado=False):
    """Escritura atómica de un json."""
    escribir_texto(ruta, json.dumps(datos, ensure_ascii=False, indent=2), privado)


def cargar(ruta, por_defecto):
    """Para quien decide algo con esto: si no existe devuelve por_defecto, y
    si existe pero no se puede leer, revienta. Un registro ilegible no es un
    registro vacío."""
    if not os.path.exists(ruta):
        return por_defecto
    with open(ruta, "r", encoding="utf-8") as f:
        return json.load(f)


def leer(ruta, por_defecto):
    """Para quien solo lo va a enseñar: nunca revienta."""
    try:
        with open(ruta, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return por_defecto
