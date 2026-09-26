"""
Parte en varios shorts seguidos las historias que no caben en uno.

Mientras los largos estén bloqueados (ver formato.py), una historia que no
cabe en un short se aplaza y se queda en guion.txt sin producir nada hasta
los 500 suscriptores. Y es justo el formato que no hay que esperar: según
formato.py, 48 shorts sumaron 29.000 vistas y 18 largos, 43. Así que en vez
de aplazarla se parte en dos, tres o cuatro shorts, cada uno con
"Parte N de M — " delante del título.

Dos vías, con el mismo corte:
  · script_writer.py la llama al escribir cada historia nueva: la que nace
    larga entra en la cola ya partida.
  · Este script, para las que ya estaban en la cola:
      python partir_historias.py        # dice qué partiría, sin tocar nada
      python partir_historias.py --si   # lo hace, guardando antes una copia

El texto no se reescribe. Gemini solo elige ENTRE QUÉ FRASES se corta,
buscando que cada parte acabe en suspenso; si falla, o no hay clave, se
corta a partes iguales. Así lo que se publica es exactamente lo que ya
estaba escrito.

OJO CON LA NUMERACIÓN. Meter partes en medio de guion.txt corre el número
de todas las historias de detrás, y el render reconoce lo ya grabado por
"NN_Titulo.mp4": una historia grabada como la 05 que pasa a ser la 07 se
volvería a grabar. Por eso, si detrás de una historia a partir hay alguna
ya grabada, antes se quitan de la cola las grabadas — lo mismo que hace
limpiar_cola.py, con su copia y su historial.
"""
import os
import re
import sys
import json
import math
import shutil
import logging
import argparse
from datetime import datetime

import titulos

logger = logging.getLogger("partir_historias")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RUTA_GUION = os.path.join(BASE_DIR, "guion.txt")
SEPARADOR = "===NUEVA_HISTORIA==="

# Lo que el render ignora al buscar el título (ver extraer_titulo_y_cuerpo).
PREFIJOS_IGNORADOS = ("#", "===", "📌", "🎙️")

# Cada parte llena como mucho el 80% de un short. El resto es para el título
# leído en voz alta, el "Sigue en la parte N" y el error de la estimación por
# palabras, que se equivoca un 10-15% según cuánto diálogo haya.
MARGEN_PARTE = 0.8

# Más allá de cuatro partes ya no es una serie que se siga, es un largo
# troceado. Esas se quedan esperando a que se abran los largos.
PARTES_MAXIMAS = 4

# Una frase que termina en una de estas no termina ahí ("el Sr. García").
ABREVIATURAS = {"sr.", "sra.", "srta.", "dr.", "dra.", "ud.", "uds.", "lic.", "ing."}

_FIN_DE_FRASE = re.compile(r'(?<=[.!?…])\s+|(?<=[.!?…]["»”’)])\s+')

PROMPT_PARTIR = """Vas a partir una historia narrada en varias partes seguidas, que se \
publicarán como shorts de YouTube una detrás de otra. No reescribes nada: solo eliges \
entre qué frases se corta.

Recibes las frases numeradas. Entre paréntesis va cuántas palabras lleva la historia \
hasta esa frase incluida. Devuelve en "cortes" el número de la ÚLTIMA frase de cada \
parte, salvo la de la última parte: tantos números como partes menos uno, de menor a \
mayor.

Cada parte tiene que terminar en un momento que deje con ganas de ver la siguiente: \
una revelación a medias, una pregunta sin responder, justo antes de que pase algo. \
Nunca a mitad de un diálogo, ni en una frase de transición sin tensión.

Respeta los límites de palabras por parte que se indican."""

SCHEMA_CORTES = {
    "type": "object",
    "properties": {"cortes": {"type": "array", "items": {"type": "integer"}}},
    "required": ["cortes"],
}


def _gvm():
    # Tarde y no arriba: importar el render abre su log en la carpeta de
    # salida, y este módulo lo importa script_writer.py en cada corrida.
    import generar_video_maestro as gvm
    return gvm


def _limites():
    """(palabras que caben en un short, palabras máximas por parte).

    Sale de las mismas constantes que aplazar_si_es_largo, pero sin la
    holgura: aquella existe para no aplazar una historia que quizá cabía, y
    aquí equivocarse hacia ese lado es dejarla atascada. Partir una que
    habría cabido justa cuesta poco — salen dos shorts en vez de uno.
    """
    gvm = _gvm()
    cabe = gvm.DURACION_MAX_SHORT_SEC * gvm.PALABRAS_POR_SEGUNDO
    return cabe, int(cabe * MARGEN_PARTE)


