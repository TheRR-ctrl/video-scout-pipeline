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
import re
import unicodedata
from datetime import datetime, timedelta

import almacen   # leer y escribir los .json de estado

CARPETA_ESTADO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pipeline_state")
RUTA_CANDIDATOS = os.path.join(CARPETA_ESTADO, "candidatos.json")
RUTA_HISTORIAL = os.path.join(CARPETA_ESTADO, "historial_vistos.json")

# Un candidato que falla una y otra vez (texto que Gemini rechaza, por
# ejemplo) bloquearía la cola para siempre. Tras estos intentos se descarta.
MAX_INTENTOS = 3

# La cola no puede crecer sin fin. Los buscadores agregan en cada corrida y
# script_writer solo drena lo que la cuota diaria de Gemini deja escribir, así
# que entra más de lo que sale: 145 candidatos esperando, los más viejos de
# hace semanas, y la cuota del día gastándose en ellos por orden de llegada.
#
# Dos frenos. Caducidad: un post de Reddit de hace dos semanas ya no es lo que
# se está contando, y escribirlo hoy es gastar cuota en algo pasado. Y un
# tope: por encima de esto la cola deja de ser trabajo pendiente y pasa a ser
# un archivo que nunca se va a atender.
FRESCURA_MAXIMA_DIAS = 14
TOPE_CANDIDATOS = 60


# Huellas de las historias ya escritas, para no contar dos veces la misma.
RUTA_HUELLAS = os.path.join(CARPETA_ESTADO, "huellas_historias.json")

# Cuánto se tienen que solapar dos textos para darlos por la misma historia.
#
# Medido sobre los 1128 pares posibles de los 48 títulos publicados: el fondo
# tiene mediana 0.00 y percentil 99 de 0.20, y los duplicados reales del canal
# («Creé los Illuminati» / «Inventé a los Illuminati») dan 0.40. Pero hay un
# par de historias DISTINTAS que llega a 0.38 solo por compartir palabras
# corrientes, así que en la franja 0.35-0.45 no se puede decidir.
#
# De ahí 0.55, y de ahí lo que este filtro sí y no hace. Atrapa textos casi
# iguales: el mismo candidato escrito dos veces, dos segmentos de la misma
# transcripción que se pisan, un repost literal. NO atrapa un repost
# reescrito con otras palabras — dos versiones de un mismo caso contadas de
# cero solapan ~0.3, por debajo de historias distintas que colisionan por
# vocabulario. Para eso no hay umbral posible con esta medida, y forzarlo
# descartaría historias buenas en silencio.
PARECIDO_MINIMO = 0.55

# Palabras que aparecen en todas las historias del canal y no distinguen
# ninguna. Sin quitarlas, dos relatos cualesquiera ya comparten un tercio de
# su vocabulario y el umbral no separa nada.
VACIAS = {
    "a", "al", "algo", "ahora", "ante", "antes", "aquel", "aqui", "asi", "aun",
    "cada", "como", "con", "cuando", "de", "del", "desde", "donde", "dos", "el",
    "ella", "ellas", "ellos", "en", "entre", "era", "eran", "eres", "es", "esa",
    "ese", "eso", "esta", "estaba", "este", "esto", "fue", "fui", "ha", "habia",
    "hasta", "hay", "hizo", "la", "las", "le", "les", "lo", "los", "mas", "me",
    "mi", "mis", "mucho", "muy", "nada", "ni", "no", "nos", "o", "para", "pero",
    "por", "porque", "que", "se", "ser", "si", "sin", "sobre", "solo", "son",
    "su", "sus", "tan", "te", "tenia", "the", "ti", "tu", "tus", "un", "una",
    "uno", "y", "ya", "yo",
}


