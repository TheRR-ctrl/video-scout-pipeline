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

import cola      # cola de candidatos e historial compartidos con script_writer.py
import formato   # si ahora mismo tiene sentido buscar historias largas
import almacen   # escritura atómica de config_trends.json

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
    # Un subreddit que no existe o que se cerró no rompe nada: si su grupo
    # falla entero se salta con un aviso y la corrida sigue (ver
    # subreddits_por_tanda más abajo, sobre por qué van en grupos y no uno a
    # uno). Si alguno falla siempre, quítalo de config_trends.json.
    "subreddits": [
        # Drama / dilemas
        "AmItheAsshole",
        "AITAH",
        "AmIOverreacting",
        "relationship_advice",
        "relationships",
        "confession",
        "confessions",
        "TrueOffMyChest",
        "offmychest",
        "AmItheButtface",
        "TwoHotTakes",
        "BestofRedditorUpdates",
        # Familia difícil: el mismo material que los dilemas, pero con la
        # relación de por medio, que es lo que engancha en los comentarios.
        "JUSTNOMIL",
        "raisedbynarcissists",
        "insaneparents",
        "EntitledParents",
        "EntitledPeople",
        # Venganza (final feliz para quien narra)
        "ProRevenge",
        "pettyrevenge",
        "NuclearRevenge",
        "MaliciousCompliance",
        "ChoosingBeggars",
        "IDontWorkHereLady",
        # Trabajo de cara al público: anécdota cerrada, con remate, contada en
        # primera persona. Es lo que mejor se convierte en guion.
        "TalesFromRetail",
        "TalesFromTheCustomer",
        "TalesFromYourServer",
        "TalesFromTheFrontDesk",
        "TalesFromTechSupport",
        "StoriesAboutKevin",
        # Suspenso / misterio (experiencias reales, no ficción tipo nosleep)
        "UnresolvedMysteries",
        "Glitch_in_the_Matrix",
        "LetsNotMeet",
        "Paranormal",
        "HighStrangeness",
        # Comedia / torpezas
        "tifu",
        "mildlyinfuriating",
    ],
    "time_filter": "day",
    "limite_por_subreddit": 15,
    # Cuántos subreddits van juntos en cada request. Reddit permite pedir
    # varios de una vez con r/sub1+sub2+.../top/.rss (sintaxis pública y
    # documentada, no un truco) y el límite de peticiones es por IP, no por
    # subreddit: agrupar de a pocos baja las peticiones de 36 a menos de 10
    # y con eso los 429 que se veían con una petición por subreddit. El precio
    # es que ya no es "los N mejores DE CADA subreddit": dentro de un mismo
    # grupo, Reddit devuelve los mejores del conjunto, así que un subreddit
    # con posts de menos puntuación puede quedar tapado por otro del mismo
    # grupo. Grupos pequeños (3-4) reparten mejor que uno solo con los 36.
    "subreddits_por_tanda": 4,
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


def subreddits_configurados():
    """La lista de subreddits tal como queda en config_trends.json (o la de
    por defecto si el archivo no la tocó). La usa el panel para pintarla."""
    crudo = almacen.cargar(RUTA_CONFIG, {})
    return list(crudo.get("subreddits", CONFIG_DEFAULT["subreddits"]))


_RE_SUBREDDIT = re.compile(r"^[A-Za-z0-9_]{3,21}$")


def _normalizar_subreddit(nombre):
    """Valida contra las reglas de nombre de Reddit (letras/números/guion
    bajo, 3 a 21 caracteres). No es solo cosmético: este nombre va sin
    escapar dentro de la URL del feed en obtener_posts_publicos, y algo como
    "sub1+sub2" colaría un feed combinado que además rompe el conteo de rank
    por subreddit que se explica en escanear()."""
    nombre = (nombre or "").strip()
    if nombre.lower().startswith("r/"):
        nombre = nombre[2:]
    if not _RE_SUBREDDIT.fullmatch(nombre):
        raise ValueError(
            "Ese no parece un nombre de subreddit válido (solo letras, números "
            "y guion bajo, de 3 a 21 caracteres, sin \"r/\" ni espacios)."
        )
    return nombre


