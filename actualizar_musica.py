"""
Actualizar Música — descarga pistas nuevas de música libre de derechos desde
Jamendo (https://www.jamendo.com) para rotar el fondo musical de los videos.

  python actualizar_musica.py           # rellena hasta 3 pistas por emocion
  python actualizar_musica.py --rotar   # cambia las que ya sonaron por otras
  python actualizar_musica.py --rotar --con-datos   # sin esperar al wifi

Sin --rotar solo RELLENA: si ya hay 3 pistas por emocion no descarga nada,
por muchas veces que se corra. Eso sirve para empezar, pero no para que la
musica cambie — las mismas 12 canciones se reparten entre todos los videos.

Con --rotar si cambia: mira que pistas ya suenan en algun video renderizado
(generar_video_maestro apunta cual uso en resultado_lote.json), baja otras
tantas, y solo entonces aparta las gastadas a musica_usadas/. Una por una:
si Jamendo no responde, la emocion se queda con las que tenia y nunca sin
ninguna. Es lo que corre solo despues de cada tanda de renders, tanto desde
el pipeline como desde el panel.

Requiere: pip install requests
Credenciales: variable de entorno JAMENDO_CLIENT_ID (gratis en
https://devportal.jamendo.com/ → Manage Apps → Add a new application).

Solo descarga pistas con licencia que permite uso comercial y sin cláusula
"No Derivatives" (evita problemas al mezclarlas con la narración). Guarda la
atribución de cada pista (artista, licencia, URL) en
pipeline_state/musica_atribucion.json para poder incluirla en la descripción
del video si la licencia lo exige (CC-BY).
"""
import os
import re
import sys
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
RUTA_HISTORIAL = os.path.join(CARPETA_ESTADO, "musica_historial.json")
RUTA_ATRIBUCION = os.path.join(CARPETA_ESTADO, "musica_atribucion.json")
# Las pistas gastadas no se borran, se apartan: si una rotacion deja la
# biblioteca sin gracia, volver atras es mover archivos de vuelta.
CARPETA_USADAS = os.path.join(BASE_DIR, "musica_usadas")

JAMENDO_API = "https://api.jamendo.com/v3.0/tracks/"
PISTAS_POR_EMOCION = 3  # cuántas pistas mantener por categoría

# Tags de Jamendo por emoción (ver detectar_emocion_historia en
# generar_video_maestro.py para las mismas 4 categorías). Jamendo combina
# varios tags a la vez con lógica "Y" (casi nunca hay resultados), así que
# se consulta un tag a la vez y se van juntando resultados de varios.
TAGS_POR_EMOCION = {
    "drama": ["sad", "dramatic", "emotional", "melancholic", "piano"],
    "venganza": ["dark", "intense", "epic", "angry", "action"],
    "suspenso": ["dark", "ambient", "cinematic", "tension", "mysterious"],
    "comedia": ["happy", "funny", "upbeat", "comedy", "fun"],
}

# Licencias Creative Commons que sí permiten uso comercial y derivados
# (necesario para poder mezclar la pista con la narración/voiceover).
LICENCIAS_PERMITIDAS = ["by", "by-sa"]

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("actualizar_musica")


cargar_json = almacen.cargar
guardar_json = almacen.guardar


def buscar_pistas(client_id, tags, cantidad, ya_descargadas):
    """Prueba un tag y una licencia a la vez (Jamendo combina varios tags con
    lógica "Y", que casi nunca da resultados) hasta juntar suficientes pistas
    nuevas, válidas y no repetidas."""
    elegidas = []
    vistas_en_esta_busqueda = set()

    for tag in tags:
        for licencia in LICENCIAS_PERMITIDAS:
            if len(elegidas) >= cantidad:
                return elegidas

            params = {
                "client_id": client_id,
                "format": "json",
                "limit": 10,
                "tags": tag,
                "license_cc": licencia,
                "audioformat": "mp32",
                "include": "musicinfo",
                "order": "popularity_total",
            }
            try:
                resp = requests.get(JAMENDO_API, params=params, timeout=20)
                resp.raise_for_status()
                data = resp.json()
            except Exception as exc:
                logger.warning(f"    fallo consultando tag={tag} licencia={licencia}: {exc}")
                continue

            for track in data.get("results", []):
                if len(elegidas) >= cantidad:
                    break
                track_id = str(track.get("id"))
                if track_id in ya_descargadas or track_id in vistas_en_esta_busqueda:
                    continue
                if not track.get("audiodownload_allowed"):
                    continue
                vistas_en_esta_busqueda.add(track_id)
                elegidas.append(track)

    return elegidas


