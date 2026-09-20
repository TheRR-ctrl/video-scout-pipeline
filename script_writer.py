"""
Script Writer — convierte pipeline_state/candidatos.json (salida de trend_scout.py)
en guion.txt, el formato que ya consume generar_video_maestro.py.

Usa la API de Gemini (capa gratuita) para reescribir cada historia con un hook
fuerte en las primeras líneas y marcar Genero:/Emocion: explícitos.

Requiere: pip install -U google-genai
Credenciales: variable de entorno GEMINI_API_KEY (gratis en https://aistudio.google.com/apikey).
"""
import os
import sys
import json
import logging
import time
import argparse

import secretos  # carga secretos.env si las claves no están en el entorno
import ruido     # calla los avisos del SDK de Google que aqui no dicen nada
import narrador  # comprobar que el género declarado casa con el texto escrito
import cola      # cola de candidatos e historial compartidos con trend_scout.py

from google import genai
from google.genai import types as genai_types

CARPETA_ESTADO = cola.CARPETA_ESTADO
RUTA_CANDIDATOS = cola.RUTA_CANDIDATOS
RUTA_GUION = os.path.join(os.path.dirname(os.path.abspath(__file__)), "guion.txt")

MODEL = "gemini-3.6-flash"

# Esperas entre reintentos cuando Gemini contesta 503. Cortas: el pico de
# demanda suele durar segundos, y la corrida entera espera aquí.
ESPERAS_SOBRECARGA_S = (5, 15, 40)
EMOCIONES_VALIDAS = ["venganza", "suspenso", "drama", "comedia"]

# A propósito no hay objetivo de longitud, ni mínimo ni máximo: forzar el
# largo del guion (antes se pedían 700-900 palabras si el original traía 400+)
# hacía que unas historias se inflaran con relleno y otras se quedaran cortas
# a media tensión, en los dos casos empeorando la narración. Cada historia se
# escribe con el largo que pide, y el formato se decide DESPUÉS: en
# generar_video_maestro.py, es_short sale de la duración real del audio ya
# generado (dur_sec <= duracion_max_short_sec), no de una cuota de palabras.

# Qué temas reparte peor el feed de Shorts. Medido en el canal: en el lote del
# 11/09, publicado entero en diez minutos, los de maltrato infantil, intento de
# filicidio, violencia y conspiración sacaron entre 2 y 19 vistas mientras los
# pleitos domésticos del MISMO lote sacaban entre 337 y 1640. No hay nada entre
# 60 y 300 vistas en todo el canal, y un hueco así no lo hace el público —que
# vota en una escala continua— sino el reparto, que es binario.
#
# No es un juicio sobre las historias: están igual de bien escritas que las de
# mil vistas. Es que renderizarlas y subirlas es trabajo del teléfono que no va
# a ver nadie.
TEMAS = [
    "cotidiano",          # pleitos de familia, pareja, trabajo, vecinos, dinero
    "paranormal",         # sustos, casas embrujadas, apariciones
    "maltrato_infantil",
    "suicidio_autolesion",
    "violencia_grave",    # agresiones, atentados, crimen violento
    "contenido_sexual",
    "salud_mental",       # ansiedad, depresión, diagnósticos como eje del relato
    "conspiracion",
    "drogas",
]

# Los que no se escriben. salud_mental y drogas se quedan fuera de la lista a
# propósito: el único dato que tengo es un video de ansiedad con 7 vistas, y
# bloquear el tema entero se llevaría media cola de dramas de pareja. Si
# quieres apretar más, añádelos en config.json → temas_bloqueados.
TEMAS_BLOQUEADOS_DEFECTO = [
    "maltrato_infantil",
    "suicidio_autolesion",
    "violencia_grave",
    "contenido_sexual",
    "conspiracion",
]

