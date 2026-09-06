"""Callar los avisos del SDK de Google que aquí no dicen nada.

El panel enseña la salida en vivo de lo que corre, y en una pantalla de
teléfono caben cuatro líneas. Cada aviso que no se puede accionar tapa el
paso que sí querías ver.
"""
import logging


def callar_sdk_google():
    """Quita las líneas de AFC de google-genai.

    En cada llamada, el SDK anuncia "AFC is enabled with max remote calls" y
    avisa una vez de que usar AFC (llamadas automáticas a funciones) desde
    Models.generate_content no es lo recomendado, que mejor Chat.send_message.

    Aquí ninguna llamada pasa `tools=`, así que no hay función que llamar y
    el consejo no aplica: el SDK lo suelta igual porque no distingue. Se
    silencia solo ese logger, y solo por debajo de ERROR: si algún día
    generate_content falla de verdad, el error se sigue viendo.
    """
    logging.getLogger("google_genai.models").setLevel(logging.ERROR)
