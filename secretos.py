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
    YOUTUBE_API_KEY=AIza...

El archivo está en .gitignore: nunca se sube al repo.
"""
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RUTA_SECRETOS = os.path.join(BASE_DIR, "secretos.env")

CLAVES_CONOCIDAS = ("GEMINI_API_KEY", "JAMENDO_CLIENT_ID", "YOUTUBE_API_KEY")

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


def guardar(clave, valor, ruta=RUTA_SECRETOS):
    """Escribe o reemplaza una clave en secretos.env, sin duplicar líneas.

    Existe porque la alternativa desde el teléfono era encadenar sed y echo con
    comillas, y el teclado de Android convierte las comillas rectas en
    tipográficas al pegar: el sed falla, el echo no, y uno acaba con la clave
    vieja intacta y un mensaje de error que no dice eso.
    """
    valor = (valor or "").strip().strip('"').strip("'")
    if not valor:
        raise ValueError("El valor está vacío.")

    lineas = []
    if os.path.exists(ruta):
        with open(ruta, "r", encoding="utf-8") as f:
            lineas = f.read().splitlines()

    # Se quitan TODAS las apariciones previas: si quedaran dos, cargar() usaría
    # la primera y el usuario estaría editando la que no manda.
    lineas = [ln for ln in lineas
              if ln.strip().partition("=")[0].strip() != clave]
    lineas.append(f"{clave}={valor}")

    tmp = ruta + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(lineas).strip() + "\n")
    os.replace(tmp, ruta)
    try:
        os.chmod(ruta, 0o600)  # el archivo guarda credenciales
    except OSError:
        pass

    os.environ[clave] = valor
    _DESDE_ARCHIVO.add(clave)
    return ruta


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


# Las cuatro credenciales de Google que aparecen en este proyecto se parecen
# lo bastante como para confundirlas, y solo una sirve como clave de API.
# Guardar la equivocada no falla al escribir: falla mucho despues, con un
# error de la API que no menciona en ningun momento que el problema es que
# ahi hay pegada otra cosa.
_OTRAS_CREDENCIALES = (
    ("AQ.", "Eso es un código de autorización de OAuth (de un solo uso, y "
            "caduca en minutos), no una clave de API."),
    ("GOCSPX-", "Eso es un client secret de OAuth: va dentro de "
                "client_secret.json, no aquí."),
    ("ya29.", "Eso es un token de acceso de OAuth, no una clave de API."),
    ("{", "Eso es el contenido de un JSON. Si es el de OAuth, guárdalo como "
          "client_secret.json en la carpeta del proyecto."),
)


def revisar_clave_api(valor):
    """Devuelve por qué `valor` no es una clave de API de Google, o None."""
    valor = (valor or "").strip().strip('"').strip("'")
    if not valor:
        return "El valor está vacío."
    for prefijo, motivo in _OTRAS_CREDENCIALES:
        if valor.startswith(prefijo):
            return motivo
    if valor.endswith(".apps.googleusercontent.com"):
        return ("Eso es un Client ID de OAuth: va dentro de client_secret.json, "
                "no aquí.")
    if not valor.startswith("AIza"):
        # i mayuscula, no L: en la mayoria de fuentes de movil se ven igual.
        return ("Una clave de API de Google empieza por 'AIza' (con i mayúscula) "
                "y tiene 39 caracteres.")
    if len(valor) < 35 or len(valor) > 45:
        return f"Eso tiene {len(valor)} caracteres; una clave tiene 39."
    return None


# Se carga al importar: así basta con `import secretos` en cada script.
cargar()
