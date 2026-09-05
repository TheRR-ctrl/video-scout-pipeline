"""
Cola de candidatos compartida entre trend_scout.py y script_writer.py.

Sin dependencias externas a propósito: los dos scripts la importan y ninguno
tiene que arrastrar las dependencias del otro (requests / google-genai).

El reparto de responsabilidades es lo importante:

  - trend_scout AGREGA candidatos a candidatos.json. Nunca borra los que ya
    estaban ahí sin consumir, y nunca marca nada como visto.
  - script_writer CONSUME: cuando una historia se escribe con éxito, ese id
    pasa a historial_vistos.json y sale de candidatos.json.

Antes el historial se escribía al escanear, así que un post se quemaba nada
más verlo: si Gemini fallaba (sin API key, cuota agotada, corte de red), esa
historia quedaba marcada como vista para siempre y no volvía a aparecer.
Encima, un escaneo que no encontraba nada nuevo sobrescribía candidatos.json
con [] y borraba los candidatos que aún no se habían usado. El resultado era
el que se veía desde fuera: el script deja de generar historias nuevas.
"""
import os
import json

CARPETA_ESTADO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pipeline_state")
RUTA_CANDIDATOS = os.path.join(CARPETA_ESTADO, "candidatos.json")
RUTA_HISTORIAL = os.path.join(CARPETA_ESTADO, "historial_vistos.json")

# Un candidato que falla una y otra vez (texto que Gemini rechaza, por
# ejemplo) bloquearía la cola para siempre. Tras estos intentos se descarta.
MAX_INTENTOS = 3


def _leer_json(ruta, por_defecto):
    if not os.path.exists(ruta):
        return por_defecto
    try:
        with open(ruta, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return por_defecto


def _escribir_json(ruta, datos):
    """Escritura atómica: el panel y los otros scripts leen estos archivos en
    cualquier momento, y un archivo a medio escribir se lee como corrupto."""
    os.makedirs(CARPETA_ESTADO, exist_ok=True)
    tmp = ruta + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(datos, f, ensure_ascii=False, indent=2)
    os.replace(tmp, ruta)


def cargar_pendientes():
    datos = _leer_json(RUTA_CANDIDATOS, [])
    return datos if isinstance(datos, list) else []


def guardar_pendientes(candidatos):
    _escribir_json(RUTA_CANDIDATOS, candidatos)


def cargar_historial():
    datos = _leer_json(RUTA_HISTORIAL, [])
    return set(datos) if isinstance(datos, list) else set()


def guardar_historial(vistos):
    _escribir_json(RUTA_HISTORIAL, sorted(vistos))


def marcar_vistos(ids):
    """Marca ids como consumidos. Lo llama script_writer, no el scout."""
    ids = set(ids)
    if not ids:
        return 0
    vistos = cargar_historial()
    nuevos = ids - vistos
    guardar_historial(vistos | ids)
    return len(nuevos)


def agregar_candidatos(nuevos):
    """Mezcla candidatos nuevos con los que quedaban pendientes, sin duplicar
    ni perder los viejos. Devuelve (total_en_cola, cuantos_se_agregaron)."""
    pendientes = cargar_pendientes()
    conocidos = {c.get("id") for c in pendientes}
    agregados = [c for c in nuevos if c.get("id") and c["id"] not in conocidos]
    cola = pendientes + agregados
    guardar_pendientes(cola)
    return len(cola), len(agregados)


def ids_en_cola():
    return {c.get("id") for c in cargar_pendientes() if c.get("id")}
