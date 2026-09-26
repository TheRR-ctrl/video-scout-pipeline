"""
Descargar Fondos — trae videos de fondo verticales desde Pexels
(https://www.pexels.com) y Pixabay (https://pixabay.com), para no depender
solo del gameplay que tengas metido a mano en la SD.

Las dos fuentes se usan a la vez por defecto y el cupo se reparte: un tema
que una tiene flojo lo completa la otra. Falta una clave, se avisa y se sigue
con la que haya.

De dónde sale la idea: los proyectos short-video-maker y MoneyPrinterTurbo
sacan su material de archivo de bancos como Pexels en vez de pedirte los
archivos. Es lo único de esos dos que encaja aquí — el resto (Remotion sobre
Node, whisper, modelos de voz locales, una WebUI de Streamlit) no corre en
Termux o duplica lo que este repo ya hace mejor: los subtítulos ya salen
palabra por palabra de los eventos WordBoundary de edge-tts, que es más
exacto que transcribir con whisper el audio que nosotros mismos generamos.

No sustituye al gameplay, lo acompaña: el renderizador elige entre TODOS los
archivos "fondo_vertical*" que encuentre, así que esto solo añade variedad a
la baraja.

Tampoco es parte de la corrida diaria —el material de archivo no necesita
cambiar por video—, igual que actualizar_musica.py: se corre cada tanto.

Requiere: pip install requests
Credenciales: PEXELS_API_KEY y/o PIXABAY_API_KEY (las dos gratis y sin
tarjeta, en https://www.pexels.com/api/ y https://pixabay.com/api/docs/).
Se ponen desde la pestaña Ajustes del panel, que las escribe en secretos.env.

Licencias: la de Pexels y la Pixabay Content License permiten uso comercial y
modificar, y ninguna exige atribución. Aun así se guarda quién grabó cada
clip, y de qué fuente salió, en
pipeline_state/fondos_atribucion.json, que es lo correcto y además te deja
citarlos en la descripción si quieres.

Uso:
  python descargar_fondos.py                     # rellena todos los temas
  python descargar_fondos.py --ver               # qué bajaría, sin bajar nada
  python descargar_fondos.py --tema lluvia       # solo un tema
  python descargar_fondos.py --cuantos 5         # cuántos guardar por tema
  python descargar_fondos.py --horizontal        # para los videos largos
  python descargar_fondos.py --fuente pixabay    # solo una de las dos
"""
import os
import re
import argparse
import logging

import almacen   # leer y escribir los .json de estado
import secretos  # carga secretos.env si las claves no están en el entorno

try:
    import requests
except ImportError:
    requests = None

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CARPETA_ESTADO = os.path.join(BASE_DIR, "pipeline_state")
RUTA_HISTORIAL = os.path.join(CARPETA_ESTADO, "fondos_historial.json")
RUTA_ATRIBUCION = os.path.join(CARPETA_ESTADO, "fondos_atribucion.json")

PEXELS_API = "https://api.pexels.com/videos/search"
PIXABAY_API = "https://pixabay.com/api/videos/"

# Ninguna de las dos exige atribución, pero se guarda igual (ver más abajo).
LICENCIAS = {
    "pexels": "Pexels License (https://www.pexels.com/license/)",
    "pixabay": "Pixabay Content License (https://pixabay.com/service/license-summary/)",
}
# Cada fuente necesita su propia clave, y son gratis y sin tarjeta las dos.
CLAVES = {
    "pexels": ("PEXELS_API_KEY", "https://www.pexels.com/api/"),
    "pixabay": ("PIXABAY_API_KEY", "https://pixabay.com/api/docs/"),
}
FONDOS_POR_TEMA = 3

