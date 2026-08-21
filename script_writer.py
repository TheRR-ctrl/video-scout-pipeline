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
    },
    "required": ["titulo_hook", "genero_narrador", "emocion", "cuerpo"],
}

SYSTEM_PROMPT = """Eres guionista de historias virales estilo "Reddit story" para Shorts/TikTok en español.
Reescribes historias reales (de Reddit) en narrativa en primera persona, natural para ser leída en voz alta por un locutor.

Reglas:
- El titulo_hook debe enganchar en 1-2 frases, generando curiosidad o tensión inmediata (no reveles el final).
- El cuerpo debe sonar como alguien contando la historia de viva voz: frases cortas, ritmo natural, sin lenguaje de texto escrito (nada de "en resumen", "por lo tanto").
- Mantén los hechos centrales de la historia original, pero puedes reordenar para maximizar tensión narrativa.
- Cierra el cuerpo con una pregunta o gancho que invite a comentar (ej. "¿Ustedes qué hubieran hecho?").
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
    return (
        f"# Genero: {genero}\n"
        f"# Emocion: {historia['emocion']}\n"
        f"# Fuente: {candidato['url']}\n"
        f"# Autor: {candidato.get('autor', '[desconocido]')}\n"
        f"{historia['titulo_hook']}\n"
        f"{historia['cuerpo']}"
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
