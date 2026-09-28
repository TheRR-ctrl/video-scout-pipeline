"""
Puente entre la app y el pipeline: lo que Java llama para levantar el panel.

Es deliberadamente corto. Todo lo que hace es dejar el proceso en las mismas
condiciones en las que el pipeline se ejecuta en Termux —un directorio de
trabajo escribible, ffmpeg en el PATH, un HOME— y a partir de ahí importa
`servidor` sin tocarlo. Cuanto menos sepa este archivo del pipeline, menos
hay que cambiarlo cuando el pipeline cambie.
"""
import os
import sys


def iniciar(dir_base, dir_bin, puerto=8770):
    """Arranca el panel. No vuelve: Flask se queda escuchando.

    Lo llama ServicioPanel en su propio hilo.
    """
    # ffmpeg vive en el directorio de librerías del APK y llega aquí a
    # través de un enlace en dir_bin. Sin esto, las 31 llamadas del
    # pipeline a "ffmpeg" no encontrarían nada.
    os.environ["PATH"] = dir_bin + os.pathsep + os.environ.get("PATH", "")

    # Varias librerías (y el propio edge-tts) esperan un HOME donde escribir
    # cachés. En Android no hay uno por omisión.
    #
    # Se asigna, no se hace setdefault: si algo dejó HOME apuntando a una
    # ruta que no existe o donde no se puede escribir —que en Android es lo
    # normal— setdefault la respetaría, y el fallo aparecería mucho después,
    # dentro de una librería, en mitad de una tanda.
    os.environ["HOME"] = dir_base
    os.environ["TMPDIR"] = os.path.join(dir_base, "tmp")
    os.makedirs(os.environ["TMPDIR"], exist_ok=True)

    # Esto es lo que hace que no haya que tocar los veinte módulos que
    # calculan su BASE_DIR con os.path.dirname(__file__): el pipeline se
    # importa desde una carpeta escribible, así que su BASE_DIR lo es.
    os.chdir(dir_base)
    if dir_base not in sys.path:
        sys.path.insert(0, dir_base)

    import servidor

    # app.run y no servidor.main(): main() pide el wake lock de Termux,
    # abre el navegador y arma el vigilante que apaga el servidor cuando
    # nadie mira. Aquí de eso se encarga el servicio en primer plano, que
    # es el mecanismo que Android entiende.
    servidor.app.run(host="127.0.0.1", port=puerto, threaded=True)


def version_python():
    """Para que la app pueda enseñar qué runtime lleva dentro."""
    return sys.version
