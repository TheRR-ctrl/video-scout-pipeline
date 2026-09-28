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
    TIKTOK_CLIENT_KEY=aw...
    TIKTOK_CLIENT_SECRET=...

El archivo está en .gitignore: nunca se sube al repo.
"""
import os
import re

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RUTA_SECRETOS = os.path.join(BASE_DIR, "secretos.env")

CLAVES_CONOCIDAS = ("GEMINI_API_KEY", "JAMENDO_CLIENT_ID", "YOUTUBE_API_KEY",
                    "TIKTOK_CLIENT_KEY", "TIKTOK_CLIENT_SECRET",
                    "PEXELS_API_KEY", "PIXABAY_API_KEY")

# Las de arriba son las que el proyecto usa hoy, y salen en el panel aunque
# falten, con su explicación. Pero se puede añadir cualquier otra desde el
# panel sin tocar código: cargar() mete en el entorno TODO lo que haya en
# secretos.env, y claves_extra() las saca para que el panel también las pinte.
#
# El nombre tiene que valer como variable de entorno y, sobre todo, no puede
# traer un "=" ni un salto de línea: el archivo es NOMBRE=valor por línea, así
# que un nombre con cualquiera de los dos metería líneas que nadie escribió.
NOMBRE_VALIDO = re.compile(r"^[A-Z][A-Z0-9_]{2,63}$")

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
    if not NOMBRE_VALIDO.match(clave or ""):
        raise ValueError(
            "Nombre inválido. Solo MAYÚSCULAS, números y guion bajo, "
            "empezando por letra (ej. PIXABAY_API_KEY)."
        )
    valor = (valor or "").strip().strip('"').strip("'")
    if not valor:
        raise ValueError("El valor está vacío.")
    # Un salto de línea en el valor partiría la línea en dos y la segunda
    # mitad se leería como otra clave.
    if "\n" in valor or "\r" in valor:
        raise ValueError("El valor no puede tener saltos de línea.")

    lineas = []
    if os.path.exists(ruta):
        with open(ruta, "r", encoding="utf-8") as f:
            lineas = f.read().splitlines()

    # Se quitan TODAS las apariciones previas: si quedaran dos, cargar() usaría
    # la primera y el usuario estaría editando la que no manda.
    lineas = [ln for ln in lineas
              if ln.strip().partition("=")[0].strip() != clave]
    lineas.append(f"{clave}={valor}")

    # Atómico y en 600 desde antes de estar en su sitio: con el chmod después
    # había un instante con las claves legibles por cualquier app.
    import almacen
    almacen.escribir_texto(ruta, "\n".join(lineas).strip() + "\n", privado=True)

    os.environ[clave] = valor
    _DESDE_ARCHIVO.add(clave)
    return ruta


def claves_en_archivo(ruta=RUTA_SECRETOS):
    """Los nombres que hay escritos en secretos.env, en su orden."""
    nombres = []
    if not os.path.exists(ruta):
        return nombres
    try:
        with open(ruta, "r", encoding="utf-8") as f:
            for linea in f:
                linea = linea.strip()
                if not linea or linea.startswith("#") or "=" not in linea:
                    continue
                clave = linea.partition("=")[0].strip()
                if clave and clave not in nombres:
                    nombres.append(clave)
    except OSError:
        pass
    return nombres


def claves_extra(ruta=RUTA_SECRETOS):
    """Las añadidas a mano que el proyecto no trae de serie."""
    return [c for c in claves_en_archivo(ruta) if c not in CLAVES_CONOCIDAS]


def estado():
    """(clave, valor_presente, de_dónde) para cada clave conocida y para las
    que se hayan añadido después desde el panel."""
    out = []
    for c in tuple(CLAVES_CONOCIDAS) + tuple(claves_extra()):
        tiene = bool(os.environ.get(c))
        if not tiene:
            origen = ""
        elif c in _DESDE_ARCHIVO:
            origen = "secretos.env"
        else:
            origen = "el entorno"
        out.append((c, tiene, origen))
    return out


# Las credenciales de Google que aparecen en este proyecto se parecen entre si
# lo justo para pegarlas en el sitio equivocado, y guardar la equivocada no
# falla al escribir: falla mucho despues, con un error de la API que no
# menciona en ningun momento que ahi hay pegada otra cosa.
#
# Solo se rechaza lo que es reconociblemente OTRA credencial. Las claves en si
# no tienen un unico formato: las de siempre son "AIza" + 35 caracteres, y las
# nuevas de AI Studio, las que la consola muestra con "Bound account", empiezan
# por "AQ." y son mas largas. Cualquier forma que no se reconozca pasa con un
# aviso, no con un bloqueo: Google ya cambio el formato una vez y este archivo
# no puede ser lo que impida usar el siguiente.
_OTRAS_CREDENCIALES = (
    ("GOCSPX-", "Eso es un client secret de OAuth: va dentro de "
                "client_secret.json, no aquí."),
    ("ya29.", "Eso es un token de acceso de OAuth, no una clave de API."),
    ("4/0A", "Eso es un código de autorización de OAuth (de un solo uso, y "
             "caduca en minutos), no una clave de API."),
    ("{", "Eso es el contenido de un JSON. Si es el de OAuth, guárdalo como "
          "client_secret.json en la carpeta del proyecto."),
)

# Prefijos de clave que Google usa hoy. Estar aqui solo evita el aviso.
_PREFIJOS_CLAVE = ("AIza", "AQ.")


def revisar_clave_api(valor):
    """(severidad, mensaje) si `valor` no se ve como una clave, o None.

    severidad "error" = es otra credencial, no se guarda.
    severidad "aviso" = no la reconozco, pero se guarda igual y ya lo dirá
    la llamada de prueba.
    """
    valor = (valor or "").strip().strip('"').strip("'")
    if not valor:
        return ("error", "El valor está vacío.")
    for prefijo, motivo in _OTRAS_CREDENCIALES:
        if valor.startswith(prefijo):
            return ("error", motivo)
    if valor.endswith(".apps.googleusercontent.com"):
        return ("error", "Eso es un Client ID de OAuth: va dentro de "
                         "client_secret.json, no aquí.")
    if valor.startswith("AIza") and len(valor) != 39:
        return ("aviso", f"Una clave que empieza por 'AIza' tiene 39 caracteres "
                         f"y esta tiene {len(valor)}.")
    if not valor.startswith(_PREFIJOS_CLAVE):
        return ("aviso", "No reconozco esa forma de clave; la guardo igual y la "
                         "pruebo a ver.")
    return None


# Se carga al importar: así basta con `import secretos` en cada script.
cargar()