SCHEMA_HISTORIA = {
    "type": "object",
    "properties": {
        "titulo_hook": {
            "type": "string",
            "description": "Título/hook de 1-2 frases cortas para los primeros 3 segundos del video. Debe generar curiosidad inmediata.",
        },
        "genero_narrador": {
            "type": "string",
            "enum": ["masculino", "femenino"],
            "description": (
                "Género de quien narra en primera persona, TAL COMO lo escribiste en "
                "el cuerpo. Léete tu propio texto antes de responder: si ahí dice "
                "«me quedé callada» o «yo era la esposa», es femenino; si dice "
                "«me quedé callado» o «soy el hijo», es masculino. Este campo elige "
                "la voz que narra el video, así que equivocarlo hace que toda la "
                "historia se escuche con la voz del género contrario."
            ),
        },
        "emocion": {
            "type": "string",
            "enum": EMOCIONES_VALIDAS,
        },
        "tema": {
            "type": "string",
            "enum": TEMAS,
            "description": (
                "De qué trata el NÚCLEO de la historia, no un detalle de paso. "
                "Si el eje es un pleito de familia, pareja, trabajo, vecinos o "
                "dinero, es «cotidiano», aunque alguien grite o llore. Reserva "
                "las otras etiquetas para cuando el tema ES eso: «maltrato_infantil» "
                "si lo que se cuenta es el daño a un niño, «suicidio_autolesion» si "
                "alguien intenta quitarse la vida o quitársela a otro, "
                "«violencia_grave» para agresiones o crímenes violentos, "
                "«conspiracion» para sociedades secretas y teorías, «salud_mental» "
                "si un diagnóstico es el eje del relato. Esta etiqueta decide si la "
                "historia se publica, así que no la adornes ni la suavices."
            ),
        },
        "cuerpo": {
            "type": "string",
            "description": (
                "Historia reescrita en primera persona, narrativa, ritmo natural para "
                "narración en voz alta, cerrando con un gancho para comentarios. Sin "
                "límite de longitud: tan larga o tan corta como la historia necesite "
                "para contarse bien."
            ),
        },
        "se_sostiene": {
            "type": "boolean",
            "description": (
                "true si la historia que ACABAS de escribir tiene situación, "
                "desarrollo y un final —aunque el final sea el remate del "
                "narrador sobre lo que le quedó—. false solo si el original no "
                "daba para una historia ni desarrollándolo. Júzgate tu texto, no "
                "el original: si lo pudiste desarrollar, se sostiene."
            ),
        },
        "por_que_no": {
            "type": "string",
            "description": (
                "Con se_sostiene en false, en una línea: qué le falta al original "
                "(no tiene final, es un comentario suelto, no se entiende qué "
                "pasó...). Cadena vacía si se sostiene."
            ),
        },
        "cierre": {
            "type": "string",
            "description": (
                "Invitación final de 1-2 frases a comentar, compartir, dar like y "
                "suscribirse, escrita con las palabras y el tono de ESTA historia en "
                "particular (no una fórmula genérica). Se narra al final del video."
            ),
        },
    },
    "required": ["titulo_hook", "genero_narrador", "emocion", "tema", "cuerpo",
                 "cierre", "se_sostiene", "por_que_no"],
}

# De un episodio largo (un podcast de anécdotas puede traer diez) se toman
# solo las mejores: si no, un solo video llenaría la cola y todo el canal
# acabaría contando lo mismo.
MAX_ANECDOTAS_POR_VIDEO = 3

# Cuántos candidatos se escriben por corrida.
#
# Sin tope, esto corre hasta chocar con el 429 de Gemini, y entonces la cuota
# del día se ha ido entera en guiones: calidad_ia y la metadata del publicador
# —que también llaman a Gemini, más tarde en el pipeline— se quedan sin nada.
# Mejor parar solo con algo de margen que reventar a mitad de la corrida.
MAX_POR_CORRIDA = 12

# El suelo por debajo del cual no hay historia que valga, se_sostiene o no.
# No es el largo que se busca —eso se lo pide el prompt, unas 200 palabras—
# sino la red por si el modelo contesta que sí a todo: a ~2,6 palabras por
# segundo, 120 palabras son tres cuartos de minuto, y en menos de eso no cabe
# situación, desarrollo y remate. Subirlo aquí aprieta el filtro sin tocar el
# prompt, pero ojo: lo que se tira ya gastó su llamada a Gemini.
PALABRAS_MINIMAS_CUERPO = 120

SCHEMA_SEGMENTOS = {
    "type": "object",
    "properties": {
        "anecdotas": {
            "type": "array",
            "description": (
                "Las anécdotas o confesiones completas e independientes que contiene "
                "la transcripción, de la más fuerte a la más floja. Solo historias que "
                "empiecen y terminen dentro del texto: nada de conversación suelta, "
                "presentaciones, patrocinios ni despedidas."
            ),
            "items": {
                "type": "object",
                "properties": {
                    "resumen": {
                        "type": "string",
                        "description": "De qué trata la anécdota, en una frase. Sirve para identificarla.",
                    },
                    "historia": {
                        "type": "string",
                        "description": (
                            "La anécdota con todos sus hechos y detalles, contada de corrido "
                            "y ya limpia de muletillas, risas, interrupciones y errores de "
                            "transcripción. Es el material que después se reescribe."
                        ),
                    },
                },
                "required": ["resumen", "historia"],
            },
        }
    },
    "required": ["anecdotas"],
}

SYSTEM_PROMPT_SEGMENTAR = """Recibes la transcripción automática de un video de YouTube de anécdotas o confesiones en español.
Tu trabajo es localizar las historias completas que contiene y devolver cada una por separado.

Reglas:
- Solo historias COMPLETAS: con situación, desarrollo y desenlace dentro del texto. Si una empieza pero se corta, no la incluyas.
- Ignora todo lo que no sea la anécdota: saludos, presentación del programa, patrocinios, comentarios entre los conductores, despedidas, "suscríbete".
- La transcripción es automática: trae errores de reconocimiento, palabras pegadas, risas y muletillas. Reconstruye lo que se quiso decir; no arrastres esa basura al resultado.
- Conserva TODOS los hechos y detalles concretos de cada anécdota (quién, dónde, qué pasó, cómo terminó). No resumas: es material para reescribir después.
- Si el video no contiene ninguna historia completa, devuelve la lista vacía. Es una respuesta correcta; no inventes una.
- No inventes hechos que no estén en la transcripción."""

