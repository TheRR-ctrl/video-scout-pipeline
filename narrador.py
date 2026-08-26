"""
Género de quien narra la historia — decide si el video lleva voz de mujer o
de hombre.

Hasta ahora salía solo de la cabecera "# Genero:" que escribe script_writer.py.
Eso falla de tres maneras: si Gemini se equivoca al elegirlo nadie lo corrige;
si el guion viene editado a mano o recuperado no hay cabecera y caía siempre
en masculina; y si la cabecera decía "Femenina" en vez de "Femenino" tampoco
la reconocía.

Este módulo lee el texto. La concordancia en primera persona del español lo
delata sin ambigüedad ("me quedé callada" solo lo dice una mujer), y el texto
es lo que de verdad se va a narrar: si la cabecera y el cuerpo no coinciden,
el cuerpo es el que manda.

Sin dependencias, para que lo puedan importar tanto script_writer.py (que
arrastra las librerías de Google) como generar_video_maestro.py.
"""
import re
import unicodedata

# Sustantivos con los que alguien se nombra a sí mismo. Van tras "soy", "era"
# o similares, nunca sueltos: "mi hermana" habla de otra persona, no de quien
# narra, y contarlo sería justo el error que esto viene a evitar.
ROLES = {
    "femenino": ["hija", "madre", "mama", "esposa", "hermana", "novia",
                 "abuela", "tia", "nieta", "suegra", "cunada", "senora",
                 "mujer", "chava", "muchacha", "maestra", "enfermera",
                 "unica", "primera", "responsable"],
    "masculino": ["hijo", "padre", "papa", "esposo", "marido", "hermano",
                  "novio", "abuelo", "tio", "nieto", "suegro", "cunado",
                  "senor", "hombre", "chavo", "muchacho", "maestro",
                  "enfermero", "unico", "primero", "responsable"],
}

# Verbos que solo pueden tener a quien narra como sujeto. "estaba" queda
# fuera a propósito: sirve igual para él, ella o yo, y en "mi madre estaba
# cansada" el adjetivo no habla de quien narra. Solo entra con "yo" delante.
ARRANQUES = [
    "me quede", "me senti", "me puse", "me vi", "me deje", "me quedo sola",
    "quede", "sali", "acabe", "termine", "llegue", "me fui", "me sentia",
    "me levante", "me despedi", "me case", "me divorcie", "yo estaba",
    "yo era", "yo seguia", "me tenian", "me dejaron", "me hicieron sentir",
]

# Adjetivos frecuentes cuya forma delata el género. Se comprueban tras uno de
# los arranques de arriba, más la terminación -ada/-ido genérica de los
# participios.
ADJETIVOS = {
    "femenino": ["sola", "cansada", "harta", "tranquila", "contenta",
                 "nerviosa", "segura", "callada", "quieta", "muerta",
                 "perdida", "sorprendida", "destrozada", "agotada"],
    "masculino": ["solo", "cansado", "harto", "tranquilo", "contento",
                  "nervioso", "seguro", "callado", "quieto", "muerto",
                  "perdido", "sorprendido", "destrozado", "agotado"],
}


def _sin_acentos(texto):
    return "".join(
        c for c in unicodedata.normalize("NFD", str(texto).lower())
        if unicodedata.category(c) != "Mn"
    )


def puntuar_genero(texto):
    """Cuántas marcas de cada género hay en el texto, y cuáles.

    Se devuelven las marcas encontradas, no solo el recuento: cuando la
    decisión sale rara, poder enseñar las tres palabras que la causaron es
    la diferencia entre corregir el vocabulario y adivinar.
    """
    t = _sin_acentos(texto)
    marcador = {"femenino": [], "masculino": []}

    for genero in ("femenino", "masculino"):
        # 1. "soy/era la hija", "yo, la responsable"
        roles = "|".join(ROLES[genero])
        for m in re.finditer(
            r"\b(?:soy|era|fui|sigo siendo|yo,?)\s+(?:la|el|una|un)?\s*(" + roles + r")\b", t
        ):
            marcador[genero].append(m.group(0).strip())

        # 2. concordancia tras un verbo que solo puede ser de quien narra
        arranques = "|".join(re.escape(a) for a in ARRANQUES)
        adjetivos = "|".join(ADJETIVOS[genero])
        # La terminación genérica va aparte de la lista para cazar también
        # participios que no estén enumerados (traicionada, engañado…).
        fin = r"\w+ad[ao]|\w+id[ao]"
        for m in re.finditer(
            r"\b(?:" + arranques + r")\s+(?:muy\s+|tan\s+|bien\s+)?(" + adjetivos + r"|" + fin + r")\b", t
        ):
            palabra = m.group(1)
            # Solo cuenta si la terminación corresponde a ESTE género: el
            # patrón genérico -ad[ao] caza los dos, hay que decidir cuál.
            if palabra in ADJETIVOS[genero] or palabra.endswith("a" if genero == "femenino" else "o"):
                marcador[genero].append(m.group(0).strip())

    return marcador


def detectar_genero_narrador(texto, margen=2):
    """'femenino', 'masculino' o None si el texto no lo deja claro.

    Se exige ventaja de `margen` marcas: una sola aparición puede ser de otro
    personaje que se coló en el patrón, y equivocarse aquí significa narrar
    la historia entera con la voz cambiada, que se nota en el primer segundo.
    """
    marcador = puntuar_genero(texto)
    fem, masc = len(marcador["femenino"]), len(marcador["masculino"])
    if fem - masc >= margen:
        return "femenino"
    if masc - fem >= margen:
        return "masculino"
    return None


def leer_cabecera_genero(texto):
    """El '# Genero:' del guion, aceptando cómo lo escriba cada cual.

    Antes solo valía 'femenino' o 'mujer' exactos. 'Femenina', 'F' o
    'narradora' caían en masculino sin decir nada, y como masculino es el
    valor por defecto, un fallo de lectura y una elección deliberada se veían
    igual.
    """
    m = re.search(r"^\s*#?\s*g[eé]nero\s*[:=]\s*(\S+)", texto, re.IGNORECASE | re.MULTILINE)
    if not m:
        return None
    val = _sin_acentos(m.group(1))
    if val.startswith(("femenin", "mujer", "narradora", "chica", "ella")) or val == "f":
        return "femenino"
    if val.startswith(("masculin", "hombre", "narrador", "chico", "el")) or val == "m":
        return "masculino"
    return None