def _contar(texto):
    # La misma limpieza que se le hace al texto antes del TTS, para contar
    # lo mismo que va a sonar.
    return len(_gvm().limpiar_texto_seguro(texto).split())


def _es_continuacion(frase):
    """Una "frase" que empieza en minúscula es la cola de la anterior:
    "—¿Qué haces? —preguntó." no se puede cortar después del "?"."""
    s = frase.lstrip('—–-"«“¿¡ ')
    return bool(s) and s[0].islower()


def _frases(lineas):
    """Las frases del cuerpo, cada una con si cierra un párrafo."""
    frases = []
    for linea in lineas:
        trozos = [t.strip() for t in _FIN_DE_FRASE.split(linea) if t.strip()]
        juntas = []
        for t in trozos:
            if juntas and (_es_continuacion(t)
                           or juntas[-1].split()[-1].lower() in ABREVIATURAS):
                juntas[-1] = f"{juntas[-1]} {t}"
            else:
                juntas.append(t)
        frases += [(f, i == len(juntas) - 1) for i, f in enumerate(juntas)]
    return frases


def analizar(bloque):
    """Qué haría con este bloque. None si no hay nada que partir.

    Devuelve un dict con "titulo" y "palabras", y o bien "partes" (cuántas
    harían falta) o bien "demasiado_larga" (no cabe ni en PARTES_MAXIMAS).
    """
    cabeceras, titulo, cuerpo = [], None, []
    for linea in bloque.splitlines():
        s = linea.strip()
        if not s:
            continue
        if s.startswith(PREFIJOS_IGNORADOS):
            cabeceras.append(s)
        elif titulo is None:
            titulo = s
        else:
            cuerpo.append(s)
    if not titulo or not cuerpo or titulos.parte_de_titulo(titulo):
        return None

    frases = _frases(cuerpo)
    conteos = [_contar(f) for f, _ in frases]
    total = sum(conteos)
    cabe, maximo = _limites()
    if total <= cabe:
        return None

    plan = {"titulo": titulo, "palabras": total, "cabeceras": cabeceras,
            "frases": frases, "conteos": conteos, "maximo": maximo}
    partes = math.ceil(total / maximo)
    if partes > PARTES_MAXIMAS or len(frases) < partes:
        plan["demasiado_larga"] = True
    else:
        plan["partes"] = partes
    return plan


def _tramos(cortes, conteos, partes, maximo, minimo):
    """Los tramos (primera, última frase) si los cortes cumplen, si no None."""
    try:
        cortes = [int(c) for c in cortes]
    except (TypeError, ValueError):
        return None
    if len(cortes) != partes - 1:
        return None
    bordes = [-1] + cortes + [len(conteos) - 1]
    if any(b <= a for a, b in zip(bordes, bordes[1:])):
        return None
    tramos = [(bordes[k] + 1, bordes[k + 1]) for k in range(partes)]
    for a, b in tramos:
        if not (minimo <= sum(conteos[a:b + 1]) <= maximo):
            return None
    return tramos


def _cortes_parejos(conteos, partes):
    """Cortes en la frase más cercana a partes iguales de palabras."""
    total, acumulado = sum(conteos), []
    for c in conteos:
        acumulado.append((acumulado[-1] if acumulado else 0) + c)
    cortes, previo = [], -1
    for k in range(1, partes):
        objetivo = total * k / partes
        # Dejando al menos una frase para cada parte que falta.
        posibles = range(previo + 1, len(conteos) - (partes - k))
        if not posibles:
            return None
        previo = min(posibles, key=lambda i: abs(acumulado[i] - objetivo))
        cortes.append(previo)
    return cortes