SYSTEM_PROMPT = """Eres guionista de historias virales estilo "Reddit story", narradas en español mexicano.
Reescribes historias reales (de Reddit) en narrativa en primera persona, natural para ser leída en voz alta por un locutor mexicano.

Reglas:
- El titulo_hook debe enganchar en 1-2 frases, generando curiosidad o tensión inmediata (no reveles el final).
- ESCRIBE CADA HISTORIA CON EL LARGO QUE PIDA, sin mínimo ni máximo. No la recortes para que quepa en un formato corto, ni la estires con relleno, repeticiones o descripciones de más para alcanzar una duración. Si la historia se cuenta bien en 40 segundos, que dure 40 segundos; si necesita ocho minutos de escenas, diálogo y tensión antes del desenlace, tómatelos. Lo único que decide el largo es cuánto necesita ESA historia para escucharse bien; el formato (short o video largo) se determina después, solo, a partir de la duración que resulte.
- El narrador es quien vivió la historia. Decide su género a partir del original y mantenlo coherente en TODO el cuerpo: si narra una mujer, toda la concordancia va en femenino ("me quedé sola", "estaba agotada", "yo era su hija"), y genero_narrador debe decir "femenino". No mezcles: media historia en masculino y media en femenino se escucha como un error, porque el video se narra con una sola voz elegida por ese campo. Si el original no deja claro quién narra, elige un género y sé consistente.
- El cuerpo debe sonar como alguien contando la historia de viva voz: frases cortas, ritmo natural, sin lenguaje de texto escrito (nada de "en resumen", "por lo tanto").
- Usa español mexicano real y cotidiano, no español neutro de doblaje: modismos, muletillas y giros naturales de México ("neta", "qué onda", "se me hizo raro", "no manches", "wey" solo si el tono de la historia lo permite, etc.), sin forzarlos ni exagerar el acento a caricatura. La historia original puede ser de cualquier país — adapta el modo de contarla al mexicano, no la ubiques falsamente en México si el contexto no calza.
- Evita que suene genérico o traducido: cada historia debe conservar su esencia y detalles particulares, no una versión aplanada/intercambiable con cualquier otra.
- Mantén los hechos centrales de la historia original, pero puedes reordenar para maximizar tensión narrativa.
- DESARROLLA lo que el original solo resume. Muchos posts son un párrafo seco: ahí tu trabajo es contarlo, no copiarlo. Puedes poner la escena (dónde, qué hora, qué se oía), lo que el narrador pensó y sintió en cada momento, el diálogo dicho a partir de lo que el original resume ("me dijo que no" → la frase que dijo), y estirar el momento de tensión antes del giro. Nada de eso son hechos nuevos: es la misma historia contada de viva voz en vez de resumida. Una historia que se cuenta bien rara vez baja de 200 palabras; si el original da poco, desarróllalo hasta que se sostenga.
- NO inventes hechos que no estén en el original: nada de que alguien confiese, muera, se vengue, aparezca un juicio o un giro que el original no tiene. Ese es el límite. El video lleva el enlace al post y el nombre de quien lo escribió, así que ponerle en la boca cosas que no dijo ya no es adaptar. Y la etiqueta `tema` decide si el video se publica: si inventas violencia o maltrato que no estaban, esa etiqueta deja de proteger a nadie.
- Si el original se corta sin desenlace, REMATA DESDE EL NARRADOR, no con un final inventado: qué le quedó, qué no volvió a saber, qué haría distinto hoy ("hasta hoy no sé qué fue de ella, y ya dejé de buscar"). Eso es cierto y cierra; un giro falso pegado al final se nota.
- Cierra el cuerpo con una pregunta o gancho que invite a comentar (ej. "¿Ustedes qué hubieran hecho?"). Va DESPUÉS del remate, no en su lugar: una pregunta al aire sobre una historia que nunca terminó deja al que escucha con la sensación de que le colgaron el teléfono.
- El campo cierre es aparte del cuerpo: es la invitación final a compartir, dar like y suscribirse, y se narra después de la historia. Escríbela amarrada a ESTA historia — retoma su tema, su desenlace o su tono, con las mismas palabras que usarías contándola (ej. si fue de una herencia: "Si tú también tienes parientes que solo aparecen cuando hay dinero de por medio, compártele este video... y suscríbete, que historias así me llegan cada semana"). Nunca uses una fórmula intercambiable tipo "no olvides darle like y suscribirte", ni repitas el mismo cierre entre historias distintas. Máximo 2 frases, que suene dicho, no leído.
- No inventes detalles explícitos, violentos o inapropiados que no estén en el original.
- Si ni desarrollándola hay historia (el original es un comentario suelto, una pregunta, un pedazo de conversación sin principio ni fin), dilo en `se_sostiene` y no la fabriques. Es una respuesta correcta y se descarta sin más; inventarla para cumplir sale mucho más caro.
- No incluyas markdown ni encabezados, solo el texto narrado."""

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
ruido.callar_sdk_google()   # los avisos de AFC del SDK, que aqui no aplican
logger = logging.getLogger("script_writer")


