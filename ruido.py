"""Callar los avisos del SDK de Google que aquí no dicen nada.

El panel enseña la salida en vivo de lo que corre, y en una pantalla de
teléfono caben cuatro líneas. Cada aviso que no se puede accionar tapa el
paso que sí querías ver.
"""
import logging


def callar_sdk_google():
    """Quita la línea del cache de discovery de googleapiclient.

    file_cache: al construir el cliente de YouTube, googleapiclient intenta
    guardar en disco el documento de la API y no puede, porque ese cache
    solo existía en oauth2client<4.0.0, que ya nadie usa. Suelta "file_cache
    is only supported with oauth2client<4.0.0" y sigue sin cache, que es
    exactamente lo que queremos: una llamada más de red al arrancar y nada
    que se quede viejo. No hay nada que arreglar, así que no hay nada que
    avisar.

    Se silencia solo ese logger, y solo por debajo de ERROR: si algún día la
    llamada falla de verdad, el error se sigue viendo.

    Aquí había una segunda línea, para el logger `google_genai.models`: el
    SDK anunciaba en cada llamada "AFC is enabled with max remote calls" y
    recomendaba Chat.send_message, un consejo que no aplicaba porque ninguna
    llamada de este proyecto pasa `tools=`. Se fue con el SDK: `gemini.py`
    habla por REST y no dice nada que no le pregunten.
    """
    logging.getLogger("googleapiclient.discovery_cache").setLevel(logging.ERROR)