def _cortes_de_gemini(client, plan):
    import script_writer as sw
    frases, conteos, partes = plan["frases"], plan["conteos"], plan["partes"]
    acumulado, lineas = 0, []
    for i, ((frase, _), c) in enumerate(zip(frases, conteos)):
        acumulado += c
        lineas.append(f"[{i}] ({acumulado}) {frase}")
    contenido = (
        f"Partes: {partes}\n"
        f"Máximo de palabras por parte: {plan['maximo']}\n"
        f"Mínimo de palabras por parte: {plan['minimo']}\n\n" + "\n".join(lineas)
    )
    resp = client.models.generate_content(
        model=sw.MODEL,
        contents=contenido,
        config=sw.genai_types.GenerateContentConfig(
            system_instruction=PROMPT_PARTIR,
            response_mime_type="application/json",
            response_schema=SCHEMA_CORTES,
        ),
    )
    return (json.loads(resp.text) or {}).get("cortes") or []


def partir(plan, client=None):
    """Los bloques de cada parte, o None si no se consiguió un corte que cumpla.

    Primero se le pide a Gemini dónde cortar; si no hay cliente, falla o
    devuelve cortes que no cumplen los límites, se corta a partes iguales.
    """
    conteos, maximo = plan["conteos"], plan["maximo"]
    partes = plan["partes"]
    # Ninguna parte por debajo de la mitad de lo que le tocaría: una parte 3
    # de veinte palabras no es un short, es un resto.
    plan["minimo"] = int(plan["palabras"] / partes * 0.5)
    tramos, origen = None, "a partes iguales"

    if client is not None:
        try:
            import script_writer as sw
            cortes = sw.con_reintentos(_cortes_de_gemini, client, plan)
            tramos = _tramos(cortes, conteos, partes, maximo, plan["minimo"])
            if tramos:
                origen = "donde eligió Gemini"
            else:
                logger.info(f"  Gemini propuso cortes que no cumplen ({cortes}); "
                            "se parte a partes iguales.")
        except Exception as exc:                  # noqa: BLE001 — hay respaldo
            logger.warning(f"  Gemini no pudo elegir los cortes ({exc}); "
                           "se parte a partes iguales.")

    # A partes iguales casi siempre cabe; solo una frase enorme lo impide, y
    # entonces se prueba con una parte más.
    while tramos is None and partes <= PARTES_MAXIMAS:
        cortes = _cortes_parejos(conteos, partes)
        tramos = cortes is not None and _tramos(cortes, conteos, partes, maximo, 1)
        if not tramos:
            tramos = None
            partes += 1
    if not tramos:
        return None

    plan["origen"] = origen
    return [_bloque_de_parte(plan, a, b, i, len(tramos))
            for i, (a, b) in enumerate(tramos, 1)]


def _bloque_de_parte(plan, primera, ultima, numero, total):
    texto = ""
    for frase, cierra_parrafo in plan["frases"][primera:ultima + 1]:
        texto += frase + ("\n" if cierra_parrafo else " ")
    texto = texto.strip()
    if numero < total:
        texto += f"\n\nSigue en la parte {numero + 1}."
    return "\n".join(plan["cabeceras"]
                     + [titulos.titulo_de_parte(plan["titulo"], numero, total), texto])


def _largos_abiertos():
    try:
        import formato
        return bool(formato.politica()["permite_largos"])
    except Exception as exc:                      # noqa: BLE001 — informativo
        # Sin saberlo se asume lo de siempre, bloqueados: partida, la
        # historia se publica; aplazada, se queda esperando.
        logger.warning(f"  No se pudo saber si los largos están abiertos ({exc}).")
        return False


def partir_si_hace_falta(bloque, client=None):
    """Para script_writer.py: el bloque tal cual, o sus partes."""
    plan = analizar(bloque)
    if not plan:
        return [bloque]
    if plan.get("demasiado_larga"):
        logger.info(f"  Larga de más ({plan['palabras']} palabras, más de "
                    f"{PARTES_MAXIMAS} partes): espera a los largos.")
        return [bloque]
    if _largos_abiertos():
        return [bloque]
    partes = partir(plan, client)
    if not partes:
        return [bloque]
    logger.info(f"  ✂ {plan['palabras']} palabras no caben en un short: "
                f"sale en {len(partes)} partes ({plan['origen']}).")
    return partes


def _cliente_gemini():
    try:
        import script_writer as sw   # carga secretos.env al importarse
    except Exception as exc:                      # noqa: BLE001 — hay respaldo
        logger.warning(f"No se pudo cargar script_writer ({exc}).")
        return None
    if not (os.environ.get("GEMINI_API_KEY") or "").strip():
        return None
    try:
        return sw.genai.Client()
    except Exception as exc:                      # noqa: BLE001 — hay respaldo
        logger.warning(f"No se pudo abrir el cliente de Gemini ({exc}).")
        return None