# Búsquedas pensadas para material que se mire sin robarle atención a la
# narración: textura y movimiento lento, nada con cara hablando ni con texto
# quemado en la imagen. Tampoco personas reales haciendo algo arriesgado
# (conducir, trepar, fuego): a YouTube le basta ese clip de fondo para marcar
# el video por seguridad infantil, sin que la narración tenga nada que ver.
# "driving road night" y "road trip window" volvían con gente al volante y
# así se descartaron — "highway pov" y "empty/night" solo traen carretera.
TEMAS = {
    "lluvia":    ["rain window night", "rain drops glass", "wet street night"],
    "ciudad":    ["city night timelapse", "neon street night", "traffic lights night"],
    "carretera": ["empty highway night", "highway pov", "road night timelapse"],
    "abstracto": ["abstract liquid ink", "smoke slow motion dark", "particles dark background"],
    "naturaleza":["ocean waves slow", "forest fog", "clouds timelapse"],
}

# Un clip demasiado corto se nota al repetirse (el render lo pone en bucle con
# -stream_loop -1), y uno muy largo solo ocupa sitio en el teléfono.
SEGUNDOS_MINIMO = 8
SEGUNDOS_MAXIMO = 45

# Tope duro por archivo. Esto vive en un móvil: más vale quedarse sin un clip
# que llenar el almacenamiento. Se corta a mitad de la descarga si se pasa.
MB_MAXIMO = 60

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("descargar_fondos")

cargar_json = almacen.cargar
guardar_json = almacen.guardar


class DemasiadoGrande(Exception):
    """El clip se pasa del tope de MB. Es una propiedad del clip, no del
    momento: se apunta en el historial para no volver a encontrarlo mañana."""


def usuario_de(video):
    """El bloque `user` de Pexels, que a veces viene como null en vez de como
    objeto. Con .get("user", {}) eso devolvería None y reventaría al encadenar."""
    return video.get("user") or {}


class ClaveRechazada(Exception):
    """La fuente dijo que no a la credencial, no al contenido.

    Se distingue de «no encontré nada» porque el remedio es opuesto: con una
    clave mala, reintentar con otra búsqueda da exactamente el mismo 401. Sin
    esto, una clave mal pegada salía como quince avisos de búsqueda fallida y
    un «no se encontró nada usable» por cada tema, que es justo el mensaje que
    te manda a mirar donde no es.
    """


def limpiar_nombre(nombre):
    return re.sub(r"[^\w\-]", "_", nombre or "na")[:40]


def candidato(fuente, ident, url, ancho, alto, duracion, autor, perfil, pagina):
    """Un clip elegido, ya sin nada específico de la fuente de donde salió.

    El id va con el nombre de la fuente delante a propósito: Pexels y Pixabay
    numeran sus clips por su cuenta y los dos empiezan por números bajos, así
    que un id pelado haría que un clip de una se tomara por uno ya bajado de
    la otra y no se descargara nunca."""
    return {
        "fuente": fuente,
        "id": f"{fuente}:{ident}",
        "id_corto": str(ident),
        "url": url,
        "ancho": ancho,
        "alto": alto,
        "duracion": duracion,
        "autor": autor,
        "perfil": perfil,
        "pagina": pagina,
        "licencia": LICENCIAS[fuente],
    }


def elegir_archivo_pexels(video, vertical):
    """De las versiones que ofrece Pexels, la más cercana a 1080x1920 (o
    1920x1080) sin pasarse.

    Pasarse no aporta nada: el render escala y recorta a la resolución de
    salida, así que bajar un 4K solo gasta datos y batería para tirar píxeles.
    """
    alto_objetivo = 1920 if vertical else 1080
    candidatos = [
        f for f in video.get("video_files", [])
        if f.get("file_type") == "video/mp4" and f.get("height") and f.get("width")
    ]
    if not candidatos:
        return None
    cabe = [f for f in candidatos if f["height"] <= alto_objetivo]
    # Si todas se pasan, la más pequeña de las grandes es el menor de los males.
    return (max(cabe, key=lambda f: f["height"]) if cabe
            else min(candidatos, key=lambda f: f["height"]))