def motivo_error_gemini(exc):
    """Motivo accionable si el fallo es de credencial o cuota, o None.

    Un fallo así no es culpa del candidato: es de la configuración, y afecta
    por igual a todos. Distinguirlo importa porque decide si la historia
    vuelve a la cola intacta o se le apunta un intento fallido.
    """
    texto = str(exc)
    if "API_KEY_INVALID" in texto or "API key not valid" in texto:
        return ("clave_invalida", "La GEMINI_API_KEY no es válida.")
    if "SERVICE_DISABLED" in texto:
        return ("api_apagada",
                "La API de Gemini no está habilitada en el proyecto de esa clave.")
    if ("API_KEY_HTTP_REFERRER_BLOCKED" in texto
            or "API_KEY_ANDROID_APP_BLOCKED" in texto
            or "API_KEY_IOS_APP_BLOCKED" in texto
            or "API_KEY_IP_ADDRESS_BLOCKED" in texto):
        return ("clave_restringida",
                "La clave tiene restricción de aplicación (web/Android/IP) y Termux "
                "no la cumple.")
    if "PERMISSION_DENIED" in texto or "are blocked" in texto:
        # No es el modelo: es la clave. Google devuelve este mismo 403 en dos
        # situaciones que desde fuera no se distinguen — la clave restringida a
        # otras APIs, y la clave de un proyecto donde generativelanguage no está
        # habilitada. La segunda engaña porque la consola puede estar enseñando
        # "Habilitada" en un proyecto distinto al de la clave, y las claves no
        # dicen a qué proyecto pertenecen.
        return ("clave_sin_gemini",
                "Esa clave existe, pero no tiene permiso para la API de Gemini "
                "(generativelanguage): o está restringida a otras APIs, o es de "
                "otro proyecto de Google Cloud.")
    if "RESOURCE_EXHAUSTED" in texto or "429" in texto or "quota" in texto.lower():
        return ("sin_cuota", "Se agotó la cuota de Gemini por ahora.")
    if "UNAUTHENTICATED" in texto:
        return ("sin_credencial", "Gemini no recibió ninguna credencial.")
    return None


# Qué hacer con cada motivo. Sin esto el diagnóstico nombra el problema pero
# deja al usuario buscando en qué pantalla de Google se arregla.
ARREGLOS = {
    "clave_invalida": (
        "Saca una nueva en https://aistudio.google.com/apikey y guárdala con",
        "--guardar-clave. Ojo: la de Gemini y la de YouTube son distintas.",
    ),
    "clave_sin_gemini": (
        "Son dos causas posibles, y se comprueban en el mismo sitio:",
        "APIs y servicios → Credenciales, en el proyecto donde habilitaste la API.",
        "1) Si la clave NO aparece en esa lista, es de otro proyecto: crea una",
        "   ahí mismo con '+ Crear credenciales → Clave de API'.",
        "2) Si aparece, ábrela y en 'Restricciones de API' añade",
        "   'Generative Language API' (o ponla en 'No restringir clave').",
        "Y comprueba que la API esté encendida en ESE proyecto:",
        "https://console.cloud.google.com/apis/library/generativelanguage.googleapis.com",
    ),
    "api_apagada": (
        "Habilítala en",
        "https://console.cloud.google.com/apis/library/generativelanguage.googleapis.com",
        "y asegúrate de que sea el mismo proyecto donde creaste la clave.",
    ),
    "clave_restringida": (
        "En Credenciales → esa clave → 'Restricciones de aplicación', ponla en",
        "'Ninguna', o usa una clave de https://aistudio.google.com/apikey.",
    ),
    "sin_cuota": (
        "Espera a que se renueve la cuota (el plan gratis se reinicia a diario)",
        "o usa una clave de un proyecto con facturación.",
    ),
}


def es_sobrecarga(exc):
    """True si Gemini rechazó la llamada por estar saturado, no por el texto."""
    texto = str(exc)
    return ("503" in texto or "UNAVAILABLE" in texto
            or "overloaded" in texto.lower() or "high demand" in texto.lower())


def con_reintentos(fn, *args):
    """Llama a fn reintentando mientras Gemini conteste que está saturado.

    El 503 es del momento, no del texto: la misma historia suele pasar unos
    segundos después. Sin esto, un pico de demanda de Google le apuntaba un
    intento fallido a la historia, y tres picos la descartaban para siempre.
    """
    for espera in ESPERAS_SOBRECARGA_S:
        try:
            return fn(*args)
        except Exception as exc:
            if not es_sobrecarga(exc):
                raise
            logger.info(f"  Gemini saturado; reintento en {espera}s.")
            time.sleep(espera)
    # Último intento: si vuelve a fallar, el error sube tal cual y lo decide
    # quien llama.
    return fn(*args)


