"""
Rehace los guiones que ya están en guion.txt para que queden en el formato
actual, sin volver a Reddit.

Para qué sirve: las historias escritas antes traen dos cosas del formato
viejo que ya no queremos:
  1. Longitud forzada — si el post original traía 400+ palabras, se pedían
     700-900 aunque la historia no diera para tanto (relleno), y las que sí
     daban para más se quedaban cortas.
  2. Sin cierre — no traen la invitación final a comentar, compartir y
     suscribirse.

Los subtítulos y la tarjeta de intro NO hacen falta rehacerlos aquí: esos se
arreglan solos con volver a renderizar, porque dependen del render y no del
texto. Si lo único que te importa es el problema de los subtítulos, no
necesitas este script — basta con rehacer_todo.py.

Por qué se reescribe desde guion.txt y no desde Reddit: trend_scout.py
sobrescribe candidatos.json en cada corrida, así que los textos originales
de los posts ya no están en disco. El guion existente sí conserva los hechos
de la historia (es una reescritura fiel del original), así que sirve de
material de partida. La atribución (# Fuente: / # Autor:) se copia tal cual,
sin tocarse.

Uso:
  python rehacer_guiones.py            # pide confirmación
  python rehacer_guiones.py --si       # sin preguntar
  python rehacer_guiones.py --desde 12 # retoma a partir de la historia 12

El guion.txt anterior se respalda como guion.txt.bak-<fecha> antes de
escribir nada, y el nuevo se va guardando historia por historia, así que si
se corta a media corrida (red, cuota de Gemini) no se pierde lo ya hecho:
vuelve a correrlo con --desde N para continuar.
"""
import os
import re
import sys
import json
import time
import shutil
import logging
import argparse
from datetime import datetime

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("rehacer_guiones")

from google import genai
from google.genai import types as genai_types
from google.genai import errors as genai_errors

import script_writer

SEPARADOR = "\n\n===NUEVA_HISTORIA===\n"

INSTRUCCION_REESCRITURA = """El texto de abajo es una versión ANTERIOR del guion de esta historia, escrita bajo una restricción artificial de longitud que ya no aplica.

Reescríbela conservando exactamente los mismos hechos, personajes y desenlace, pero:
- Con la longitud que la historia realmente pida. Si la versión anterior fue inflada con relleno, repeticiones o descripciones de más para alcanzar una cuota de palabras, córtalo. Si se quedó corta y la historia daba para desarrollar más tensión, desarróllala.
- No inventes hechos nuevos que no estén en el texto de abajo.
- Aplicando todas las reglas de estilo (español mexicano real, ritmo de voz alta, hook fuerte, gancho para comentarios) y agregando el campo cierre, que la versión anterior no tenía."""


def parsear_guion(ruta):
    """Devuelve una lista de dicts con las cabeceras (#) y el texto de cada
    historia, respetando el mismo formato que escribe script_writer.py."""
    if not os.path.exists(ruta):
        raise SystemExit(f"No se encontró {ruta}. ¿Ya corriste el pipeline alguna vez?")

    with open(ruta, "r", encoding="utf-8") as f:
        bloques = [b.strip() for b in f.read().split("===NUEVA_HISTORIA===") if b.strip()]

    historias = []
    for bloque in bloques:
        cabeceras = {}
        for clave in ("Genero", "Emocion", "Fuente", "Autor"):
            m = re.search(rf"^#\s*{clave}:\s*(.+)$", bloque, re.IGNORECASE | re.MULTILINE)
            if m:
                cabeceras[clave] = m.group(1).strip()

        # Mismo criterio que extraer_titulo_y_cuerpo(): primera línea no
        # comentada = título, el resto = cuerpo.
        lineas = [
            l.strip() for l in bloque.splitlines()
            if l.strip() and not l.strip().startswith(("#", "===", "📌", "🎙️"))
        ]
        if not lineas:
            continue

        historias.append({
            "cabeceras": cabeceras,
            "titulo": lineas[0],
            "cuerpo": " ".join(lineas[1:]) if len(lineas) > 1 else "",
            "bloque_original": bloque,
        })
    return historias


