"""
Sube a TikTok los videos que el publisher ya aprobó y subió a YouTube.

Por qué va después de publisher.py y no en paralelo: los chequeos de calidad
(ffprobe, y el filtro de contenido de Gemini) ya se hicieron allí, y su
resultado quedó en pipeline_state/metadata.json. Repetirlos aquí gastaría
cuota de Gemini para llegar al mismo veredicto, y peor: podría dar uno
distinto sobre el mismo video, que es la clase de incoherencia imposible de
depurar después. Aquí solo se sube lo que ya pasó por ahí.

Dos modos, y la diferencia no es un detalle:

  borrador (por defecto)  El video aterriza en la bandeja de "subidos" de tu
                          cuenta y tú le das a publicar desde la app. Solo
                          necesita el permiso video.upload, que TikTok
                          concede sin más trámite.

  directo                 Publica solo, sin tocar nada. Necesita el permiso
                          video.publish Y que TikTok haya auditado la app.
                          Sin esa auditoría, TikTok obliga a que todo lo
                          publicado quede en modo privado, así que "directo"
                          sin auditar no es publicar: es subir en privado.

Uso:
  python tiktok_publisher.py                # sube a borradores
  python tiktok_publisher.py --directo      # publica (requiere auditoría)
  python tiktok_publisher.py --simular      # enseña qué haría, sin subir nada
  python tiktok_publisher.py --estado       # qué hay subido y qué falta

Credenciales: TIKTOK_CLIENT_KEY y TIKTOK_CLIENT_SECRET en secretos.env, y
tiktok_token.json (lo genera  python generar_tiktok_token.py ).
"""
import os
import json
import time
import logging
import argparse

import requests

import secretos  # carga secretos.env si las claves no están en el entorno
import publisher  # se reutilizan sus rutas de estado y su lectura del lote

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("tiktok")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RUTA_TOKEN = os.path.join(BASE_DIR, "tiktok_token.json")
RUTA_SUBIDOS = os.path.join(publisher.CARPETA_ESTADO, "tiktok_subidos.json")

API = "https://open.tiktokapis.com/v2"
URL_INBOX_INIT = f"{API}/post/publish/inbox/video/init/"
URL_DIRECTO_INIT = f"{API}/post/publish/video/init/"
URL_ESTADO = f"{API}/post/publish/status/fetch/"
URL_TOKEN = f"{API}/oauth/token/"

# Reglas de troceo de TikTok: cada trozo entre 5 MB y 64 MB, salvo el último,
# que puede pasarse para arrastrar los bytes sobrantes. Un video de menos de
# 5 MB va entero, en un solo trozo.
MB = 1024 * 1024
CHUNK_MIN = 5 * MB
CHUNK_MAX = 64 * MB

# El pie de cada video. TikTok admite bastante más, pero lo que se lee sin
# desplegar son las primeras líneas, y los hashtags cuentan para el límite.
LIMITE_PIE = 2200


def cargar_config():
    """La sección tiktok de config.json, con los valores por defecto."""
    cfg = publisher.cargar_config()
    tiktok = {
        "activo": False,       # apagado hasta que haya credenciales
        "modo": "borrador",    # "borrador" o "directo"
        "max_por_corrida": 5,  # subir de golpe 40 videos es pedir un bloqueo
        # Solo se usa en modo directo. Por defecto se sube en privado, igual
        # que en YouTube: da una ventana para mirar el video en la app antes
        # de que lo vea nadie. Con la app auditada puede ponerse
        # PUBLIC_TO_EVERYONE; sin auditar, TikTok lo fuerza a privado de todas
        # formas y ponerlo aquí solo haría que la llamada fallara.
        "privacidad": "SELF_ONLY",
    }
    tiktok.update(cfg.get("tiktok", {}))
    return tiktok


# ---------------------------------------------------------
# Token
# ---------------------------------------------------------
def cargar_token():
    if not os.path.exists(RUTA_TOKEN):
        raise SystemExit(
            f"No encontré {RUTA_TOKEN}.\n"
            "Genéralo una sola vez con:  python generar_tiktok_token.py"
        )
    with open(RUTA_TOKEN, "r", encoding="utf-8") as f:
        return json.load(f)


def guardar_token(token):
    with open(RUTA_TOKEN, "w", encoding="utf-8") as f:
        json.dump(token, f, indent=2)
    try:
        os.chmod(RUTA_TOKEN, 0o600)  # guarda credenciales
    except OSError:
        pass