def probar_clave():
    """Comprueba GEMINI_API_KEY con una llamada mínima."""
    clave = (os.environ.get("GEMINI_API_KEY") or "").strip()
    if not clave:
        print(" ✗ No hay GEMINI_API_KEY en el entorno ni en secretos.env.")
        print("   Sácala gratis en https://aistudio.google.com/apikey y guárdala:")
        print("     python script_writer.py --guardar-clave TU_CLAVE_AQUI")
        return False
    print(f" Clave encontrada: {clave[:8]}…{clave[-4:]} ({len(clave)} caracteres)")
    if clave == (os.environ.get("YOUTUBE_API_KEY") or "").strip():
        # Las dos empiezan por AIzaSy y se parecen a simple vista, asi que
        # pegar la de YouTube aqui es el error mas facil de cometer. Se dice
        # antes de llamar, porque la respuesta de Google a ese caso es un 403
        # generico que no menciona a YouTube por ningun lado.
        print(" ✗ Es la misma clave que YOUTUBE_API_KEY, y esa no sirve para Gemini.")
        print("   Son credenciales distintas: la de Gemini se saca en")
        print("   https://aistudio.google.com/apikey")
        return False
    if "..." in clave or "…" in clave or len(clave) < 30:
        # El caso tipico es haber guardado el texto de ejemplo con los puntos
        # suspensivos incluidos, copiado de estas mismas instrucciones.
        print(" ✗ Eso no parece una clave real (las de 'AIza' tienen 39"
              " caracteres, y las de 'AQ.' son más largas).")
        print("     python script_writer.py --guardar-clave TU_CLAVE_AQUI")
        return False
    try:
        # El cliente va a una variable a proposito: si se deja como temporal
        # (genai.Client().models...), se queda sin referencias en cuanto se lee
        # .models, el recolector lo cierra, y la llamada muere con "client has
        # been closed" en vez de decir si la clave sirve.
        client = genai.Client()
        client.models.generate_content(model=MODEL, contents="Responde solo: ok")
    except Exception as exc:
        motivo = motivo_error_gemini(exc)
        print(" ✗ La clave no funcionó.")
        if motivo:
            print(f"   Motivo: {motivo[1]}")
            for linea in ARREGLOS.get(motivo[0], ()):
                print(f"   {linea}")
        print(f"   Detalle: {str(exc)[:300]}")
        return False
    print(" ✓ La clave de Gemini funciona.")
    return True


def reescribir_historia(client, candidato):
    if candidato.get("fuente") == "youtube":
        # El material vino de lo que alguien contó hablando en un video ajeno,
        # no de un texto que su autor publicó. Se deja claro en el prompt para
        # que Gemini lo vuelva a contar con sus propias palabras en vez de
        # pulir la transcripción, que es lo que haría por defecto.
        prompt = (
            f"Anécdota contada en el canal de YouTube «{candidato.get('canal', candidato['subreddit'])}»"
            f" (video: {candidato['titulo_original']}).\n\n"
            "Está transcrita de alguien hablando. NO la edites ni la pulas: vuelve a "
            "contarla desde cero, con tus propias palabras y tu propia estructura, "
            "conservando los hechos. No copies frases del original.\n\n"
            f"{candidato['texto_original']}"
        )
    else:
        prompt = (
            f"Historia original (r/{candidato['subreddit']}):\n\n"
            f"Título: {candidato['titulo_original']}\n\n"
            f"{candidato['texto_original']}"
        )

    response = client.models.generate_content(
        model=MODEL,
        contents=prompt,
        config=genai_types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            response_mime_type="application/json",
            response_schema=SCHEMA_HISTORIA,
        ),
    )

    return json.loads(response.text)


def segmentar_transcripcion(client, candidato):
    """Parte la transcripción de un video en las anécdotas que contiene.

    Devuelve una lista de candidatos derivados, cada uno con el mismo formato
    que un candidato normal (para que el resto del flujo no cambie) pero con la
    historia ya aislada. Un episodio de podcast trae varias anécdotas sin
    relación entre sí: mandárselo entero al escritor de guiones daría un solo
    video revuelto en vez de varios buenos.
    """
    response = client.models.generate_content(
        model=MODEL,
        contents=candidato["texto_original"],
        config=genai_types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT_SEGMENTAR,
            response_mime_type="application/json",
            response_schema=SCHEMA_SEGMENTOS,
        ),
    )
    anecdotas = (json.loads(response.text) or {}).get("anecdotas") or []

    # El tope se aplica DESPUÉS de descartar las que no sirven: si se cortara
    # antes, una entrada corta o vacía se comería un cupo y perderíamos una
    # anécdota buena que venía detrás.
    utiles = [a for a in anecdotas if len(((a.get("historia") or "").strip()).split()) >= 80]

    derivados = []
    for i, a in enumerate(utiles[:MAX_ANECDOTAS_POR_VIDEO], 1):
        derivado = dict(candidato)
        derivado["id"] = f"{candidato['id']}#{i}"
        derivado["titulo_original"] = (a.get("resumen") or candidato["titulo_original"]).strip()
        derivado["texto_original"] = a["historia"].strip()
        derivados.append(derivado)
    return derivados