def reescribir(client, historia):
    prompt = (
        f"{INSTRUCCION_REESCRITURA}\n\n"
        f"--- Versión anterior ---\n"
        f"Título: {historia['titulo']}\n\n"
        f"{historia['cuerpo']}"
    )
    response = client.models.generate_content(
        model=script_writer.MODEL,
        contents=prompt,
        config=genai_types.GenerateContentConfig(
            system_instruction=script_writer.SYSTEM_PROMPT,
            response_mime_type="application/json",
            response_schema=script_writer.SCHEMA_HISTORIA,
        ),
    )
    return json.loads(response.text)


def construir_bloque(nueva, cabeceras):
    """Rearma el bloque conservando la atribución original intacta."""
    genero = "Femenino" if nueva["genero_narrador"] == "femenino" else "Masculino"
    cuerpo = nueva["cuerpo"].rstrip()
    cierre = (nueva.get("cierre") or "").strip()
    if cierre:
        cuerpo = f"{cuerpo}\n\n{cierre}"

    return (
        f"# Genero: {genero}\n"
        f"# Emocion: {nueva['emocion']}\n"
        f"# Fuente: {cabeceras.get('Fuente', '[desconocida]')}\n"
        f"# Autor: {cabeceras.get('Autor', '[desconocido]')}\n"
        f"{nueva['titulo_hook']}\n"
        f"{cuerpo}"
    )


def main():
    parser = argparse.ArgumentParser(description="Reescribe guion.txt en el formato actual.")
    parser.add_argument("--si", action="store_true", help="No pedir confirmación.")
    parser.add_argument("--desde", type=int, default=1, help="Retomar desde esta historia (1 = la primera).")
    args = parser.parse_args()

    historias = parsear_guion(script_writer.RUTA_GUION)
    total = len(historias)
    logger.info(f"{total} historia(s) en {script_writer.RUTA_GUION}")

    sin_cierre = total - args.desde + 1
    palabras = sum(len(h["cuerpo"].split()) for h in historias)
    logger.info(f"~{palabras} palabras en total; se van a reescribir {sin_cierre} historia(s) con Gemini.")

    if not args.si:
        resp = input(
            f"\nEsto hace {sin_cierre} llamada(s) a Gemini y reemplaza guion.txt "
            f"(se respalda antes). ¿Continuamos? (escribe 'si'): "
        ).strip().lower()
        if resp != "si":
            logger.info("Cancelado. No se tocó nada.")
            return

    respaldo = f"{script_writer.RUTA_GUION}.bak-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    shutil.copy2(script_writer.RUTA_GUION, respaldo)
    logger.info(f"Respaldo guardado en {respaldo}")

    client = genai.Client()
    bloques = []
    fallidas = []

    for i, historia in enumerate(historias, 1):
        if i < args.desde:
            # Ya reescritas en una corrida anterior: se dejan como están.
            bloques.append(historia["bloque_original"])
            continue

        titulo_corto = historia["titulo"][:55]
        logger.info(f"[{i}/{total}] Reescribiendo: {titulo_corto}...")
        try:
            nueva = reescribir(client, historia)
            bloques.append(construir_bloque(nueva, historia["cabeceras"]))
            n_antes = len(historia["cuerpo"].split())
            n_despues = len(nueva["cuerpo"].split())
            logger.info(f"        {n_antes} -> {n_despues} palabras, con cierre.")
        except (genai_errors.APIError, Exception) as exc:
            # Si falla, se conserva el bloque viejo: es preferible un guion
            # con formato viejo que perder la historia.
            logger.warning(f"        Falló ({exc}); se conserva la versión anterior.")
            bloques.append(historia["bloque_original"])
            fallidas.append(i)

        # Se guarda en cada vuelta para que una caída no borre lo avanzado.
        with open(script_writer.RUTA_GUION, "w", encoding="utf-8") as f:
            f.write(SEPARADOR.join(bloques))
        time.sleep(1)

    logger.info(f"\n✅ Listo: {total - len(fallidas)}/{total} historia(s) en el formato actual.")
    if fallidas:
        logger.warning(
            f"Quedaron con formato viejo las historias: {fallidas}. "
            f"Vuelve a correr con --desde {min(fallidas)} para reintentarlas."
        )
    logger.info(
        "\nAhora, para que los videos se rehagan con estos guiones:\n"
        "  python rehacer_todo.py       # borra los videos viejos (YouTube + local)\n"
        "  python generar_video_maestro.py\n"
        "  python publisher.py"
    )


if __name__ == "__main__":
    main()
