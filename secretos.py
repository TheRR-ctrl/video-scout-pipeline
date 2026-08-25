"""
Carga las claves desde secretos.env si no están ya en el entorno.

Por qué existe: GEMINI_API_KEY y JAMENDO_CLIENT_ID eran solo variables de
entorno, así que vivían únicamente en la sesión de terminal donde se
escribió el `export`. Al abrir una pestaña nueva de Termux, reiniciar el
teléfono o correr desde cron, desaparecían — y el pipeline seguía adelante
usando la metadata de respaldo sin que nada dijera por qué.

Guardarlas en un archivo hace que sobrevivan a todo eso. El entorno sigue
teniendo prioridad, para poder pisar un valor puntualmente sin editar nada:

    GEMINI_API_KEY=otra python script_writer.py

Formato de secretos.env (una por línea, se ignoran comentarios y comillas):

    GEMINI_API_KEY=AIza...
    JAMENDO_CLIENT_ID=abc123

El archivo está en .gitignore: nunca se sube al repo.
"""
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RUTA_SECRETOS = os.path.join(BASE_DIR, "secretos.env")

CLAVES_CONOCIDAS = ("GEMINI_API_KEY", "JAMENDO_CLIENT_ID")

# Qué claves acabaron viniendo del archivo. Se registra al cargar, porque
# después no hay forma de saberlo: en os.environ ya no se distingue el
# origen, y decir "desde secretos.env" cuando en realidad ganó el entorno
# haría que el diagnóstico apuntara al lugar equivocado.
_DESDE_ARCHIVO = set()


def cargar(ruta=RUTA_SECRETOS):
    """Mete en os.environ lo que falte. Devuelve las claves que cargó."""
    if not os.path.exists(ruta):
        return []

    cargadas = []
    try:
        with open(ruta, "r", encoding="utf-8") as f:
            for linea in f:
                linea = linea.strip()
                if not linea or linea.startswith("#") or "=" not in linea:
                    continue
                clave, _, valor = linea.partition("=")
                clave = clave.strip()
                valor = valor.strip().strip('"').strip("'")
                if not clave or not valor:
                    continue
                # El entorno manda: si ya está definida, no se pisa.
                if not os.environ.get(clave):
                    os.environ[clave] = valor
                    _DESDE_ARCHIVO.add(clave)
                    cargadas.append(clave)
    except Exception as exc:
        print(f"⚠️ No se pudo leer {ruta}: {exc}")
    return cargadas


def estado():
    """(clave, valor_presente, de_dónde) para cada clave conocida."""
    out = []
    for c in CLAVES_CONOCIDAS:
        tiene = bool(os.environ.get(c))
        if not tiene:
            origen = ""
        elif c in _DESDE_ARCHIVO:
            origen = "secretos.env"
        else:
            origen = "el entorno"
        out.append((c, tiene, origen))
    return out


# Se carga al importar: así basta con `import secretos` en cada script.
cargar()
