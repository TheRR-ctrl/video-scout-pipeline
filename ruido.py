"""Callar los avisos del SDK de Google que aquí no dicen nada.

El panel enseña la salida en vivo de lo que corre, y en una pantalla de
teléfono caben cuatro líneas. Cada aviso que no se puede accionar tapa el
paso que sí querías ver.
"""
import logging


def callar_sdk_google():
    """Quita las líneas de AFC de google-genai y la del cache de discovery.

    AFC: en cada llamada, el SDK anuncia "AFC is enabled with max remote
    calls" y avisa una vez de que usar AFC (llamadas automáticas a funciones)
    desde Models.generate_content no es lo recomendado, que mejor
    Chat.send_message.

    Aquí ninguna llamada pasa `tools=`, así que no hay función que llamar y
    el consejo no aplica: el SDK lo suelta igual porque no distingue.

    file_cache: al construir el cliente de YouTube, googleapiclient intenta
    guardar en disco el documento de la API y no puede, porque ese cache
    solo existía en oauth2client<4.0.0, que ya nadie usa. Suelta "file_cache
    is only supported with oauth2client<4.0.0" y sigue sin cache, que es
    exactamente lo que queremos: una llamada más de red al arrancar y nada
    que se quede viejo. No hay nada que arreglar, así que no hay nada que
    avisar.

    En los dos casos se silencia solo ese logger, y solo por debajo de
    ERROR: si algún día la llamada falla de verdad, el error se sigue viendo.
    """
    logging.getLogger("google_genai.models").setLevel(logging.ERROR)
    logging.getLogger("googleapiclient.discovery_cache").setLevel(logging.ERROR)