def descargar_pista(track, destino):
    url = track.get("audiodownload") or track.get("audio")
    if not url:
        return False
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    with open(destino, "wb") as f:
        f.write(resp.content)
    return True


def limpiar_nombre_archivo(nombre):
    return re.sub(r"[^\w\-]", "_", nombre)[:60]


def descargar_lote(pistas, ya_descargadas, atribucion):
    """Baja las pistas que se le den y devuelve los nombres que aterrizaron.

    Apunta la atribucion de cada una segun la descarga, no al final: si el
    proceso muere a media tanda, lo que ya esta en disco tiene su autor y su
    licencia escritos, que es lo que exige la CC-BY.
    """
    bajadas = []
    for track in pistas:
        artista = limpiar_nombre_archivo(track.get("artist_name", "na"))
        emocion = track["_emocion"]
        nombre_archivo = f"musica_{emocion}_{artista}_{track['id']}.mp3"
        destino = os.path.join(BASE_DIR, nombre_archivo)
        try:
            if descargar_pista(track, destino):
                logger.info(f"  ✅ {nombre_archivo} ({track.get('artist_name')} - {track.get('name')})")
                ya_descargadas.add(str(track["id"]))
                atribucion[nombre_archivo] = {
                    "artista": track.get("artist_name"),
                    "titulo": track.get("name"),
                    "licencia_url": track.get("license_ccurl"),
                    "pagina_jamendo": track.get("shareurl"),
                }
                bajadas.append(nombre_archivo)
            else:
                logger.warning(f"  ⚠️ Sin URL de descarga para track {track['id']}")
        except Exception as exc:
            logger.warning(f"  ⚠️ Fallo descargando track {track['id']}: {exc}")
    return bajadas


def pistas_de(emocion):
    return sorted(
        f for f in os.listdir(BASE_DIR)
        if f.startswith(f"musica_{emocion}_") and f.endswith(".mp3")
    )


def buscar_para(client_id, emocion, cuantas, ya_descargadas):
    """buscar_pistas para una emocion, dejando apuntado a cual pertenece."""
    try:
        pistas = buscar_pistas(client_id, TAGS_POR_EMOCION[emocion], cuantas, ya_descargadas)
    except Exception as exc:
        logger.warning(f"{emocion}: fallo al consultar Jamendo: {exc}")
        return []
    for t in pistas:
        t["_emocion"] = emocion
    return pistas


def pistas_ya_sonadas():
    """Las que suenan en algun video ya renderizado y siguen en la carpeta.

    generar_video_maestro apunta la pista de cada video en resultado_lote.json
    (musica_archivo). Es la unica fuente que sabe que se uso de verdad: por el
    nombre del archivo no se distingue una cancion estrenada de una que lleva
    en veinte videos.
    """
    import publisher
    ruta = os.path.join(publisher.cargar_config()["carpeta_salida"], "resultado_lote.json")
    if not os.path.exists(ruta):
        return set()
    lote = cargar_json(ruta, {})
    usadas = {v.get("musica_archivo") for v in lote.get("completados", [])}
    return {u for u in usadas if u and os.path.exists(os.path.join(BASE_DIR, u))}


def apartar(nombre):
    os.makedirs(CARPETA_USADAS, exist_ok=True)
    os.replace(os.path.join(BASE_DIR, nombre), os.path.join(CARPETA_USADAS, nombre))