def buscar_pexels(clave, consultas, cantidad, vertical, ya_bajados):
    """Va probando consultas hasta juntar suficientes clips nuevos y usables.

    Devuelve candidatos normalizados (ver `candidato`), no la respuesta cruda:
    así el resto del script no sabe de qué fuente vino cada clip."""
    elegidos = []
    vistos = set()
    for consulta in consultas:
        if len(elegidos) >= cantidad:
            break
        params = {
            "query": consulta,
            "orientation": "portrait" if vertical else "landscape",
            "size": "medium",
            "per_page": 15,
        }
        try:
            resp = requests.get(PEXELS_API, params=params,
                                headers={"Authorization": clave}, timeout=20)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            codigo = getattr(getattr(exc, "response", None), "status_code", None)
            if codigo in (401, 403):
                raise ClaveRechazada(
                    f"Pexels rechazó la clave (HTTP {codigo}). Revisa PEXELS_API_KEY: "
                    "se copia entera desde https://www.pexels.com/api/ → Your API Key."
                )
            if codigo == 429:
                raise ClaveRechazada(
                    "Pexels dice que has hecho demasiadas peticiones (HTTP 429). "
                    "El límite gratuito se renueva solo; prueba más tarde."
                )
            logger.warning(f"    fallo consultando «{consulta}»: {exc}")
            continue

        for video in data.get("videos", []):
            if len(elegidos) >= cantidad:
                break
            vid = str(video.get("id"))
            if f"pexels:{vid}" in ya_bajados or vid in vistos:
                continue
            dur = video.get("duration") or 0
            if not (SEGUNDOS_MINIMO <= dur <= SEGUNDOS_MAXIMO):
                continue
            archivo = elegir_archivo_pexels(video, vertical)
            if not archivo:
                continue
            vistos.add(vid)
            usuario = usuario_de(video)
            elegidos.append(candidato(
                fuente="pexels", ident=vid, url=archivo["link"],
                ancho=archivo["width"], alto=archivo["height"], duracion=dur,
                autor=usuario.get("name"), perfil=usuario.get("url"),
                pagina=video.get("url"),
            ))
    return elegidos


def elegir_archivo_pixabay(video, vertical):
    """De las versiones que ofrece Pixabay, la más cercana a la resolución de
    salida sin pasarse, y descartando las que no van en el formato pedido.

    Pixabay no tiene parámetro de orientación en la API de videos (la de
    imágenes sí), así que el filtro va aquí, mirando las medidas de verdad.
    Y a diferencia de Pexels, la respuesta trae `size` en bytes: se puede
    descartar un clip demasiado pesado sin llegar a empezar la descarga.
    """
    alto_objetivo = 1920 if vertical else 1080
    tope = MB_MAXIMO * 1024 * 1024
    candidatos = []
    for version in (video.get("videos") or {}).values():
        if not isinstance(version, dict):
            continue
        ancho, alto = version.get("width") or 0, version.get("height") or 0
        if not (ancho and alto) or not version.get("url"):
            continue
        # Un clip apaisado recortado a 1080x1920 se queda en una tira central
        # sin contexto; mejor no bajarlo.
        if vertical and alto <= ancho:
            continue
        if not vertical and ancho <= alto:
            continue
        if (version.get("size") or 0) > tope:
            continue
        candidatos.append(version)

    if not candidatos:
        return None
    cabe = [f for f in candidatos if f["height"] <= alto_objetivo]
    return (max(cabe, key=lambda f: f["height"]) if cabe
            else min(candidatos, key=lambda f: f["height"]))