def token_valido(token=None):
    """Devuelve un access_token vivo, refrescándolo si le queda poco.

    El de TikTok dura 24 horas, asi que en un pipeline que corre a diario
    caduca practicamente siempre entre corrida y corrida: refrescar no es un
    caso raro, es el camino normal.
    """
    token = token or cargar_token()
    margen = 300  # cinco minutos, para no apurar el vencimiento a mitad de subida
    if token.get("expira_en", 0) - margen > time.time():
        return token["access_token"], token

    clave = os.environ.get("TIKTOK_CLIENT_KEY", "").strip()
    secreto = os.environ.get("TIKTOK_CLIENT_SECRET", "").strip()
    if not clave or not secreto:
        raise SystemExit(
            "Faltan TIKTOK_CLIENT_KEY o TIKTOK_CLIENT_SECRET en secretos.env.\n"
            "Sácalas en https://developers.tiktok.com/ → tu app → Credentials."
        )

    logger.info("El token caducó; lo refresco.")
    r = requests.post(
        URL_TOKEN,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "client_key": clave,
            "client_secret": secreto,
            "grant_type": "refresh_token",
            "refresh_token": token["refresh_token"],
        },
        timeout=30,
    )
    datos = r.json()
    if "access_token" not in datos:
        raise SystemExit(
            f"No pude refrescar el token: {datos}\n"
            "Si dice que el refresh_token no vale, vuelve a autorizar con:\n"
            "  python generar_tiktok_token.py"
        )

    nuevo = {
        "access_token": datos["access_token"],
        # TikTok puede devolver un refresh_token distinto al refrescar, y el
        # anterior deja de servir: guardar el nuevo no es opcional.
        "refresh_token": datos.get("refresh_token", token["refresh_token"]),
        "expira_en": time.time() + int(datos.get("expires_in", 86400)),
        "scope": datos.get("scope", token.get("scope", "")),
    }
    guardar_token(nuevo)
    return nuevo["access_token"], nuevo


# ---------------------------------------------------------
# Pie de publicación
# ---------------------------------------------------------
def construir_pie(metadata):
    """Título + hashtags, recortado al límite.

    Se reutiliza lo que ya escribió Gemini para YouTube en vez de pedir un
    texto nuevo: son el mismo video y la misma historia, y así el pie no
    depende de que haya cuota de API en el momento de subir a TikTok.
    """
    titulo = (metadata.get("titulo_youtube") or "").strip()
    etiquetas = ["".join(c for c in h if c.isalnum()) for h in metadata.get("hashtags", [])]
    etiquetas = [e for e in etiquetas if e]

    pie = titulo
    for etiqueta in etiquetas:
        candidato = f"{pie} #{etiqueta}"
        # Se añaden de uno en uno y se para al llegar al límite, en vez de
        # juntarlo todo y cortar al final: cortar dejaría un hashtag partido
        # por la mitad, que TikTok interpreta como una etiqueta inventada.
        if len(candidato) > LIMITE_PIE:
            break
        pie = candidato
    return pie[:LIMITE_PIE]


# ---------------------------------------------------------
# Subida
# ---------------------------------------------------------
def plan_de_troceo(tamano):
    """(chunk_size, total_chunk_count) segun las reglas de TikTok."""
    if tamano < CHUNK_MIN:
        return tamano, 1
    if tamano <= CHUNK_MAX:
        return tamano, 1
    # El ultimo trozo carga con el resto, por eso se redondea hacia abajo.
    chunk = CHUNK_MAX
    total = tamano // chunk
    return chunk, int(total)


def iniciar_subida(access_token, ruta, modo, pie, privacidad="SELF_ONLY"):
    tamano = os.path.getsize(ruta)
    chunk, total = plan_de_troceo(tamano)

    cuerpo = {
        "source_info": {
            "source": "FILE_UPLOAD",
            "video_size": tamano,
            "chunk_size": chunk,
            "total_chunk_count": total,
        }
    }
    if modo == "directo":
        cuerpo["post_info"] = {
            "title": pie,
            "privacy_level": privacidad,
            "disable_comment": False,
            "disable_duet": False,
            "disable_stitch": False,
        }

    url = URL_DIRECTO_INIT if modo == "directo" else URL_INBOX_INIT
    r = requests.post(
        url,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json; charset=UTF-8",
        },
        json=cuerpo,
        timeout=60,
    )
    datos = r.json()
    if r.status_code != 200 or not datos.get("data", {}).get("upload_url"):
        raise RuntimeError(f"TikTok rechazó el inicio de subida: {datos.get('error') or datos}")
    return datos["data"]["publish_id"], datos["data"]["upload_url"], chunk, total, tamano