def rotar(client_id):
    """Cambia por otras las pistas que ya sonaron, sin dejar ninguna emocion
    a cero: primero entra la nueva, y solo entonces sale la gastada.

    Solo toca las cuatro emociones que baja este script. Una musica_fondo_*
    puesta a mano se queda donde esta: no la trajo Jamendo y no hay con que
    reemplazarla.
    """
    gastadas = pistas_ya_sonadas()
    if not gastadas:
        logger.info("Ninguna de las pistas que hay ha sonado todavia. No toca rotar.")
        return 0

    historial = cargar_json(RUTA_HISTORIAL, [])
    ya_descargadas = set(historial)
    atribucion = cargar_json(RUTA_ATRIBUCION, {})
    entraron = salieron = 0

    for emocion in TAGS_POR_EMOCION:
        suyas = [g for g in sorted(gastadas) if g.startswith(f"musica_{emocion}_")]
        if not suyas:
            continue
        logger.info(f"{emocion}: {len(suyas)} pista(s) ya sonaron; buscando recambio...")
        nuevas = descargar_lote(
            buscar_para(client_id, emocion, len(suyas), ya_descargadas),
            ya_descargadas, atribucion,
        )
        # Ni una mas que las que entraron. Con Jamendo caido esto no hace
        # nada, que es mejor que dejar la emocion sin musica.
        for vieja in suyas[:len(nuevas)]:
            try:
                apartar(vieja)
                logger.info(f"  → {vieja} a musica_usadas/")
                salieron += 1
            except OSError as exc:
                logger.warning(f"  ⚠️ No se pudo apartar {vieja}: {exc}")
        entraron += len(nuevas)

    guardar_json(RUTA_HISTORIAL, sorted(ya_descargadas))
    guardar_json(RUTA_ATRIBUCION, atribucion)
    logger.info(f"Rotacion: {entraron} pista(s) nuevas, {salieron} apartada(s).")
    return 0


def hay_via_libre_para_descargar(con_datos):
    """La musica son megas: sin wifi se deja para la proxima, igual que las
    subidas. Con --con-datos, SUBIR_CON_DATOS=1 o solo_wifi=false, adelante."""
    import publisher
    if (con_datos or os.environ.get("SUBIR_CON_DATOS") == "1"
            or not publisher.cargar_config().get("solo_wifi", True)):
        return True
    if publisher.conectado_a_wifi():
        return True
    logger.info("Sin wifi: la rotacion se deja para la proxima tanda.\n"
                "   Para hacerla ahora igual: python actualizar_musica.py --rotar --con-datos")
    return False


def main(argv=None):
    ap = argparse.ArgumentParser(description="Trae musica libre de derechos desde Jamendo.")
    ap.add_argument("--rotar", action="store_true",
                    help="Cambia las pistas que ya sonaron por otras nuevas.")
    ap.add_argument("--con-datos", action="store_true",
                    help="Rotar sin esperar al wifi.")
    args = ap.parse_args(argv if argv is not None else sys.argv[1:])

    if requests is None:
        raise SystemExit("Falta el paquete 'requests'. Instálalo con: pip install requests")

    client_id = os.environ.get("JAMENDO_CLIENT_ID")
    if not client_id:
        aviso = (
            "Falta la variable de entorno JAMENDO_CLIENT_ID. Consíguela gratis en "
            "https://devportal.jamendo.com/ (Manage Apps → Add a new application)."
        )
        if args.rotar:
            # La rotacion corre sola despues de cada tanda de renders: sin
            # clave se salta y ya, no tiñe de rojo una corrida que fue bien.
            logger.info(aviso)
            return 0
        raise SystemExit(aviso)

    if args.rotar:
        if not hay_via_libre_para_descargar(args.con_datos):
            return 0
        return rotar(client_id)

    historial = cargar_json(RUTA_HISTORIAL, [])
    ya_descargadas = set(historial)
    atribucion = cargar_json(RUTA_ATRIBUCION, {})

    for emocion, tags in TAGS_POR_EMOCION.items():
        existentes = pistas_de(emocion)
        faltan = max(0, PISTAS_POR_EMOCION - len(existentes))
        if faltan == 0:
            logger.info(f"{emocion}: ya hay {len(existentes)} pista(s), no se descarga nada.")
            continue

        logger.info(f"{emocion}: buscando {faltan} pista(s) nueva(s) (tags: {tags})...")
        descargar_lote(buscar_para(client_id, emocion, faltan, ya_descargadas),
                       ya_descargadas, atribucion)

    guardar_json(RUTA_HISTORIAL, sorted(ya_descargadas))
    guardar_json(RUTA_ATRIBUCION, atribucion)
    logger.info("Listo.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