def buscar_pixabay(clave, consultas, cantidad, vertical, ya_bajados):
    """Lo mismo que buscar_pexels, contra Pixabay. Devuelve candidatos
    normalizados, así que a partir de aquí da igual de dónde salieron."""
    elegidos = []
    vistos = set()
    for consulta in consultas:
        if len(elegidos) >= cantidad:
            break
        params = {
            "key": clave,
            "q": consulta,
            "video_type": "film",
            "safesearch": "true",
            "per_page": 20,   # el mínimo que acepta Pixabay es 3
        }
        try:
            resp = requests.get(PIXABAY_API, params=params, timeout=20)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            codigo = getattr(getattr(exc, "response", None), "status_code", None)
            if codigo in (400, 401, 403):
                # Pixabay contesta 400 a una clave mal formada y 401/403 a una
                # que no vale; las tres se arreglan mirando la credencial, no
                # cambiando de búsqueda.
                raise ClaveRechazada(
                    f"Pixabay rechazó la clave (HTTP {codigo}). Revisa "
                    "PIXABAY_API_KEY: se copia entera desde "
                    "https://pixabay.com/api/docs/ estando con la sesión abierta."
                )
            if codigo == 429:
                raise ClaveRechazada(
                    "Pixabay dice que has hecho demasiadas peticiones (HTTP 429). "
                    "El límite gratuito se renueva solo; prueba más tarde."
                )
            logger.warning(f"    fallo consultando «{consulta}»: {exc}")
            continue

        for video in data.get("hits", []):
            if len(elegidos) >= cantidad:
                break
            vid = str(video.get("id"))
            if f"pixabay:{vid}" in ya_bajados or vid in vistos:
                continue
            dur = video.get("duration") or 0
            if not (SEGUNDOS_MINIMO <= dur <= SEGUNDOS_MAXIMO):
                continue
            archivo = elegir_archivo_pixabay(video, vertical)
            if not archivo:
                continue
            vistos.add(vid)
            # En Pixabay el autor es una cadena suelta, no un objeto, y no
            # viene la URL del perfil: se arma con el id, que es como enlaza
            # la propia web.
            autor = video.get("user")
            user_id = video.get("user_id")
            perfil = (f"https://pixabay.com/users/{autor}-{user_id}/"
                      if autor and user_id else None)
            elegidos.append(candidato(
                fuente="pixabay", ident=vid, url=archivo["url"],
                ancho=archivo["width"], alto=archivo["height"], duracion=dur,
                autor=autor, perfil=perfil, pagina=video.get("pageURL"),
            ))
    return elegidos


BUSCADORES = {"pexels": buscar_pexels, "pixabay": buscar_pixabay}


def descargar(url, destino):
    """Baja el clip a trozos y aborta si se pasa del tope.

    A trozos y no de una: un mp4 entero en memoria en un teléfono es cómo se
    consigue que Android mate el proceso. Y el archivo a medias se borra —un
    fondo truncado haría fallar el render mucho más tarde, cuando ya no se
    sabría de dónde salió.
    """
    tope = MB_MAXIMO * 1024 * 1024
    escritos = 0
    # El temporal va oculto y SIN el prefijo fondo_: si Android mata el
    # proceso aquí, lo que quede no debe parecerse a un fondo de verdad. El
    # panel y estado.py cuentan los fondos con glob("fondo_vertical*") sin
    # mirar la extensión, y un "fondo_vertical_x.mp4.parcial" se colaba.
    parcial = os.path.join(os.path.dirname(destino),
                           "." + os.path.basename(destino) + ".parcial")
    try:
        with requests.get(url, stream=True, timeout=90) as resp:
            resp.raise_for_status()
            with open(parcial, "wb") as f:
                for trozo in resp.iter_content(chunk_size=1 << 18):
                    if not trozo:
                        continue
                    escritos += len(trozo)
                    if escritos > tope:
                        raise DemasiadoGrande(f"pasa de {MB_MAXIMO} MB")
                    f.write(trozo)
        os.replace(parcial, destino)
        return escritos
    except Exception:
        if os.path.exists(parcial):
            os.remove(parcial)
        raise