def subir_bytes(upload_url, ruta, chunk, total, tamano):
    """Manda el archivo por trozos, en orden, como exige TikTok."""
    with open(ruta, "rb") as f:
        for i in range(total):
            inicio = i * chunk
            # El ultimo trozo se lleva todo lo que quede, que puede ser mas
            # que chunk_size.
            fin = tamano - 1 if i == total - 1 else inicio + chunk - 1
            f.seek(inicio)
            datos = f.read(fin - inicio + 1)
            r = requests.put(
                upload_url,
                headers={
                    "Content-Type": "video/mp4",
                    "Content-Length": str(len(datos)),
                    "Content-Range": f"bytes {inicio}-{fin}/{tamano}",
                },
                data=datos,
                timeout=600,
            )
            if r.status_code not in (200, 201, 206):
                raise RuntimeError(f"Falló el trozo {i + 1}/{total}: {r.status_code} {r.text[:200]}")
            if total > 1:
                logger.info(f"    trozo {i + 1}/{total} subido")


def esperar_proceso(access_token, publish_id, intentos=10, espera=6):
    """Pregunta por el estado hasta que TikTok termine de procesar.

    No es cosmético: si el proceso falla (formato, duración, derechos de la
    música), el fallo aparece aquí y no en la subida, que ya devolvió 200.
    Sin esta consulta se apuntaría como subido algo que TikTok descartó.
    """
    for _ in range(intentos):
        r = requests.post(
            URL_ESTADO,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json; charset=UTF-8",
            },
            json={"publish_id": publish_id},
            timeout=30,
        )
        datos = r.json().get("data", {})
        estado = datos.get("status")
        if estado in ("PUBLISH_COMPLETE", "SEND_TO_USER_INBOX"):
            return True, estado
        if estado == "FAILED":
            return False, datos.get("fail_reason", "sin motivo")
        time.sleep(espera)
    return True, "procesando todavía"


# ---------------------------------------------------------
# Principal
# ---------------------------------------------------------
def videos_pendientes():
    """Los que YouTube ya aprobó y subió, y TikTok todavía no."""
    cfg = publisher.cargar_config()
    ruta_lote = os.path.join(cfg["carpeta_salida"], "resultado_lote.json")
    if not os.path.exists(ruta_lote):
        logger.error(f"No encontré {ruta_lote}. Corre generar_video_maestro.py primero.")
        return [], {}

    with open(ruta_lote, "r", encoding="utf-8") as f:
        completados = json.load(f).get("completados", [])

    metadata = publisher.cargar_json(publisher.RUTA_METADATA, {})
    ya_en_tiktok = {v["ruta"] for v in publisher.cargar_json(RUTA_SUBIDOS, [])}
    rechazados = {r["ruta"] for r in publisher.cargar_json(publisher.RUTA_RECHAZADOS, [])}

    pendientes = []
    for v in completados:
        if v["ruta"] in ya_en_tiktok or v["ruta"] in rechazados:
            continue
        if not os.path.exists(v["ruta"]):
            # El video se subió a YouTube y luego se borró del teléfono para
            # hacer sitio. No es un error: simplemente ya no hay qué subir.
            continue
        m = metadata.get(os.path.basename(v["ruta"]))
        if not m or not m.get("aprobado"):
            # Sin el veredicto del publisher no se sube: o no ha pasado por
            # ahí todavía, o lo rechazó.
            continue
        pendientes.append((v, m))
    return pendientes, metadata


def _numeros(entrada, tope):
    """Convierte "1 3 5-8" en [1, 3, 5, 6, 7, 8], sin repetidos ni fuera de rango."""
    elegidos = set()
    for pieza in entrada.replace(",", " ").split():
        if "-" in pieza[1:]:
            desde, _, hasta = pieza.partition("-")
        else:
            desde = hasta = pieza
        try:
            a, b = int(desde), int(hasta)
        except ValueError:
            raise ValueError(f"No entiendo '{pieza}'.")
        if a > b:
            a, b = b, a
        for n in range(a, b + 1):
            if not 1 <= n <= tope:
                raise ValueError(f"El {n} no está en la lista (hay {tope}).")
            elegidos.add(n)
    return sorted(elegidos)