def _escribir_guion(bloques):
    tmp = RUTA_GUION + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(("\n" + SEPARADOR + "\n").join(bloques) + "\n")
    os.replace(tmp, RUTA_GUION)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Parte en varios shorts las historias largas de la cola.")
    ap.add_argument("--si", action="store_true", help="Hacerlo de verdad.")
    args = ap.parse_args(argv if argv is not None else sys.argv[1:])
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    import limpiar_cola

    bloques = limpiar_cola.bloques_del_guion(RUTA_GUION)
    if not bloques:
        print(f"\n  No hay historias en {RUTA_GUION}.\n")
        return 0

    if _largos_abiertos():
        import formato
        print(f"\n  Los largos están abiertos ({formato.politica()['motivo']}): una historia\n"
              "  larga ya sale como video largo, no hace falta partirla.\n")
        return 0

    hechos = limpiar_cola.ya_renderizados()
    grabadas = {i for i, b in enumerate(bloques, 1) if limpiar_cola.apodo(b) in hechos}
    planes = {i: analizar(b) for i, b in enumerate(bloques, 1) if i not in grabadas}
    largas = {i: p for i, p in planes.items() if p and p.get("partes")}
    sin_arreglo = {i: p for i, p in planes.items() if p and p.get("demasiado_larga")}

    print(f"\n  {len(bloques)} historia(s) en la cola.\n")
    if sin_arreglo:
        print(f"  {len(sin_arreglo)} no cabe(n) ni en {PARTES_MAXIMAS} partes; siguen esperando a los largos:")
        for i, p in sin_arreglo.items():
            print(f"   {i:3d}  {p['titulo'][:50]}  ({p['palabras']} palabras)")
        print()
    if not largas:
        print("  Ninguna otra pasa de lo que cabe en un short. No hay nada que partir.\n")
        return 0

    print(f"  Se partirían {len(largas)}:")
    for i, p in largas.items():
        print(f"   {i:3d}  {p['titulo'][:50]}  {p['palabras']} palabras → {p['partes']} partes")

    primera = min(largas)
    quitar = grabadas if any(i > primera for i in grabadas) else set()
    if quitar:
        print(f"\n  Antes se quitan de la cola las {len(quitar)} que ya tienen video, igual que\n"
              "  «Limpiar»: al meter las partes, las de detrás cambian de número y el\n"
              "  render las volvería a grabar.")

    if not args.si:
        print("\n  Esto era el listado. Para hacerlo:  python partir_historias.py --si\n")
        return 0

    client = _cliente_gemini()
    if client is None:
        print("\n  Sin GEMINI_API_KEY: se corta a partes iguales, sin buscar el suspenso.")

    print()
    nuevos, partidas, quitadas = [], [], []
    for i, b in enumerate(bloques, 1):
        if i in quitar:
            quitadas.append(b)
            continue
        if i in largas:
            partes = partir(largas[i], client)
            if partes:
                nuevos.extend(partes)
                partidas.append(b)
                print(f"   ✓ {largas[i]['titulo'][:50]}: {len(partes)} partes, "
                      f"cortadas {largas[i]['origen']}.")
                continue
            print(f"   ✗ {largas[i]['titulo'][:50]}: no hubo corte que cumpliera; se queda igual.")
        nuevos.append(b)

    if not partidas:
        print("\n  No se partió ninguna; guion.txt no se toca.\n")
        return 0

    # Al historial ANTES de tocar guion.txt, igual que limpiar_cola.py: si
    # algo falla entre medias, mejor una historia repetida que una perdida.
    limpiar_cola.archivar_en_historial(quitadas)
    limpiar_cola.archivar_en_historial(partidas, motivo="Partida en varios shorts")
    respaldo = f"{RUTA_GUION}.bak-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    shutil.copy2(RUTA_GUION, respaldo)
    _escribir_guion(nuevos)

    print(f"\n   ✓ Las originales quedan enteras en {os.path.basename(limpiar_cola.RUTA_HISTORIAL_GUION)}")
    print(f"   ✓ Copia de la cola anterior en {os.path.basename(respaldo)}")
    print(f"   ✓ {RUTA_GUION}: quedan {len(nuevos)} historia(s).\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
