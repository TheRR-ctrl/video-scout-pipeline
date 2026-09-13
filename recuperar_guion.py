"""
Recupera guion.txt a partir de resultado_lote.json.

Para qué sirve: guion.txt vive en la carpeta del repo y está en .gitignore,
así que si esa carpeta se vuelve a clonar (o se borra), el guion se pierde.
resultado_lote.json, en cambio, se guarda junto a los videos —en la SD card,
fuera del repo— y de cada historia renderizada conserva el título, el cuerpo
narrado, la emoción, la fuente y el autor. Con eso alcanza para rearmar
guion.txt completo, incluidas las historias cuyo .mp4 ya se borró.

Lo único que no se guardó es el género del narrador (masculino/femenino),
que decide qué voz se usa. Se infiere del texto buscando concordancias
femeninas en primera persona ("estaba cansada", "me quedé sola"); es una
heurística, no un dato, así que revisa las que marque si te importa mucho.

Uso:
  python recuperar_guion.py              # escribe guion.txt
  python recuperar_guion.py --ver        # solo muestra qué recuperaría
"""
import os
import re
import sys
import json
import argparse
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RUTA_GUION = os.path.join(BASE_DIR, "guion.txt")
SEPARADOR = "\n\n===NUEVA_HISTORIA===\n"

# Marcas de concordancia femenina en primera persona. No busca cualquier
# palabra femenina (la historia puede hablar de otras personas), sino
# construcciones donde quien narra se describe a sí misma.
PISTAS_FEMENINAS = [
    r"\b(?:estaba|quedé|quedaba|sentí|sentía|volví|puse|dejé|vi)\s+(?:muy\s+|bien\s+|super\s+)?\w+ada\b",
    r"\b(?:soy|era|fui|estoy|estaba)\s+(?:la|una)\s+\w+a\b",
    r"\bme\s+quedé\s+\w+a\b",
    r"\byo\s+(?:sola|misma|solita)\b",
    r"\b(?:cansada|harta|sorprendida|enojada|preocupada|nerviosa|tranquila|segura|obligada|acostumbrada)\b",
    r"\bmi\s+esposo\b",
    r"\bmi\s+novio\b",
]


def inferir_genero(texto):
    """Devuelve ('Femenino'|'Masculino', seguro: bool)."""
    aciertos = sum(1 for p in PISTAS_FEMENINAS if re.search(p, texto, re.IGNORECASE))
    if aciertos >= 2:
        return "Femenino", True
    if aciertos == 1:
        return "Femenino", False
    return "Masculino", False


def cargar_completados():
    try:
        import generar_video_maestro as gvm
        cfg = gvm.cargar_config(os.path.join(BASE_DIR, "config.json"))
        carpeta = cfg["carpeta_salida"]
    except Exception as exc:
        raise SystemExit(f"No se pudo leer la configuración: {exc}")

    ruta = os.path.join(carpeta, "resultado_lote.json")
    if not os.path.exists(ruta):
        raise SystemExit(
            f"No se encontró {ruta}.\n"
            f"Ahí es donde generar_video_maestro.py guarda el registro de lo "
            f"renderizado; sin ese archivo no hay nada que recuperar."
        )

    with open(ruta, "r", encoding="utf-8") as f:
        completados = json.load(f).get("completados", [])

    if not completados:
        raise SystemExit(f"{ruta} existe pero no tiene historias registradas.")
    return completados, carpeta


def construir_bloque(video):
    genero, _ = inferir_genero(video.get("cuerpo", ""))
    return (
        f"# Genero: {genero}\n"
        f"# Emocion: {video.get('emocion', 'drama')}\n"
        f"# Fuente: {video.get('fuente_url') or '[desconocida]'}\n"
        f"# Autor: {video.get('autor_original') or '[desconocido]'}\n"
        f"{video.get('titulo', 'Historia de Reddit')}\n"
        f"{video.get('cuerpo', '')}"
    )


def main():
    parser = argparse.ArgumentParser(description="Rearma guion.txt desde resultado_lote.json.")
    parser.add_argument("--ver", action="store_true", help="Solo mostrar, sin escribir.")
    args = parser.parse_args()

    completados, carpeta = cargar_completados()

    con_cuerpo = [v for v in completados if (v.get("cuerpo") or "").strip()]
    sin_cuerpo = len(completados) - len(con_cuerpo)

    print(f"\nresultado_lote.json: {len(completados)} historia(s) registradas en")
    print(f"  {carpeta}\n")
    if sin_cuerpo:
        print(f"  ⚠️  {sin_cuerpo} sin texto guardado (no se pueden recuperar)\n")

    dudosas = []
    for i, v in enumerate(con_cuerpo, 1):
        genero, seguro = inferir_genero(v.get("cuerpo", ""))
        existe = "●" if os.path.exists(v.get("ruta", "")) else "○"
        marca = "" if seguro or genero == "Masculino" else "  ← género inferido, revisar"
        if marca:
            dudosas.append(i)
        print(f"  {existe} {i:2d}. [{genero[:3]}] {v.get('titulo', '?')[:52]}{marca}")

    print(f"\n  ● = el .mp4 todavía existe   ○ = solo queda el texto")

    if args.ver:
        print("\n(--ver: no se escribió nada)")
        return

    if os.path.exists(RUTA_GUION):
        respaldo = f"{RUTA_GUION}.bak-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        os.replace(RUTA_GUION, respaldo)
        print(f"\nguion.txt anterior movido a {os.path.basename(respaldo)}")

    with open(RUTA_GUION, "w", encoding="utf-8") as f:
        f.write(SEPARADOR.join(construir_bloque(v) for v in con_cuerpo))

    print(f"\n✅ guion.txt recuperado con {len(con_cuerpo)} historia(s).")
    if dudosas:
        print(f"   Género inferido (revisa si quieres): historias {dudosas}")
    print(
        "\nAhora puedes rehacerlas con el formato actual:\n"
        "  python rehacer_guiones.py        # opcional: largo libre + cierre narrado\n"
        "  python generar_video_maestro.py  # subtítulos nuevos\n"
    )


if __name__ == "__main__":
    main()