def _verificar_subreddit_existe(nombre):
    """Un GET liviano al RSS del subreddit, para no guardar un nombre mal
    escrito sin darse cuenta — mismo espíritu que resolver_channel_id en
    youtube_scout.py, pero sin caché: aquí se llama una sola vez, al agregar.

    Reddit no tiene una vía de búsqueda pública sin bloqueo (ver el aviso al
    principio del archivo), así que esto no busca por nombre, solo confirma
    que el nombre exacto existe antes de guardarlo.

    Un solo alta es una sola petición, sin problema. Varias seguidas sin
    pausa sí pueden toparse con el 429 de RATE_LIMIT_SEG, y como un 429 se
    deja pasar sin marcar error (ver arriba), ese subreddit se guarda SIN
    haberse comprobado de verdad, justo cuando más falta hace.
    """
    resp = requests.get(f"https://www.reddit.com/r/{nombre}/top/.rss",
                         params={"limit": 1}, headers={"User-Agent": _UA_NAVEGADOR}, timeout=10)
    if resp.status_code == 404:
        raise ValueError(f'El subreddit "r/{nombre}" no existe (404).')
    if resp.status_code not in (403, 429):
        # 403/429 es bloqueo o rate limit, no "no existe": no tiene sentido
        # rechazar el alta por una causa ajena al nombre, así que se deja pasar.
        resp.raise_for_status()


def agregar_subreddit(nombre):
    """Agrega un subreddit a config_trends.json, comprobando antes que
    existe. Devuelve (lista, se_agregó)."""
    nombre = _normalizar_subreddit(nombre)
    crudo = almacen.cargar(RUTA_CONFIG, {})
    subs = list(crudo.get("subreddits", CONFIG_DEFAULT["subreddits"]))
    if nombre in subs:
        return subs, False
    _verificar_subreddit_existe(nombre)
    subs.append(nombre)
    crudo["subreddits"] = subs
    almacen.guardar(RUTA_CONFIG, crudo)
    return subs, True


def quitar_subreddit(nombre):
    """Quita un subreddit de config_trends.json. Devuelve (lista, se_quitó)."""
    nombre = _normalizar_subreddit(nombre)
    crudo = almacen.cargar(RUTA_CONFIG, {})
    subs = list(crudo.get("subreddits", CONFIG_DEFAULT["subreddits"]))
    if nombre not in subs:
        return subs, False
    subs.remove(nombre)
    crudo["subreddits"] = subs
    almacen.guardar(RUTA_CONFIG, crudo)
    return subs, True


_RE_CUERPO = re.compile(r"<!--\s*SC_OFF\s*-->(.*?)<!--\s*SC_ON\s*-->", re.DOTALL)


def _limpiar_contenido_html(contenido_crudo):
    """Convierte el HTML del <content> del feed en texto plano, quitando el
    pie que Reddit agrega ("submitted by ... [link] [comments]").

    Reddit envuelve el cuerpo del post entre <!-- SC_OFF --> y <!-- SC_ON -->,
    y el pie queda siempre fuera: por eso se recorta por ahí.

    Antes se cortaba por la primera aparición de "submitted by", y eso truncaba
    la historia cuando la frase salía en el propio texto del post —cosa que
    pasa justo en estos subreddits, donde se habla de informes, denuncias y
    formularios. Lo malo no era perder el final, era perderlo en silencio: si
    lo que quedaba pasaba de min_palabras_texto, Gemini recibía media historia,
    la reescribía como si estuviera entera, y salía un video contando un
    principio sin final.

    El corte por "submitted by" se queda como respaldo para una entrada sin
    esos marcadores, donde es mejor que nada.
    """
    texto = html.unescape(contenido_crudo or "")
    cuerpo = _RE_CUERPO.search(texto)
    texto = cuerpo.group(1) if cuerpo else texto.split("submitted by")[0]
    texto = re.sub(r"<!--.*?-->", "", texto, flags=re.DOTALL)
    texto = re.sub(r"<[^>]+>", " ", texto)
    return re.sub(r"\s+", " ", texto).strip()