def huella(texto):
    """El conjunto de palabras con las que se compara una historia.

    Sin acentos, sin signos, sin palabras vacías y sin las muy cortas: dos
    versiones del mismo relato se escriben distinto pero nombran las mismas
    cosas, y son esos sustantivos los que las delatan.
    """
    t = unicodedata.normalize("NFKD", (texto or "").lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    palabras = re.findall(r"[a-z0-9ñ]+", t)
    return {p for p in palabras if len(p) > 3 and p not in VACIAS}


def parecido(a, b):
    """Cuánto se solapa la más corta de dos huellas con la otra, de 0 a 1.

    Contención y no Jaccard: los textos que hay que comparar tienen largos muy
    distintos —un título contra un cuerpo de 900 palabras, un post recortado
    contra el original entero— y Jaccard castiga esa diferencia tanto que un
    texto contenido palabra por palabra dentro de otro más largo apenas pasa
    de 0.1. Lo que interesa aquí es justo eso: si lo que trae el candidato ya
    está dicho en algo que se contó.
    """
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def cargar_huellas():
    datos = _leer_json(RUTA_HUELLAS, [])
    return [set(h) for h in datos if isinstance(h, list)]


def guardar_huella(texto):
    """Apunta una historia como ya contada."""
    h = huella(texto)
    if not h:
        return
    datos = _leer_json(RUTA_HUELLAS, [])
    datos.append(sorted(h))
    # Con el tiempo esto crece, y comparar contra miles de huellas en cada
    # historia nueva sale caro. Las últimas 400 cubren meses de canal, que es
    # mucho más de lo que un espectador recuerda.
    _escribir_json(RUTA_HUELLAS, datos[-400:])


def ya_contada(texto, huellas=None):
    """Si esta historia se parece demasiado a una ya escrita.

    Devuelve el parecido con la más parecida, o 0.0 si ninguna llega al
    umbral, para poder decir en el log cuánto se parecía.
    """
    h = huella(texto)
    if not h:
        return 0.0
    peor = max((parecido(h, vieja) for vieja in (huellas if huellas is not None else cargar_huellas())),
               default=0.0)
    return peor if peor >= PARECIDO_MINIMO else 0.0


_leer_json = almacen.leer
_escribir_json = almacen.guardar


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


def _ahora():
    return datetime.now().replace(microsecond=0)


def fecha_de(candidato):
    """Cuándo se vio este candidato. Los de antes de que existiera el sello no
    tienen fecha: se tratan como recién llegados en vez de como caducados, que
    los borraría todos de golpe la primera vez que corra esto."""
    try:
        return datetime.fromisoformat(str(candidato.get("visto_en") or ""))
    except ValueError:
        return None


def por_frescura(candidatos):
    """Los más recientes primero.

    La cola es por orden de llegada, así que la cuota del día se gastaba en lo
    más viejo — lo que menos posibilidades tiene de funcionar ya. Sin fecha van
    al final, que es donde estaban.
    """
    viejisimo = datetime.min
    return sorted(candidatos, key=lambda c: fecha_de(c) or viejisimo, reverse=True)


def podar(candidatos, ahora=None):
    """Quita lo caducado y lo que sobra del tope. Devuelve (quedan, fuera).

    El tope se aplica sobre la frescura, no sobre el orden del archivo: si hay
    que dejar fuera a alguien, que sea al más viejo.
    """
    ahora = ahora or _ahora()
    limite = ahora - timedelta(days=FRESCURA_MAXIMA_DIAS)
    frescos, fuera = [], []
    for c in candidatos:
        fecha = fecha_de(c)
        (fuera if (fecha and fecha < limite) else frescos).append(c)

    frescos = por_frescura(frescos)
    if len(frescos) > TOPE_CANDIDATOS:
        fuera.extend(frescos[TOPE_CANDIDATOS:])
        frescos = frescos[:TOPE_CANDIDATOS]
    return frescos, fuera


def agregar_candidatos(nuevos):
    """Mezcla candidatos nuevos con los que quedaban pendientes, sin duplicar
    ni perder los viejos, y poda lo caducado y lo que pase del tope.

    Devuelve (total_en_cola, cuantos_se_agregaron, repetidos, podados)."""
    pendientes = cargar_pendientes()
    conocidos = {c.get("id") for c in pendientes}

    # El id no basta. Un relato reposteado en otro subreddit, o el mismo caso
    # contado por dos usuarios, son ids distintos y pasaban los dos: el canal
    # acabó con «Creé los Illuminati» y «Inventé a los Illuminati» subidos por
    # separado, y los dos se quedaron en 10 vistas. Así que se compara también
    # el texto, contra la cola y contra lo ya escrito.
    huellas = cargar_huellas() + [huella(_texto_de(c)) for c in pendientes]

    agregados = []
    repetidos = 0
    for c in nuevos:
        if not c.get("id") or c["id"] in conocidos:
            continue
        cuanto = ya_contada(_texto_de(c), huellas)
        if cuanto:
            repetidos += 1
            continue
        conocidos.add(c["id"])
        huellas.append(huella(_texto_de(c)))
        c.setdefault("visto_en", _ahora().isoformat())
        agregados.append(c)

    # Los que ya estaban sin sello lo reciben ahora: sin fecha no se puede
    # decidir si caducaron, y darlos por viejos borraría la cola entera.
    ahora = _ahora().isoformat()
    for c in pendientes:
        c.setdefault("visto_en", ahora)

    cola, fuera = podar(pendientes + agregados)
    guardar_pendientes(cola)
    # Lo podado se marca como visto: si no, el siguiente escaneo lo volvería a
    # traer y la poda se repetiría en cada corrida sin avanzar nada.
    marcar_vistos([c.get("id") for c in fuera if c.get("id")])
    return len(cola), len(agregados), repetidos, len(fuera)


def _texto_de(candidato):
    """El texto con el que se identifica un candidato de la cola."""
    return " ".join(str(candidato.get(k) or "") for k in ("titulo_original", "texto_original"))


def ids_en_cola():
    return {c.get("id") for c in cargar_pendientes() if c.get("id")}