def temas_bloqueados():
    """Los temas que no se escriben, de config.json o los de fábrica."""
    try:
        import generar_video_maestro as gvm
        cfg = gvm.cargar_config(os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json"))
        lista = cfg.get("temas_bloqueados")
    except Exception:
        lista = None
    if not isinstance(lista, list):
        lista = TEMAS_BLOQUEADOS_DEFECTO
    return {str(t) for t in lista}


def motivo_para_tirar(historia, huellas):
    """Por qué esta historia no se lleva al guion, o None si sí.

    Se mira DESPUÉS de escribirla, no antes: el tema lo etiqueta Gemini con la
    historia ya entendida, y para saber si es la misma que otra hace falta el
    texto. La llamada ya está gastada de todos modos; lo que se ahorra es el
    render de cinco minutos en el teléfono y una subida que nadie va a ver.
    """
    # Lo corto ya no se tira por corto: el prompt le pide desarrollarlo. Se
    # tira lo que ni desarrollado era una historia, y eso lo dice quien leyó
    # las dos versiones. El suelo de palabras es solo una red por si el
    # modelo contesta que sí a todo: un cuerpo de tres frases no es una
    # historia con desenlace, diga lo que diga el campo.
    if not historia.get("se_sostiene", True):
        falta = (historia.get("por_que_no") or "").strip()
        return f"el original no daba para una historia{': ' + falta if falta else ''}"

    palabras = len(str(historia.get("cuerpo", "")).split())
    if palabras < PALABRAS_MINIMAS_CUERPO:
        return f"se quedó en {palabras} palabras; ni desarrollada llega a historia"

    tema = historia.get("tema", "")
    if tema in temas_bloqueados():
        return f"tema «{tema}», que el feed de Shorts no reparte"

    cuanto = cola.ya_contada(historia.get("cuerpo", ""), huellas)
    if cuanto:
        return f"ya se contó esta historia (se parecen en {cuanto:.0%})"
    return None


def construir_bloque_guion(historia, candidato):
    # Prefijo '#' para que extraer_titulo_y_cuerpo() las trate como comentario
    # y no las tome como primera línea (título) de la historia.
    # Fuente/Autor se conservan para dar atribución en la descripción del video
    # (transparencia exigida por la Responsible Builder Policy de Reddit: no
    # presentar contenido ajeno como propio).
    # Se comprueba contra el texto que Gemini acaba de escribir. Declarar un
    # género y narrar en el otro es un fallo fácil de cometer y caro de notar:
    # no se ve leyendo el guion, se oye en el video ya renderizado. Aquí
    # todavía se arregla solo.
    declarado = historia["genero_narrador"]
    del_texto = narrador.detectar_genero_narrador(historia["cuerpo"], margen=3)
    if del_texto and del_texto != declarado:
        marcas = narrador.puntuar_genero(historia["cuerpo"])[del_texto][:3]
        logger.warning(
            f"  Dijo narrador {declarado} pero escribió en {del_texto} "
            f"({', '.join(marcas)}…). Se corrige a {del_texto}."
        )
        declarado = del_texto

    genero = "Femenino" if declarado == "femenino" else "Masculino"

    # El cierre (invitación a comentar/compartir/suscribirse) se anexa al cuerpo
    # en vez de ir como campo aparte: así lo narra la misma voz, en la misma
    # llamada al TTS, y hereda los subtítulos karaoke sincronizados sin que
    # generar_video_maestro.py tenga que saber que existe.
    cuerpo = historia["cuerpo"].rstrip()
    cierre = (historia.get("cierre") or "").strip()
    if cierre:
        cuerpo = f"{cuerpo}\n\n{cierre}"

    return (
        f"# Genero: {genero}\n"
        f"# Emocion: {historia['emocion']}\n"
        f"# Fuente: {candidato['url']}\n"
        f"# Autor: {candidato.get('autor', '[desconocido]')}\n"
        f"{historia['titulo_hook']}\n"
        f"{cuerpo}"
    )


def escribir_guion(bloques):
    """Agrega los bloques nuevos al final de guion.txt."""
    contenido_previo = ""
    if os.path.exists(RUTA_GUION):
        with open(RUTA_GUION, "r", encoding="utf-8") as f:
            contenido_previo = f.read().strip()

    separador = "\n\n===NUEVA_HISTORIA===\n"
    nuevo_contenido = separador.join(bloques)
    if contenido_previo:
        nuevo_contenido = contenido_previo + separador + nuevo_contenido

    # Archivo temporal y os.replace: si el proceso muere a media escritura, el
    # guion.txt de antes sigue entero en vez de quedar cortado por la mitad.
    tmp = RUTA_GUION + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(nuevo_contenido)
    os.replace(tmp, RUTA_GUION)


def main(argv=None):
    # argv explícito: pipeline.py llama a main() sin argumentos, y si argparse
    # cayera a sys.argv se comería los flags del pipeline.
    ap = argparse.ArgumentParser(description="Convierte la cola de candidatos en guiones.")
    ap.add_argument("--max", type=int, default=MAX_POR_CORRIDA, dest="maximo",
                    metavar="N", help=f"Cuántos candidatos escribir como mucho "
                                      f"(por omisión {MAX_POR_CORRIDA}; 0 = sin tope).")
    ap.add_argument("--probar-clave", action="store_true",
                    help="Comprueba que GEMINI_API_KEY sirve, sin escribir nada.")
    # nargs="?" para poder usarlo sin valor: pasar la clave en la linea de
    # comandos la deja escrita en el historial de la shell, y desde el movil
    # encima obliga a pegarla entre comillas, que el teclado de Android
    # convierte en tipograficas. Sin valor, se pide por teclado y no queda
    # rastro. El const="" distingue "flag sin valor" de "flag ausente" (None).
    ap.add_argument("--guardar-clave", metavar="CLAVE", nargs="?", const="",
                    help="Guarda GEMINI_API_KEY en secretos.env (reemplaza la anterior) "
                         "y la prueba. Sin valor, la pide por teclado.")
    args = ap.parse_args(argv or [])

    if args.guardar_clave is not None:
        clave = args.guardar_clave.strip()
        if not clave:
            try:
                clave = input(" Pega la clave de Gemini y pulsa enter: ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n Cancelado, no se ha tocado nada.")
                return 1
            if not clave:
                print(" ✗ No pegaste nada. No se ha tocado nada.")
                return 1
        revision = secretos.revisar_clave_api(clave)
        if revision:
            severidad, problema = revision
            if severidad == "error":
                # Otra credencial: se rechaza antes de escribir, para no dejar
                # el archivo peor de como estaba.
                print(f" ✗ {problema}")
                print("   La clave sale de APIs y servicios → Credenciales →")
                print("   + Crear credenciales → Clave de API.")
                return 1
            print(f" ⚠️ {problema}")
        try:
            ruta = secretos.guardar("GEMINI_API_KEY", clave)
        except ValueError as exc:
            print(f" ✗ {exc}")
            return 1
        print(f" Guardada en {ruta}")
        return 0 if probar_clave() else 1

    if args.probar_clave:
        return 0 if probar_clave() else 1

    # Los más frescos primero: la cuota del día rinde más en lo de ayer que en
    # un post de hace tres semanas, y la cola es por orden de llegada.
    candidatos = cola.por_frescura(cola.cargar_pendientes())
    # Lo que no toca esta vez vuelve a la cola tal cual, sin gastar intento.
    para_luego = []
    if args.maximo and len(candidatos) > args.maximo:
        candidatos, para_luego = candidatos[:args.maximo], candidatos[args.maximo:]
        logger.info(
            f"{len(candidatos)} de {len(candidatos) + len(para_luego)} candidato(s) en esta "
            f"corrida; el resto espera a la próxima (--max para cambiarlo)."
        )

    if not candidatos:
        logger.info(
            "No hay candidatos en la cola. Corre  python trend_scout.py  para buscar "
            "historias nuevas (o  python trend_scout.py --diagnostico  si no encuentra nada)."
        )
        return

    client = genai.Client()
    bloques = []
    usados = []      # ids que sí se convirtieron en guion
    descartados = [] # ids que fallaron demasiadas veces
    tiradas = []     # escritas pero no publicables (tema o repetida)
    # Se cargan una vez, no por historia: esto crece hasta 400 huellas y
    # releerlas del disco en cada una sería leer el mismo archivo veinte veces.
    huellas = cola.cargar_huellas()
    quedan = []      # candidatos que vuelven a la cola para el próximo intento
    error_global = None  # fallo de configuración que corta la corrida entera

    for i, candidato in enumerate(candidatos, 1):
        logger.info(f"[{i}/{len(candidatos)}] {candidato['titulo_original'][:60]}...")
        try:
            # Un video de YouTube no es una historia: es un episodio con varias
            # dentro. Primero se separan, y cada una se reescribe aparte.
            if candidato.get("tipo") == "transcripcion":
                partes = con_reintentos(segmentar_transcripcion, client, candidato)
                if not partes:
                    logger.warning(
                        f"  Sin anécdotas completas en {candidato['id']}; se descarta el video."
                    )
                    descartados.append(candidato["id"])
                    continue
                logger.info(f"  {len(partes)} anécdota(s) encontrada(s) en el video.")
            else:
                partes = [candidato]

            # El fallo de una anécdota no tumba a las demás. Y basta con que
            # una salga para dar el video por consumido: reintentarlo entero
            # volvería a escribir las que ya están en el guion.
            escritas = 0
            for parte in partes:
                if len(partes) > 1:
                    logger.info(f"  → Reescribiendo: {parte['titulo_original'][:60]}...")
                try:
                    historia = con_reintentos(reescribir_historia, client, parte)
                    motivo_tirar = motivo_para_tirar(historia, huellas)
                    if motivo_tirar:
                        logger.info(f"  ✗ Descartada: {motivo_tirar}")
                        tiradas.append((historia.get("titulo_hook", "")[:70], motivo_tirar))
                        # Cuenta como escrita para que el candidato se dé por
                        # consumido: la decisión no va a cambiar si se
                        # reintenta, y dejarlo en la cola lo haría volver a
                        # gastar una llamada a Gemini en cada corrida.
                        escritas += 1
                        continue
                    bloques.append(construir_bloque_guion(historia, parte))
                    # La huella entra en la lista viva, no solo en el disco: dos
                    # segmentos del MISMO video que se pisan llegan dentro de
                    # esta misma vuelta, y comparándolos solo contra el
                    # historial guardado pasarían los dos.
                    huellas.append(cola.huella(historia["cuerpo"]))
                    cola.guardar_huella(historia["cuerpo"])
                    escritas += 1
                except Exception as exc:
                    # Un fallo de credencial o cuota no es de esta anécdota y
                    # va a repetirse en todas: sube al manejador de afuera, que
                    # corta la corrida y deja la cola intacta. Tragárselo aquí
                    # convertía el problema en "ninguna parte se pudo
                    # reescribir" y le apuntaba un intento al candidato.
                    if motivo_error_gemini(exc) or es_sobrecarga(exc):
                        raise
                    logger.warning(f"  Fallo en {parte['id']}: {exc}")

            if escritas:
                usados.append(candidato["id"])
                continue
            raise RuntimeError("ninguna parte se pudo reescribir")
        except Exception as exc:
            motivo = motivo_error_gemini(exc)
            if motivo:
                # Clave mala o cuota agotada: el problema es la configuración,
                # no esta historia. Se corta aquí, y los candidatos que faltan
                # (este incluido) vuelven a la cola SIN sumar intento: si no,
                # tres corridas con la clave rota bastarían para descartar la
                # cola entera y marcarla como ya usada.
                error_global = motivo
                quedan.append(candidato)
                quedan.extend(candidatos[i:])
                break
            if es_sobrecarga(exc):
                # Aguantó todos los reintentos saturado. No es culpa de esta
                # historia, así que vuelve a la cola como estaba; los demás
                # candidatos sí se intentan, porque el pico puede pasar dentro
                # de la misma corrida.
                logger.warning(
                    f"Gemini sigue saturado; {candidato['id']} vuelve a la cola sin gastar intento."
                )
                quedan.append(candidato)
                continue
            logger.warning(f"Fallo en candidato {candidato['id']}: {exc}")

        # Un fallo NO quema la historia: vuelve a la cola. Solo se descarta
        # después de varios intentos, para que un texto que Gemini siempre
        # rechaza no bloquee la cola para siempre.
        candidato["intentos"] = int(candidato.get("intentos", 0)) + 1
        if candidato["intentos"] >= cola.MAX_INTENTOS:
            logger.warning(
                f"Candidato {candidato['id']} descartado tras {candidato['intentos']} intentos."
            )
            descartados.append(candidato["id"])
        else:
            quedan.append(candidato)

    # El guion va PRIMERO, antes de tocar la cola. Al revés —que era como
    # estaba— hay una ventana en la que los candidatos ya salieron de la cola
    # y su guion todavía no existe en ningún sitio: un Ctrl+C, una batería que
    # se acaba o que Android mate Termux ahí en medio, y ese trabajo no está ni
    # hecho ni pendiente. Se pierde y no hay forma de saber cuál era.
    if bloques:
        escribir_guion(bloques)
        logger.info(f"{len(bloques)} historia(s) agregada(s) a {RUTA_GUION}")

    if tiradas:
        logger.info(f"{len(tiradas)} escrita(s) pero no llevada(s) al guion:")
        for titulo, motivo in tiradas:
            logger.info(f"   • {titulo} — {motivo}")

    # La cola se actualiza siempre, aunque no haya salido ningún bloque: si
    # no, los contadores de intentos se perderían y los mismos candidatos
    # rotos se reintentarían eternamente.
    cola.guardar_pendientes(quedan + para_luego)
    # Con el guion ya en disco, esos posts pasan a ser "vistos". Marcarlos
    # antes (que era lo que hacía trend_scout al escanear) los quemaba aunque
    # la reescritura fallara.
    cola.marcar_vistos(usados + descartados)
    if quedan or para_luego:
        logger.info(
            f"{len(quedan) + len(para_luego)} candidato(s) quedaron en la cola para la próxima."
        )

    if error_global:
        codigo, mensaje = error_global
        logger.error(mensaje)
        print("")
        print(f" ⛔ {mensaje}")
        print(f"    Los {len(quedan) + len(para_luego)} candidato(s) siguen en la cola, intactos.")
        print("")
        for linea in ARREGLOS.get(codigo, (
            "Saca una clave en https://aistudio.google.com/apikey y guárdala con",
            "python script_writer.py --guardar-clave TU_CLAVE_AQUI",
        )):
            print(f"    {linea}")
        print("")
        print("    Compruébala con:  python script_writer.py --probar-clave")

    if not bloques and not error_global:
        logger.error("Ninguna historia se pudo reescribir con éxito.")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]) or 0)
