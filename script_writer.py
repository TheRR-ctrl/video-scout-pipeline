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
import argparse

import secretos  # carga secretos.env si las claves no están en el entorno
import narrador  # comprobar que el género declarado casa con el texto escrito
import cola      # cola de candidatos e historial compartidos con trend_scout.py

from google import genai
from google.genai import types as genai_types

CARPETA_ESTADO = cola.CARPETA_ESTADO
RUTA_CANDIDATOS = cola.RUTA_CANDIDATOS
RUTA_GUION = os.path.join(os.path.dirname(os.path.abspath(__file__)), "guion.txt")

MODEL = "gemini-3.6-flash"
EMOCIONES_VALIDAS = ["venganza", "suspenso", "drama", "comedia"]

# A propósito no hay objetivo de longitud, ni mínimo ni máximo: forzar el
# largo del guion (antes se pedían 700-900 palabras si el original traía 400+)
# hacía que unas historias se inflaran con relleno y otras se quedaran cortas
# a media tensión, en los dos casos empeorando la narración. Cada historia se
# escribe con el largo que pide, y el formato se decide DESPUÉS: en
# generar_video_maestro.py, es_short sale de la duración real del audio ya
# generado (dur_sec <= duracion_max_short_sec), no de una cuota de palabras.

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
        "cuerpo": {
            "type": "string",
            "description": (
                "Historia reescrita en primera persona, narrativa, ritmo natural para "
                "narración en voz alta, cerrando con un gancho para comentarios. Sin "
                "límite de longitud: tan larga o tan corta como la historia necesite "
                "para contarse bien."
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
    "required": ["titulo_hook", "genero_narrador", "emocion", "cuerpo", "cierre"],
}

# De un episodio largo (un podcast de anécdotas puede traer diez) se toman
# solo las mejores: si no, un solo video llenaría la cola y todo el canal
# acabaría contando lo mismo.
MAX_ANECDOTAS_POR_VIDEO = 3

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
- Cierra el cuerpo con una pregunta o gancho que invite a comentar (ej. "¿Ustedes qué hubieran hecho?").
- El campo cierre es aparte del cuerpo: es la invitación final a compartir, dar like y suscribirse, y se narra después de la historia. Escríbela amarrada a ESTA historia — retoma su tema, su desenlace o su tono, con las mismas palabras que usarías contándola (ej. si fue de una herencia: "Si tú también tienes parientes que solo aparecen cuando hay dinero de por medio, compártele este video... y suscríbete, que historias así me llegan cada semana"). Nunca uses una fórmula intercambiable tipo "no olvides darle like y suscribirte", ni repitas el mismo cierre entre historias distintas. Máximo 2 frases, que suene dicho, no leído.
- No inventes detalles explícitos, violentos o inapropiados que no estén en el original.
- No incluyas markdown ni encabezados, solo el texto narrado."""

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
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
    if "PERMISSION_DENIED" in texto or "SERVICE_DISABLED" in texto:
        return ("sin_permiso", "La clave de Gemini no tiene permiso para este modelo.")
    if "RESOURCE_EXHAUSTED" in texto or "429" in texto or "quota" in texto.lower():
        return ("sin_cuota", "Se agotó la cuota de Gemini por ahora.")
    if "UNAUTHENTICATED" in texto:
        return ("sin_credencial", "Gemini no recibió ninguna credencial.")
    return None


def probar_clave():
    """Comprueba GEMINI_API_KEY con una llamada mínima."""
    clave = (os.environ.get("GEMINI_API_KEY") or "").strip()
    if not clave:
        print(" ✗ No hay GEMINI_API_KEY en el entorno ni en secretos.env.")
        print("   Sácala gratis en https://aistudio.google.com/apikey y guárdala:")
        print("     python script_writer.py --guardar-clave TU_CLAVE_AQUI")
        return False
    print(f" Clave encontrada: {clave[:8]}…{clave[-4:]} ({len(clave)} caracteres)")
    if "..." in clave or "…" in clave or len(clave) < 30:
        print(" ✗ Eso no parece una clave real (una tiene ~39 caracteres).")
        print("     python script_writer.py --guardar-clave TU_CLAVE_AQUI")
        return False
    try:
        genai.Client().models.generate_content(model=MODEL, contents="Responde solo: ok")
    except Exception as exc:
        motivo = motivo_error_gemini(exc)
        print(" ✗ La clave no funcionó.")
        if motivo:
            print(f"   Motivo: {motivo[1]}")
            if motivo[0] == "clave_invalida":
                print("   Saca una nueva en https://aistudio.google.com/apikey y guárdala con")
                print("   --guardar-clave. Ojo: la de Gemini y la de YouTube son distintas.")
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


def main(argv=None):
    # argv explícito: pipeline.py llama a main() sin argumentos, y si argparse
    # cayera a sys.argv se comería los flags del pipeline.
    ap = argparse.ArgumentParser(description="Convierte la cola de candidatos en guiones.")
    ap.add_argument("--probar-clave", action="store_true",
                    help="Comprueba que GEMINI_API_KEY sirve, sin escribir nada.")
    ap.add_argument("--guardar-clave", metavar="CLAVE",
                    help="Guarda GEMINI_API_KEY en secretos.env (reemplaza la anterior) y la prueba.")
    args = ap.parse_args(argv or [])

    if args.guardar_clave:
        try:
            ruta = secretos.guardar("GEMINI_API_KEY", args.guardar_clave)
        except ValueError as exc:
            print(f" ✗ {exc}")
            return 1
        print(f" Guardada en {ruta}")
        return 0 if probar_clave() else 1

    if args.probar_clave:
        return 0 if probar_clave() else 1

    candidatos = cola.cargar_pendientes()

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
    quedan = []      # candidatos que vuelven a la cola para el próximo intento
    error_global = None  # fallo de configuración que corta la corrida entera

    for i, candidato in enumerate(candidatos, 1):
        logger.info(f"[{i}/{len(candidatos)}] {candidato['titulo_original'][:60]}...")
        try:
            # Un video de YouTube no es una historia: es un episodio con varias
            # dentro. Primero se separan, y cada una se reescribe aparte.
            if candidato.get("tipo") == "transcripcion":
                partes = segmentar_transcripcion(client, candidato)
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
                    historia = reescribir_historia(client, parte)
                    bloques.append(construir_bloque_guion(historia, parte))
                    escritas += 1
                except Exception as exc:
                    # Un fallo de credencial o cuota no es de esta anécdota y
                    # va a repetirse en todas: sube al manejador de afuera, que
                    # corta la corrida y deja la cola intacta. Tragárselo aquí
                    # convertía el problema en "ninguna parte se pudo
                    # reescribir" y le apuntaba un intento al candidato.
                    if motivo_error_gemini(exc):
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

    # La cola se actualiza siempre, aunque no haya salido ningún bloque: si
    # no, los contadores de intentos se perderían y los mismos candidatos
    # rotos se reintentarían eternamente.
    cola.guardar_pendientes(quedan)
    if descartados:
        cola.marcar_vistos(descartados)

    if error_global:
        codigo, mensaje = error_global
        logger.error(mensaje)
        print("")
        print(f" ⛔ {mensaje}")
        print(f"    Los {len(quedan)} candidato(s) siguen en la cola, intactos.")
        print("")
        if codigo == "sin_cuota":
            print("    Espera a que se renueve la cuota y vuelve a correrlo.")
        else:
            print("    Saca una clave en https://aistudio.google.com/apikey y guárdala:")
            print("      python script_writer.py --guardar-clave TU_CLAVE_AQUI")
            print("    (La clave de Gemini y la de YouTube son distintas.)")
        print("")
        print("    Compruébala con:  python script_writer.py --probar-clave")

    if not bloques:
        if not error_global:
            logger.error("Ninguna historia se pudo reescribir con éxito.")
        return

    contenido_previo = ""
    if os.path.exists(RUTA_GUION):
        with open(RUTA_GUION, "r", encoding="utf-8") as f:
            contenido_previo = f.read().strip()

    separador = "\n\n===NUEVA_HISTORIA===\n"
    nuevo_contenido = separador.join(bloques)
    if contenido_previo:
        nuevo_contenido = contenido_previo + separador + nuevo_contenido

    with open(RUTA_GUION, "w", encoding="utf-8") as f:
        f.write(nuevo_contenido)

    # Solo AHORA, con el guion ya escrito en disco, esos posts pasan a ser
    # "vistos". Marcarlos antes (que era lo que hacía trend_scout al escanear)
    # los quemaba aunque la reescritura fallara.
    cola.marcar_vistos(usados)

    logger.info(f"{len(bloques)} historia(s) agregada(s) a {RUTA_GUION}")
    if quedan:
        logger.info(f"{len(quedan)} candidato(s) quedaron en la cola para el próximo intento.")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]) or 0)