def obtener_posts_publicos(grupo_subreddits, cfg):
    """Lee el feed RSS/Atom público 'top' de un grupo de subreddits en una
    sola petición (solo lectura): r/sub1+sub2+.../top/.rss.

    Es sintaxis pública de Reddit, no un bypass — la misma que usa cualquiera
    que arme un feed combinado a mano en reddit.com. Cada <entry> trae su
    subreddit real en <category term="...">, así que la atribución por post
    no se pierde por venir en un pedido conjunto.

    El límite de la petición sube con el tamaño del grupo (grupos más grandes
    piden más) para que un subreddit no se quede sin sitio solo por compartir
    petición con otros — el tope real de Reddit son 100 resultados, más que
    eso no da más.
    """
    url = f"https://www.reddit.com/r/{'+'.join(grupo_subreddits)}/top/.rss"
    limite = min(100, cfg["limite_por_subreddit"] * len(grupo_subreddits))
    params = {"t": cfg["time_filter"], "limit": limite}
    headers = {"User-Agent": _UA_NAVEGADOR}

    resp = requests.get(url, params=params, headers=headers, timeout=15)
    if resp.status_code == 429:
        # Un solo reintento con una espera más larga antes de rendirse. Si
        # sale bien, "0 fallados" en el reporte no dice que no hubo ningún
        # 429 — solo que ninguno se quedó sin resolver. Se deja constancia
        # aquí para que un bloqueo silencioso no se lea como que no pasó.
        logger.info(f"429 de reddit.com en r/{'+'.join(grupo_subreddits)}, "
                    f"reintentando en {RATE_LIMIT_SEG * 2:.0f}s...")
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
        cat_el = entry.find("a:category", _NS)
        # De qué subreddit vino este post en concreto, no el grupo entero.
        subreddit_real = cat_el.get("term") if cat_el is not None else ""

        posts.append({
            "id": post_id,
            "subreddit": subreddit_real,
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
    # rank_en_subreddit tiene que seguir siendo eso — la posición DENTRO de
    # su propio subreddit, no dentro de la respuesta del grupo. Si se dejara
    # como la posición en el feed combinado, el subreddit que domina el grupo
    # (el de posts con más puntuación) se quedaría con los ranks 0, 1, 2...
    # y el resto del grupo entraría siempre detrás — exactamente el mismo
    # problema de "uno tapa a los demás" que se evitó al pedir, pero
    # reapareciendo al elegir. Contarlo por subreddit real deshace eso: cada
    # subreddit vuelve a competir por sus propios ranks bajos, igual que
    # cuando se pedía uno a la vez.
    rank_por_sub = {}

    subs = cfg["subreddits"]
    tanda = max(1, cfg.get("subreddits_por_tanda", 1))
    grupos = [subs[i:i + tanda] for i in range(0, len(subs), tanda)]

    for i, grupo in enumerate(grupos):
        if i > 0:
            time.sleep(RATE_LIMIT_SEG)

        logger.info(f"Escaneando r/{'+'.join(grupo)}...")
        try:
            posts = obtener_posts_publicos(grupo, cfg)
        except Exception as exc:
            logger.warning(f"No se pudo leer el grupo r/{'+'.join(grupo)}: {exc}")
            contar["subs_fallidos"] += len(grupo)
            continue
        contar["subs_ok"] += len(grupo)

        for post in posts:
            sub_real = post["subreddit"]
            rank = rank_por_sub.get(sub_real, 0)
            rank_por_sub[sub_real] = rank + 1
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
                "subreddit": post["subreddit"],
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
    #
    # Mientras los largos estén bloqueados esa reserva es contraproducente:
    # generar_video_maestro aplaza la historia después de escribir el guion y
    # narrarla, así que cada cupo largo es una llamada a Gemini y un TTS que
    # acaban en la nevera, quitándole el sitio a un short que sí se publica.
    umbral = cfg["umbral_palabras_historia_larga"]
    cupos_largos = cfg["min_candidatos_largos"] if formato.politica()["permite_largos"] else 0
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
    total_cola, agregados, repetidos, podados = cola.agregar_candidatos(nuevos)

    logger.info(f"{agregados} candidato(s) nuevo(s); {total_cola} en cola en {RUTA_CANDIDATOS}")
    if repetidos:
        # Descartados por parecerse a una historia ya escrita, no por el id.
        # Verlo en el log importa: si sale alto, la fuente está reciclando.
        logger.info(f"{repetidos} descartado(s) por ser la misma historia que una ya contada")
    if podados:
        # La cola tiene tope y caducidad: lo que sobra se suelta aquí, no se
        # queda engordando el archivo para no atenderse nunca.
        logger.info(
            f"{podados} candidato(s) soltado(s) de la cola por caducados o por pasar "
            f"del tope de {cola.TOPE_CANDIDATOS}"
        )
    for c in nuevos:
        print(f" • [{c['subreddit']}] {c['titulo_original']} (rank={c['rank_en_subreddit']})")

    if args.diagnostico or agregados == 0:
        explicar(cfg, contar, pendientes_antes, agregados, total_cola)


if __name__ == "__main__":
    main(sys.argv[1:])
