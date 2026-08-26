"""
Recorte de títulos para YouTube.

YouTube corta a 100 caracteres y rechaza '<' y '>'. Hasta ahora el ajuste era
un `titulo[:100]` seco, que parte la palabra por la mitad y deja cosas como
"…no sabía lo que grabó mi abu". Gemini tampoco respeta siempre el límite
que se le pide, así que hace falta recortar de todas formas — pero
recortando por palabras.

Vive en su propio módulo, sin dependencias, para que lo usen igual
publisher.py (que arrastra las librerías de Google) y el panel, que no
puede importarlas.
"""
import re

LIMITE_YOUTUBE = 100

# Cortes naturales: si cae uno lo bastante avanzado, la parte de delante ya
# es una frase entera y queda mejor que cualquier recorte con puntos
# suspensivos.
SEPARADORES = ("—", "–", " - ", ":", ";", "…", "|", "·")

# Terminar en una de estas deja la frase colgando ("no sabía lo que…" está
# bien; "no sabía lo que grabó mi…" no). Se sueltan al recortar.
COLGANTES = {
    "de", "del", "la", "el", "los", "las", "un", "una", "unos", "unas",
    "que", "y", "o", "en", "a", "al", "con", "sin", "por", "para", "su",
    "sus", "mi", "mis", "tu", "tus", "lo", "le", "les", "se", "me", "te",
    "es", "fue", "era", "muy", "más", "pero", "porque", "cuando", "como",
}


def largo_youtube(texto):
    """Longitud como la cuenta YouTube.

    Se mide en unidades UTF-16, no en caracteres de Python: un emoji fuera
    del plano básico cuenta doble para la API, y contarlo como uno solo
    dejaría pasar títulos que luego rechaza. Para texto normal ambos números
    coinciden, así que esto solo cambia algo cuando de verdad importa.
    """
    return len(texto.encode("utf-16-le")) // 2


def limpiar_titulo(texto):
    """Quita lo que YouTube rechaza y normaliza los espacios."""
    texto = (texto or "").replace("<", "").replace(">", "")
    texto = re.sub(r"\s+", " ", texto).strip()
    return texto


def _soltar_colgantes(texto):
    palabras = texto.split()
    while palabras and (palabras[-1].lower().strip(".,;:!¡?¿\"'") in COLGANTES):
        palabras.pop()
    return " ".join(palabras)


def recortar_titulo(texto, limite=LIMITE_YOUTUBE):
    """Devuelve un título que cabe en YouTube sin partir palabras.

    Se intenta, en este orden:
      1. Dejarlo tal cual, si ya cabe.
      2. Cortar en un separador natural (— : ; |), si deja al menos la mitad
         del título: lo que queda es una frase completa y no necesita puntos
         suspensivos.
      3. Cortar por palabras y cerrar con "…", soltando las palabras que
         dejarían la frase colgando.
    """
    texto = limpiar_titulo(texto)
    if largo_youtube(texto) <= limite:
        return texto

    # 2. corte natural
    mejor = ""
    for sep in SEPARADORES:
        pos = 0
        while True:
            pos = texto.find(sep, pos + 1)
            if pos == -1:
                break
            trozo = texto[:pos].strip().rstrip(",;:-–—·|")
            if largo_youtube(trozo) <= limite and largo_youtube(trozo) > largo_youtube(mejor):
                mejor = trozo
    if mejor and largo_youtube(mejor) >= limite // 2:
        return mejor

    # 3. corte por palabras, reservando espacio para los puntos suspensivos
    corte = ""
    for palabra in texto.split():
        tentativa = (corte + " " + palabra).strip()
        if largo_youtube(tentativa) > limite - 1:
            break
        corte = tentativa

    corte = _soltar_colgantes(corte).rstrip(",;:-–—·| ")
    if not corte:
        # Una sola palabra más larga que el límite: no hay corte por palabras
        # posible y hay que partirla, pero al menos se avisa con el "…".
        return texto[: limite - 1].rstrip() + "…"
    return corte + "…"
