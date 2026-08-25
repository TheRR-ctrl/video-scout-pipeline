"""
Script Writer — convierte pipeline_state/candidatos.json (salida de trend_scout.py)
en guion.txt, el formato que ya consume generar_video_maestro.py.

Usa la API de Gemini (capa gratuita) para reescribir cada historia con un hook
fuerte en las primeras líneas y marcar Genero:/Emocion: explícitos.

Requiere: pip install -U google-genai
Credenciales: variable de entorno GEMINI_API_KEY (gratis en https://aistudio.google.com/apikey).
"""
import os
import json
import logging

from google import genai
from google.genai import types as genai_types
from google.genai import errors as genai_errors

CARPETA_ESTADO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pipeline_state")
RUTA_CANDIDATOS = os.path.join(CARPETA_ESTADO, "candidatos.json")
RUTA_GUION = os.path.join(os.path.dirname(os.path.abspath(__file__)), "guion.txt")

MODEL = "gemini-3.6-flash"
EMOCIONES_VALIDAS = ["venganza", "suspenso", "drama", "comedia"]

# Si la historia original tiene suficiente material (en palabras), se le pide
# a Gemini una versión extendida (~5 min de narración) en vez de la versión
# corta de siempre, para que también salgan videos largos y no solo Shorts.
UMBRAL_PALABRAS_PARA_EXTENDER = 400
OBJETIVO_PALABRAS_EXTENDIDO = "700 y 900"

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
            "description": "Género gramatical de quien narra la historia en primera persona.",
        },
        "emocion": {
            "type": "string",
            "enum": EMOCIONES_VALIDAS,
        },
        "cuerpo": {
            "type": "string",
            "description": "Historia reescrita en primera persona, narrativa, ritmo natural para narración en voz alta, cerrando con un gancho para comentarios.",
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

SYSTEM_PROMPT = """Eres guionista de historias virales estilo "Reddit story" para Shorts/TikTok, narradas en español mexicano.
Reescribes historias reales (de Reddit) en narrativa en primera persona, natural para ser leída en voz alta por un locutor mexicano.

Reglas:
- El titulo_hook debe enganchar en 1-2 frases, generando curiosidad o tensión inmediata (no reveles el final).
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


def reescribir_historia(client, candidato):
    prompt = (
        f"Historia original (r/{candidato['subreddit']}):\n\n"
        f"Título: {candidato['titulo_original']}\n\n"
        f"{candidato['texto_original']}"
    )

    num_palabras_original = len(candidato["texto_original"].split())
    if num_palabras_original >= UMBRAL_PALABRAS_PARA_EXTENDER:
        prompt += (
            f"\n\nEsta historia tiene suficiente material: escribe el cuerpo "
            f"extendido, apuntando a entre {OBJETIVO_PALABRAS_EXTENDIDO} palabras "
            f"(para un video de varios minutos, no un short). Desarrolla más el "
            f"ritmo narrativo — más escenas, diálogo, reflexión del narrador, "
            f"tensión antes del desenlace — sin inventar hechos nuevos que no "
            f"estén en el original."
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


def construir_bloque_guion(historia, candidato):
    # Prefijo '#' para que extraer_titulo_y_cuerpo() las trate como comentario
    # y no las tome como primera línea (título) de la historia.
    # Fuente/Autor se conservan para dar atribución en la descripción del video
    # (transparencia exigida por la Responsible Builder Policy de Reddit: no
    # presentar contenido ajeno como propio).
    genero = "Femenino" if historia["genero_narrador"] == "femenino" else "Masculino"

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


def main():
    if not os.path.exists(RUTA_CANDIDATOS):
        logger.error(f"No se encontró {RUTA_CANDIDATOS}. Corre trend_scout.py primero.")
        return

    with open(RUTA_CANDIDATOS, "r", encoding="utf-8") as f:
        candidatos = json.load(f)

    if not candidatos:
        logger.info("No hay candidatos nuevos que procesar.")
        return

    client = genai.Client()
    bloques = []

    for i, candidato in enumerate(candidatos, 1):
        logger.info(f"[{i}/{len(candidatos)}] Reescribiendo: {candidato['titulo_original'][:60]}...")
        try:
            historia = reescribir_historia(client, candidato)
            bloques.append(construir_bloque_guion(historia, candidato))
        except genai_errors.APIError as exc:
            logger.warning(f"Fallo de API en candidato {candidato['id']}: {exc}")
        except Exception as exc:
            logger.warning(f"Fallo inesperado en candidato {candidato['id']}: {exc}")

    if not bloques:
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

    logger.info(f"{len(bloques)} historia(s) agregada(s) a {RUTA_GUION}")


if __name__ == "__main__":
    main()