def marcar_subidos():
    """Apunta como ya subidos videos que están en TikTok pero no en el registro.

    Hace falta porque el registro solo conoce lo que subió este script: los
    videos que uno publicó a mano antes de montar esto siguen contando como
    pendientes, y se subirían por segunda vez.
    """
    pendientes, _ = videos_pendientes()
    if not pendientes:
        print(" No hay pendientes que marcar.")
        return 0

    print(f"\n {len(pendientes)} video(s) pendiente(s):\n")
    for i, (v, m) in enumerate(pendientes, 1):
        titulo = m.get("titulo_youtube") or os.path.basename(v["ruta"])
        print(f"  {i:3}. {titulo[:70]}")

    print("\n Escribe los números de los que YA están en TikTok.")
    print(" Vale '1 4 7', o un rango '1-5', o las dos cosas. Enter para salir.")
    entrada = input(" > ").strip()
    if not entrada:
        print(" No he tocado nada.")
        return 0

    try:
        elegidos = _numeros(entrada, len(pendientes))
    except ValueError as exc:
        print(f" ✗ {exc} No he tocado nada.")
        return 1

    print("\n Voy a marcar como ya subidos:")
    for n in elegidos:
        v, m = pendientes[n - 1]
        print(f"   • {(m.get('titulo_youtube') or os.path.basename(v['ruta']))[:60]}")
    if input("\n ¿Correcto? [s/N] ").strip().lower() not in ("s", "si", "sí"):
        print(" Cancelado, no he tocado nada.")
        return 0

    subidos = publisher.cargar_json(RUTA_SUBIDOS, [])
    for n in elegidos:
        v, m = pendientes[n - 1]
        subidos.append({
            "ruta": v["ruta"],
            "publish_id": "",
            "modo": "manual",
            "pie": m.get("titulo_youtube", ""),
            # Se distingue de una subida real: si algún día hay que revisar
            # esto, conviene saber cuáles pasaron por la API y cuáles no.
            "estado": "marcado a mano (ya estaba en TikTok)",
            "fecha": time.strftime("%Y-%m-%dT%H:%M:%S"),
        })
    publisher.guardar_json(RUTA_SUBIDOS, subidos)
    print(f"\n ✓ {len(elegidos)} marcado(s). Quedan {len(pendientes) - len(elegidos)} pendientes.")
    return 0