def ya_hay(prefijo, tema):
    return [f for f in os.listdir(BASE_DIR)
            if f.startswith(f"{prefijo}_{tema}_") and f.endswith(".mp4")]


def fusionar_atribucion(ruta):
    """Mezcla la atribución de una tanda ajena en la del teléfono.

    El artefacto de Actions trae el registro del runner, que arranca en
    blanco. Descomprimirlo encima borraría la atribución acumulada aquí, así
    que llega con otro nombre y se funde con esto en vez de pisarla."""
    try:
        nueva = cargar_json(ruta, None)
    except (OSError, ValueError) as exc:
        raise SystemExit(f"El archivo de atribución está ilegible ({ruta}): {exc}")
    if not isinstance(nueva, dict):
        raise SystemExit(f"No se pudo leer un registro de atribución en: {ruta}")

    actual = cargar_json(RUTA_ATRIBUCION, {})
    antes = len(actual)
    actual.update(nueva)
    guardar_json(RUTA_ATRIBUCION, actual)
    logger.info(f"Atribución: {len(actual) - antes} clip(s) nuevo(s), "
                f"{len(actual)} en total.")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description="Bajar videos de fondo desde Pexels y Pixabay.")
    p.add_argument("--fuente", choices=("ambas", "pexels", "pixabay"), default="ambas",
                   help="de dónde bajar (por defecto las dos, repartiendo el cupo)")
    p.add_argument("--tema", help="solo este tema (" + ", ".join(TEMAS) + ")")
    p.add_argument("--cuantos", type=int, default=FONDOS_POR_TEMA,
                   help=f"cuántos clips mantener por tema (por defecto {FONDOS_POR_TEMA})")
    p.add_argument("--horizontal", action="store_true",
                   help="material apaisado para los videos largos")
    p.add_argument("--ver", action="store_true", help="enseñar qué bajaría, sin bajar nada")
    p.add_argument("--fusionar", metavar="ARCHIVO",
                   help="añadir la atribución de una tanda bajada en otro sitio "
                        "(el .json que trae el artefacto de Actions) y salir")
    args = p.parse_args(argv)

    if args.fusionar:
        return fusionar_atribucion(args.fusionar)

    if requests is None:
        raise SystemExit("Falta el paquete 'requests'. Instálalo con: pip install requests")

    pedidas = ("pexels", "pixabay") if args.fuente == "ambas" else (args.fuente,)
    claves = {}
    for fuente in pedidas:
        nombre_var, donde = CLAVES[fuente]
        valor = os.environ.get(nombre_var)
        if valor:
            claves[fuente] = valor
        elif args.fuente != "ambas":
            raise SystemExit(
                f"Falta la variable de entorno {nombre_var}. Es gratis y sin tarjeta: "
                f"{donde}. Ponla en secretos.env desde la pestaña Ajustes del panel "
                "(ese archivo no se sube al repo)."
            )
        else:
            # Con --fuente ambas, que falte una no es motivo para no usar la
            # otra: se avisa y se sigue.
            logger.info(f"Sin {nombre_var}: se omite {fuente}.")
    if not claves:
        raise SystemExit(
            "No hay ninguna clave configurada. Hace falta PEXELS_API_KEY o "
            "PIXABAY_API_KEY; se ponen en la pestaña Ajustes del panel."
        )

    temas = TEMAS
    if args.tema:
        if args.tema not in TEMAS:
            raise SystemExit(f"Tema desconocido: {args.tema}. Hay: {', '.join(TEMAS)}")
        temas = {args.tema: TEMAS[args.tema]}

    vertical = not args.horizontal
    prefijo = "fondo_vertical" if vertical else "fondo_horizontal"

    historial = cargar_json(RUTA_HISTORIAL, [])
    # Los historiales de antes de Pixabay guardan el id de Pexels pelado. Se
    # leen como suyos: si no, todo lo bajado hasta hoy volvería a bajarse.
    ya_bajados = {h if ":" in h else f"pexels:{h}" for h in historial}
    atribucion = cargar_json(RUTA_ATRIBUCION, {})

    for tema, consultas in temas.items():
        existentes = ya_hay(prefijo, tema)
        faltan = max(0, args.cuantos - len(existentes))
        if faltan == 0:
            logger.info(f"{tema}: ya hay {len(existentes)} clip(s), no se baja nada.")
            continue

        # El cupo se reparte entre las fuentes disponibles, y lo que no
        # consiga una lo intenta la siguiente: así un tema que Pexels tiene
        # flojo se completa con Pixabay en vez de quedarse corto.
        logger.info(f"{tema}: buscando {faltan} clip(s) nuevo(s)...")
        encontrados = []
        restantes = list(claves.items())
        for i, (fuente, clave) in enumerate(restantes):
            pendientes = faltan - len(encontrados)
            if pendientes <= 0:
                break
            # Al repartir, la primera fuente no se lleva todo el cupo salvo
            # que sea la última que queda.
            cupo = pendientes if i == len(restantes) - 1 else max(1, pendientes // 2)
            try:
                encontrados += BUSCADORES[fuente](clave, consultas, cupo,
                                                  vertical, ya_bajados)
            except ClaveRechazada as exc:
                # No se para todo: la otra fuente puede seguir sirviendo. Se
                # quita esta de la lista para no repetir el mismo error en
                # cada tema que queda.
                logger.warning(f"  ⚠️ {exc}")
                claves.pop(fuente, None)
        if not claves:
            if not args.ver:
                guardar_json(RUTA_HISTORIAL, sorted(ya_bajados))
                guardar_json(RUTA_ATRIBUCION, atribucion)
            raise SystemExit("Ninguna fuente aceptó su clave; no hay nada más que intentar.")
        if not encontrados:
            logger.warning(f"{tema}: no se encontró nada usable.")
            continue

        for clip in encontrados:
            autor = limpiar_nombre(clip["autor"])
            nombre = (f"{prefijo}_{tema}_{clip['fuente']}_{autor}_"
                      f"{clip['id_corto']}.mp4")
            destino = os.path.join(BASE_DIR, nombre)
            etiqueta = (f"{nombre} ({clip['ancho']}x{clip['alto']}, "
                        f"{clip['duracion']}s)")
            if args.ver:
                logger.info(f"  · bajaría {etiqueta}")
                continue
            try:
                bytes_ = descargar(clip["url"], destino)
                logger.info(f"  ✅ {etiqueta} — {bytes_/1024/1024:.1f} MB")
                ya_bajados.add(clip["id"])
                atribucion[nombre] = {
                    "fuente": clip["fuente"],
                    "autor": clip["autor"],
                    "perfil": clip["perfil"],
                    "pagina": clip["pagina"],
                    "licencia": clip["licencia"],
                }
            except DemasiadoGrande as exc:
                # Se apunta igual: el clip pesa lo que pesa, y sin esto se
                # vuelve a encontrar y a bajar (hasta 60 MB de datos) en cada
                # corrida futura hasta que se corte sola otra vez.
                logger.warning(f"  ⚠️ {nombre}: {exc} — descartado para siempre")
                ya_bajados.add(clip["id"])
            except Exception as exc:
                logger.warning(f"  ⚠️ {nombre}: {exc}")

        # Al terminar cada tema y no solo al final de todo: si Android mata el
        # proceso a mitad del siguiente tema, lo que ya está en disco conserva
        # su registro y su atribución.
        if not args.ver:
            guardar_json(RUTA_HISTORIAL, sorted(ya_bajados))
            guardar_json(RUTA_ATRIBUCION, atribucion)

    if not args.ver:
        guardar_json(RUTA_HISTORIAL, sorted(ya_bajados))
        guardar_json(RUTA_ATRIBUCION, atribucion)
    logger.info("Listo.")


if __name__ == "__main__":
    main()
