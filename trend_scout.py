"""
Trend Scout — detecta historias con potencial viral en Reddit.

Salida: pipeline_state/candidatos.json
Cada corrida evita repetir posts ya vistos (pipeline_state/historial_vistos.json).

Requiere: pip install requests

Nota sobre el acceso a Reddit:
  La API oficial de Reddit requiere pasar por su formulario de "Reddit Data
  Access" (revisión de la Responsible Builder Policy), que no siempre se
  concede para uso personal. El endpoint JSON público sin autenticación
  (www.reddit.com/r/<sub>/top/.json) también está bloqueado por el filtro
  anti-bot de Reddit, incluso con un User-Agent de navegador real.

  Lo que sí funciona sin bloqueo es el **feed RSS/Atom** de cada subreddit
  (www.reddit.com/r/<sub>/top/.rss), que trae el texto completo de cada post.
  Es una vía pública, de solo lectura, pensada para lectores de RSS — el
  mismo tipo de acceso que cualquier agregador de noticias usa. Igual que
  antes: nunca se postea, comenta, vota ni envía mensajes, y cada historia
  conserva su autor y URL original para dar atribución en el video (ver
  "autor"/"url" en el candidato), en vez de presentar el contenido como
  propio.

  El RSS no expone score ni número de comentarios (a diferencia del JSON),
  así que el filtrado por "viralidad" se basa en el orden del feed /top/
  (ya viene rankeado por Reddit) en vez de umbrales de score/comentarios.

  Si Reddit empieza a bloquear también el RSS, hay que espaciar más las
  corridas (ver RATE_LIMIT_SEG) o retomar la vía de la API oficial.
"""
import os
import sys
import re
import html
import json
import time
import logging
import argparse
from xml.etree import ElementTree as ET

import cola  # cola de candidatos e historial compartidos con script_writer.py

try:
    import requests
except ImportError:
    requests = None

CARPETA_ESTADO = cola.CARPETA_ESTADO
RUTA_CANDIDATOS = cola.RUTA_CANDIDATOS
RUTA_HISTORIAL = cola.RUTA_HISTORIAL
RUTA_CONFIG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config_trends.json")

RATE_LIMIT_SEG = 12.0  # pausa entre requests a reddit.com; el RSS es más estricto que el JSON con el rate limit