def revisar_subidos():
    """Vuelve a preguntar a TikTok por el estado de lo ya subido.

    El script no espera indefinidamente a que termine el proceso: cuando se
    cansa apunta "procesando todavía" y sigue. Si TikTok descartó el video
    despues de eso (formato, duracion, musica con derechos), el registro se
    quedo con la version optimista y esta orden lo corrige.
    """
    subidos = publisher.cargar_json(RUTA_SUBIDOS, [])
    consultables = [s for s in subidos if s.get("publish_id")]
    if not consultables:
        print(" No hay subidas por API que consultar.")
        return 0

    access_token, _ = token_valido()
    cambios = 0
    for s in consultables:
        r = requests.post(
            URL_ESTADO,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json; charset=UTF-8",
            },
            json={"publish_id": s["publish_id"]},
            timeout=30,
        )
        datos = r.json().get("data", {})
        estado = datos.get("status") or r.json().get("error", {}).get("message", "sin respuesta")
        if estado == "FAILED":
            estado = f"FAILED: {datos.get('fail_reason', 'sin motivo')}"
        nombre = os.path.basename(s["ruta"])[:50]
        print(f"  {estado:32} {nombre}")
        if estado != s.get("estado"):
            s["estado"] = estado
            cambios += 1

    if cambios:
        publisher.guardar_json(RUTA_SUBIDOS, subidos)
        print(f"\n {cambios} estado(s) actualizado(s) en el registro.")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Sube a TikTok los videos ya aprobados.")
    ap.add_argument("--directo", action="store_true",
                    help="Publica en vez de dejar en borradores (requiere auditoría de TikTok).")
    ap.add_argument("--simular", action="store_true",
                    help="Enseña qué subiría, sin llamar a TikTok.")
    ap.add_argument("--con-datos", action="store_true",
                    help="Sube aunque no haya WiFi (ojo: los videos pesan cientos de MB).")
    ap.add_argument("--revisar", action="store_true",
                    help="Pregunta a TikTok en qué acabó cada video ya subido.")
    ap.add_argument("--marcar-subidos", action="store_true",
                    help="Marca como ya subidos videos que pusiste en TikTok a mano.")
    ap.add_argument("--estado", action="store_true",
                    help="Cuántos videos hay subidos y cuántos pendientes.")
    args = ap.parse_args(argv if argv is not None else [])

    if args.marcar_subidos:
        return marcar_subidos()

    if args.revisar:
        return revisar_subidos()

    cfg = cargar_config()
    modo = "directo" if args.directo or cfg["modo"] == "directo" else "borrador"

    pendientes, _ = videos_pendientes()
    subidos = publisher.cargar_json(RUTA_SUBIDOS, [])

    if args.estado:
        print(f" Subidos a TikTok : {len(subidos)}")
        print(f" Pendientes       : {len(pendientes)}")
        for v, m in pendientes[:10]:
            print(f"   • {m.get('titulo_youtube', os.path.basename(v['ruta']))[:60]}")
        return 0

    if not cfg["activo"] and not args.simular:
        logger.info(
            'TikTok está apagado. Para encenderlo, pon "tiktok": {"activo": true} '
            "en config.json (y ten antes tiktok_token.json)."
        )
        return 0

    if not pendientes:
        logger.info("No hay videos pendientes de subir a TikTok.")
        return 0

    tanda = pendientes[: cfg["max_por_corrida"]]
    logger.info(f"{len(pendientes)} pendiente(s); subo {len(tanda)} en modo {modo}.")

    if args.simular:
        for v, m in tanda:
            tamano = os.path.getsize(v["ruta"])
            chunk, total = plan_de_troceo(tamano)
            print(f"\n  {os.path.basename(v['ruta'])}")
            print(f"    {tamano / MB:.1f} MB en {total} trozo(s) de {chunk / MB:.1f} MB")
            print(f"    pie: {construir_pie(m)[:120]}")
        return 0

    # Mismas tres puertas que en publisher.py, y a proposito: un video de aqui
    # pesa lo mismo que el que va a YouTube, y el cron corre a diario sin que
    # nadie mire si el telefono esta en wifi o en la calle.
    permitir_datos = (
        args.con_datos
        or os.environ.get("SUBIR_CON_DATOS") == "1"
        or not publisher.cargar_config().get("solo_wifi", True)
    )
    if not permitir_datos and not publisher.conectado_a_wifi():
        logger.info(
            "Sin WiFi activo — aplazo la subida a TikTok para no gastar datos.\n"
            "   Para subir ahora de todas formas: python tiktok_publisher.py --con-datos"
        )
        return 0
    if permitir_datos and not publisher.conectado_a_wifi():
        logger.warning("Sin WiFi, pero se pidió subir con datos móviles. Ojo con tu plan.")

    access_token, _ = token_valido()

    for v, m in tanda:
        ruta = v["ruta"]
        pie = construir_pie(m)
        logger.info(f"Subiendo: {os.path.basename(ruta)}")
        try:
            publish_id, upload_url, chunk, total, tamano = iniciar_subida(
                access_token, ruta, modo, pie, cfg["privacidad"]
            )
            subir_bytes(upload_url, ruta, chunk, total, tamano)
            ok, detalle = esperar_proceso(access_token, publish_id)
            if not ok:
                logger.error(f"  TikTok descartó el video: {detalle}")
                continue

            subidos.append({
                "ruta": ruta,
                "publish_id": publish_id,
                "modo": modo,
                "pie": pie,
                "estado": detalle,
                "fecha": time.strftime("%Y-%m-%dT%H:%M:%S"),
            })
            # Se guarda tras cada video, no al final: si la subida siguiente
            # se corta, lo ya subido no se repite en la próxima corrida.
            publisher.guardar_json(RUTA_SUBIDOS, subidos)
            destino = "publicado" if modo == "directo" else "en tus borradores de TikTok"
            logger.info(f"  ✅ {destino} ({detalle})")
        except Exception as exc:
            # Un fallo no tumba la tanda: el resto puede subir bien, y este
            # video vuelve a intentarse en la próxima corrida.
            logger.error(f"  ❌ {exc}")

    return 0


if __name__ == "__main__":
    import sys
    # Desde la terminal manda lo que se escribió; desde pipeline.py se llama
    # main() sin argumentos y se usan los valores por defecto.
    raise SystemExit(main(sys.argv[1:]) or 0)