CONFIG_DEFAULT = {
    "subreddits": [
        # Drama / dilemas
        "AmItheAsshole",
        "relationship_advice",
        "relationships",
        "confession",
        "AmItheButtface",
        # Venganza (final feliz para quien narra)
        "ProRevenge",
        "pettyrevenge",
        "MaliciousCompliance",
        "EntitledParents",
        # Suspenso / misterio (experiencias reales, no ficción tipo nosleep)
        "UnresolvedMysteries",
        "Glitch_in_the_Matrix",
        # Comedia / torpezas
        "tifu",
        "mildlyinfuriating",
    ],
    "time_filter": "day",
    "limite_por_subreddit": 15,
    "min_palabras_texto": 80,
    "max_palabras_texto": 1800,
    "max_candidatos_salida": 20,
    # Para que salgan también videos largos (3-5 min): de los candidatos que
    # superen este umbral de palabras, se reservan algunos cupos aunque no
    # sean los mejor rankeados en /top/. El resto de los cupos se llena
    # normal, por ranking.
    "umbral_palabras_historia_larga": 400,
    "min_candidatos_largos": 3,
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("trend_scout")

_UA_NAVEGADOR = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_NS = {"a": "http://www.w3.org/2005/Atom"}


def cargar_config():
    cfg = dict(CONFIG_DEFAULT)
    if os.path.exists(RUTA_CONFIG):
        with open(RUTA_CONFIG, "r", encoding="utf-8") as f:
            cfg.update(json.load(f))
    return cfg


cargar_historial = cola.cargar_historial
guardar_historial = cola.guardar_historial


def _limpiar_contenido_html(contenido_crudo):
    """Convierte el HTML del <content> del feed en texto plano, quitando el
    pie que Reddit agrega ("submitted by ... [link] [comments]")."""
    texto = html.unescape(contenido_crudo or "")
    texto = re.sub(r"<!--.*?-->", "", texto, flags=re.DOTALL)
    texto = texto.split("submitted by")[0]
    texto = re.sub(r"<[^>]+>", " ", texto)
    return re.sub(r"\s+", " ", texto).strip()


def obtener_posts_publicos(subreddit, cfg):
    """Lee el feed RSS/Atom público 'top' de un subreddit (solo lectura)."""
    url = f"https://www.reddit.com/r/{subreddit}/top/.rss"
    params = {"t": cfg["time_filter"], "limit": cfg["limite_por_subreddit"]}
    headers = {"User-Agent": _UA_NAVEGADOR}

    resp = requests.get(url, params=params, headers=headers, timeout=15)
    if resp.status_code == 429:
        # Un solo reintento con una espera más larga antes de rendirse.
        time.sleep(RATE_LIMIT_SEG * 2)
        resp = requests.get(url, params=params, headers=headers, timeout=15)
    if resp.status_code in (429, 403):
        raise RuntimeError(f"Bloqueado por reddit.com ({resp.status_code}).")
    resp.raise_for_status()

    root = ET.fromstring(resp.text)
    posts = []
    for entry in root.findall("a:entry", _NS):
        post_id = (entry.findtext("a:id", default="", namespaces=_NS) or "").replace("t3_", "")
        titulo = entry.findtext("a:title", default="", namespaces=_NS) or ""
        texto = _limpiar_contenido_html(entry.findtext("a:content", default="", namespaces=_NS))
        link_el = entry.find("a:link", _NS)
        url_post = link_el.get("href") if link_el is not None else ""
        autor = entry.findtext("a:author/a:name", default="", namespaces=_NS) or ""
        autor = autor.replace("/u/", "").strip()

        posts.append({
            "id": post_id,
            "titulo": titulo,
            "texto": texto,
            "url": url_post,
            "autor": autor,
        })
    return posts


def escanear(cfg, contar=None):
    """Devuelve los candidatos NUEVOS de esta pasada.

    No toca el historial: un post se marca como visto cuando script_writer.py
    consigue convertirlo en guion, no cuando el scout lo ve. Así, si Gemini
    falla, la historia sigue en la cola para el siguiente intento en vez de
    quemarse.

    `contar` es un dict opcional donde se acumula por qué se descartó cada
    post; lo usa --diagnostico para explicar un escaneo que salió vacío.
    """
    if requests is None:
        raise RuntimeError("Falta el paquete 'requests'. Instálalo con: pip install requests")

    if contar is None:
        contar = {}
    for k in ("leidos", "ya_vistos", "ya_en_cola", "sin_texto", "muy_corto", "muy_largo", "nuevos", "subs_ok", "subs_fallidos"):
        contar.setdefault(k, 0)

    vistos = cargar_historial()
    en_cola = cola.ids_en_cola()
    candidatos = []

    for i, nombre_sub in enumerate(cfg["subreddits"]):
        if i > 0:
            time.sleep(RATE_LIMIT_SEG)

        logger.info(f"Escaneando r/{nombre_sub}...")
        try:
            posts = obtener_posts_publicos(nombre_sub, cfg)
        except Exception as exc:
            logger.warning(f"No se pudo leer r/{nombre_sub}: {exc}")
            contar["subs_fallidos"] += 1
            continue
        contar["subs_ok"] += 1

        for rank, post in enumerate(posts):
            contar["leidos"] += 1
            post_id = post["id"]
            if not post_id:
                continue
            if post_id in vistos:
                contar["ya_vistos"] += 1
                continue
            if post_id in en_cola:
                contar["ya_en_cola"] += 1
                continue
            texto = post["texto"]
            if not texto:
                contar["sin_texto"] += 1
                continue

            num_palabras = len(texto.split())
            if num_palabras < cfg["min_palabras_texto"]:
                contar["muy_corto"] += 1
                continue
            if num_palabras > cfg["max_palabras_texto"]:
                contar["muy_largo"] += 1
                continue

            autor = post["autor"]
            candidatos.append({
                "id": post_id,
                "subreddit": nombre_sub,
                "titulo_original": post["titulo"],
                "texto_original": texto,
                # El feed RSS no trae score/num_comments; usamos la posición
                # en el ranking /top/ (ya ordenado por Reddit) como proxy.
                "rank_en_subreddit": rank,
                "url": post["url"],
                # Se conserva para dar atribución en la descripción del video
                # (evita presentar la historia como propia).
                "autor": f"u/{autor}" if autor and autor != "[deleted]" else "[autor eliminado]",
                # Cuántas veces script_writer intentó reescribirlo y falló.
                "intentos": 0,
            })
            en_cola.add(post_id)
            contar["nuevos"] += 1

    candidatos.sort(key=lambda c: c["rank_en_subreddit"])

    # Reserva algunos cupos para historias largas (para que también salgan
    # videos de varios minutos), aunque no sean las mejor rankeadas.
    umbral = cfg["umbral_palabras_historia_larga"]
    cupos_largos = cfg["min_candidatos_largos"]
    largas = [c for c in candidatos if len(c["texto_original"].split()) >= umbral]

    seleccionados = largas[:cupos_largos]
    ids_ya_elegidos = {c["id"] for c in seleccionados}
    for c in candidatos:
        if len(seleccionados) >= cfg["max_candidatos_salida"]:
            break
        if c["id"] not in ids_ya_elegidos:
            seleccionados.append(c)
            ids_ya_elegidos.add(c["id"])

    seleccionados.sort(key=lambda c: c["rank_en_subreddit"])
    return seleccionados


def explicar(cfg, contar, pendientes_antes, agregados, total_cola):
    """Informe legible de por qué salieron (o no salieron) historias nuevas."""
    # Se lee con .get porque este informe existe justo para cuando algo salió
    # mal: si el escaneo abortó antes de llenar los contadores, un KeyError
    # aquí taparía el error de verdad con uno mío.
    vistos = cargar_historial()
    print("")
    print("─── Diagnóstico del escaneo ───")
    print(f" Subreddits leídos ok : {contar.get('subs_ok', 0)} de {len(cfg['subreddits'])}"
          + (f"  ({contar.get('subs_fallidos', 0)} fallaron)" if contar.get('subs_fallidos', 0) else ""))
    print(f" Posts leídos         : {contar.get('leidos', 0)}")
    print(f"   • ya usados antes  : {contar.get('ya_vistos', 0)}")
    print(f"   • ya en la cola    : {contar.get('ya_en_cola', 0)}")
    print(f"   • sin texto (link) : {contar.get('sin_texto', 0)}")
    print(f"   • muy cortos (<{cfg['min_palabras_texto']} palabras) : {contar.get('muy_corto', 0)}")
    print(f"   • muy largos (>{cfg['max_palabras_texto']} palabras) : {contar.get('muy_largo', 0)}")
    print(f"   • NUEVOS           : {contar.get('nuevos', 0)}")
    print("")
    print(f" Cola antes           : {pendientes_antes} candidato(s) sin usar")
    print(f" Agregados ahora      : {agregados}")
    print(f" Cola ahora           : {total_cola}")
    print(f" Historial            : {len(vistos)} post(s) ya convertidos en guion")
    print("")

    if contar.get('subs_ok', 0) == 0:
        print(" ⛔ Reddit no respondió en ningún subreddit. Suele ser bloqueo por")
        print("    rate limit (429/403). Espera un rato y vuelve a correrlo, o sube")
        print("    RATE_LIMIT_SEG en trend_scout.py.")
    elif contar.get('nuevos', 0) == 0 and contar.get('ya_vistos', 0) >= max(1, contar.get('leidos', 0) // 2):
        print(" ℹ️  Casi todo el /top/ del día ya se usó. Opciones:")
        print("    • cambiar \"time_filter\" a \"week\" o \"month\" en config_trends.json")
        print("    • agregar más subreddits")
        print("    • olvidar el historial viejo:  python trend_scout.py --olvidar-historial 30")
    elif contar.get('nuevos', 0) == 0 and total_cola > 0:
        print(" ℹ️  No hay posts nuevos, pero la cola NO está vacía: corre")
        print("    python script_writer.py  para convertir los que quedan.")
    elif contar.get('nuevos', 0) == 0:
        print(" ℹ️  No hay nada nuevo que cumpla los filtros de longitud.")
        print("    Prueba a bajar min_palabras_texto o a subir limite_por_subreddit.")


def main(argv=None):
    # argv explícito, y sin caer a sys.argv por omisión: pipeline.py llama a
    # main() sin argumentos, y argparse se comería los flags del pipeline
    # (--hasta, --forzar…) abortando la etapa con un error de uso absurdo.
    ap = argparse.ArgumentParser(description="Busca historias nuevas en Reddit y las deja en la cola.")
    ap.add_argument("--diagnostico", action="store_true",
                    help="Escanea y explica por qué salieron (o no) historias nuevas.")
    ap.add_argument("--estado", action="store_true",
                    help="Solo muestra el estado de la cola y del historial, sin escanear.")
    ap.add_argument("--olvidar-historial", nargs="?", const=0, type=int, metavar="N",
                    help="Borra el historial de posts vistos (deja los N más recientes; sin N, lo borra entero).")
    args = ap.parse_args(argv or [])

    cfg = cargar_config()

    if args.olvidar_historial is not None:
        vistos = sorted(cargar_historial())
        quedan = set(vistos[-args.olvidar_historial:]) if args.olvidar_historial else set()
        guardar_historial(quedan)
        logger.info(f"Historial: {len(vistos)} → {len(quedan)} post(s). "
                    "Las historias viejas pueden volver a aparecer.")
        return

    if args.estado:
        pendientes = cola.cargar_pendientes()
        print(f" Cola      : {len(pendientes)} candidato(s) sin convertir en guion")
        for c in pendientes[:20]:
            print(f"   • [{c.get('subreddit','?')}] {c.get('titulo_original','')[:70]}")
        print(f" Historial : {len(cargar_historial())} post(s) ya usados")
        return

    pendientes_antes = len(cola.cargar_pendientes())
    contar = {}
    nuevos = escanear(cfg, contar)

    # Se AGREGAN a la cola: los pendientes que script_writer aún no consumió
    # se conservan. (Antes esta línea sobrescribía el archivo entero, así que
    # un escaneo vacío borraba candidatos que nunca se llegaron a usar.)
    total_cola, agregados = cola.agregar_candidatos(nuevos)

    logger.info(f"{agregados} candidato(s) nuevo(s); {total_cola} en cola en {RUTA_CANDIDATOS}")
    for c in nuevos:
        print(f" • [{c['subreddit']}] {c['titulo_original']} (rank={c['rank_en_subreddit']})")

    if args.diagnostico or agregados == 0:
        explicar(cfg, contar, pendientes_antes, agregados, total_cola)


if __name__ == "__main__":
    main(sys.argv[1:])
