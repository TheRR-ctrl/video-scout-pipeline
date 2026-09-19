"""
Borra de YouTube los videos que no arrancaron y devuelve la historia a la cola.

De los 48 shorts del canal, 18 se quedaron por debajo de 300 vistas y la
mitad de esos no pasó de 12. No están entre 60 y 300: o el feed reparte el
video o no lo reparte, y cuando no lo reparte el video no se recupera solo
nunca. Dos motivos conocidos lo provocan: contar dos veces la misma historia
(la segunda copia queda como refrito) y los temas que el feed no empuja.

Un video así no sirve de nada en el canal, pero la historia sí: el guion
sigue en guion.txt y se puede volver a grabar con otro título, otra
miniatura y otro momento del día. Esto es lo que hace:

  python relanzar.py                    # qué vistas tiene cada video subido
  python relanzar.py --duplicados       # lista las copias repetidas sin vistas
  python relanzar.py --sin-vistas       # lista los no vistos, para rehacerlos
  ... --si                              # y con esto se hace de verdad

«--duplicados» borra la copia repetida y no la vuelve a grabar: la historia
ya está contada en la copia que se queda (la que más vistas tiene).
«--sin-vistas» borra el video Y deja la historia en la cola, así que el
siguiente `python generar_video_maestro.py` la graba otra vez de cero.

Las vistas se leen del canal (videos.list), no se adivinan. Borrar necesita
un permiso que el token actual no tiene; si falta, esto lo dice antes de
tocar nada y explica cómo añadirlo.

Dos guardas, para que esto se pueda dejar en el cron sin vigilarlo:

  --dias-minimos 14   no toca lo subido hace menos de dos semanas. Un video
                      de ayer con 0 vistas no fracasó, es que no le ha tocado.
  --max-intentos 2    una historia se rehace dos veces como mucho. Si a la
                      tercera sigue a cero, el problema es la historia.

La revisión quincenal del cron (instalar_cron.sh, días 1 y 15) es esto mismo
con los valores por omisión, así que lo que borra automáticamente es lo que
verías corriéndolo a mano.
"""
import os
import sys
import json
import time
import glob
import logging
import argparse
from datetime import datetime, timezone

import cola
import almacen
import publisher
import limpiar_cola

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RUTA_TOKEN = os.path.join(BASE_DIR, "youtube_token.json")
RUTA_HISTORIAL = os.path.join(publisher.CARPETA_ESTADO, "relanzados.json")

# Las vistas leídas la última vez. El panel las pinta desde aquí en vez de
# preguntarle a YouTube en cada refresco: pintar la pestaña no puede depender
# de que haya red, y la API tiene cuota diaria.
RUTA_VISTAS = os.path.join(publisher.CARPETA_ESTADO, "vistas.json")

# El permiso que hace falta para videos.delete. youtube.upload deja subir
# pero no borrar, y youtube.readonly solo deja mirar.
PERMISO_BORRADO = "https://www.googleapis.com/auth/youtube.force-ssl"

# Cuántas vistas cuentan como «no lo vio nadie». 0 es literal: ni una. El
# canal no tiene videos entre 60 y 300 vistas, así que subirlo a 50 no
# cambiaría a quién señala; queda como palanca por si el reparto cambia.
MAX_VISTAS_DEFECTO = 0

# Días que se le dan a un video antes de darlo por muerto. Un video subido
# ayer con 0 vistas no fracasó: todavía no le ha tocado. Sin este margen,
# la revisión automática borraría lo que acaba de publicarse. 14 son los de
# la revisión quincenal, y de sobra para los 9h de buffer de publisher.
DIAS_MINIMOS_DEFECTO = 14

# Cuántas veces se rehace la misma historia antes de dejarla ir. Si a la
# tercera sigue sin arrancar, el problema es la historia y no el reparto:
# volver a grabarla es gastar TTS y render para repetir el resultado.
MAX_INTENTOS_DEFECTO = 2

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("relanzar")


# ---------------------------------------------------------
# Permisos
# ---------------------------------------------------------
def permisos_del_token():
    """Los scopes que tiene youtube_token.json, tal como los guardó Google."""
    try:
        with open(RUTA_TOKEN, "r", encoding="utf-8") as f:
            datos = json.load(f)
    except Exception:
        return None
    return set(datos.get("scopes") or [])


AVISO_PERMISO = """
  El token de YouTube no tiene permiso para borrar.

  Se generó solo con «subir» y «leer», que es todo lo que hacía falta hasta
  ahora. Borrar un video es otra cosa y Google lo pide aparte. Para añadirlo:

    1. En publisher.py y en generar_youtube_token.py, añade a la lista SCOPES:
         "https://www.googleapis.com/auth/youtube.force-ssl",
       (en la versión que acabas de bajar ya están puestos)

    2. Borra el token viejo y genera uno nuevo:
         rm youtube_token.json
         python generar_youtube_token.py

  Sale el link de autorización, lo abres en el navegador del teléfono y das
  permiso. Es el mismo paso que hiciste la primera vez.

  Mientras no lo hagas, esto solo lista: no borra nada. El mismo permiso le
  falta a rehacer_todo.py, que también borra del canal.
"""


def comprobar_permiso_de_borrado():
    """True si se puede borrar. Si no, lo explica y devuelve False."""
    permisos = permisos_del_token()
    if permisos is None:
        print("\n  No encontré youtube_token.json. Genera el token primero:")
        print("    python generar_youtube_token.py\n")
        return False
    if PERMISO_BORRADO not in permisos:
        print(AVISO_PERMISO)
        return False
    return True


# ---------------------------------------------------------
# Vistas
# ---------------------------------------------------------
def vistas_de(servicio, ids):
    """{video_id: vistas}. Los que YouTube ya no conoce quedan fuera.

    Se pregunta de 50 en 50 porque es el tope de la API; son 66 videos, así
    que esto son dos llamadas, no sesenta y seis.
    """
    salida = {}
    for i in range(0, len(ids), 50):
        trozo = ids[i:i + 50]
        try:
            resp = servicio.videos().list(part="statistics", id=",".join(trozo)).execute()
        except Exception as exc:
            logger.warning(f"No se pudieron leer las vistas de {len(trozo)} video(s): {exc}")
            continue
        for item in resp.get("items", []):
            # Un video con las estadísticas ocultas no trae viewCount. No es
            # lo mismo que cero vistas, así que se deja fuera para que no
            # entre por el filtro de «no vistos».
            crudo = item.get("statistics", {}).get("viewCount")
            if crudo is not None:
                salida[item["id"]] = int(crudo)
    return salida


def subidos_con_vistas(servicio=None):
    """Los registros de publicados.json con sus vistas reales.

    Cada uno lleva "vistas" (int) o None si YouTube no lo reconoce — eso
    último pasa cuando el video se borró a mano desde la app y el registro
    local se quedó atrás.
    """
    publicados = publisher.cargar_json(publisher.RUTA_PUBLICADOS, [])
    con_id = [p for p in publicados if p.get("video_id")]
    if not con_id:
        return []

    servicio = servicio or publisher.obtener_servicio_youtube()
    vistas = vistas_de(servicio, [p["video_id"] for p in con_id])
    for p in con_id:
        p["vistas"] = vistas.get(p["video_id"])
    guardar_vistas(vistas)
    return con_id


def guardar_vistas(vistas):
    """Deja las vistas leídas para que el panel las pueda pintar sin red."""
    publisher.guardar_json(RUTA_VISTAS, {
        "cuando": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "vistas": vistas,
    })


def vistas_guardadas():
    """(vistas, cuándo se leyeron). Sin caché todavía: ({}, None)."""
    datos = almacen.leer(RUTA_VISTAS, {}) or {}
    return datos.get("vistas") or {}, datos.get("cuando")


def subidos_con_vistas_guardadas():
    """Como subidos_con_vistas pero desde la caché, sin tocar la red.

    Un video subido después del último refresco no está en la caché: queda
    con vistas None, igual que uno que YouTube ya no reconoce. Para el panel
    es lo correcto —los dos se pintan con «?» y ninguno entra en los
    filtros—, pero por eso esto no decide borrados: eso siempre relee.
    """
    vistas, _ = vistas_guardadas()
    registros = [p for p in publisher.cargar_json(publisher.RUTA_PUBLICADOS, [])
                 if p.get("video_id")]
    for p in registros:
        p["vistas"] = vistas.get(p["video_id"])
    return registros


# ---------------------------------------------------------
# Duplicados
# ---------------------------------------------------------
def grupos_repetidos(registros):
    """Agrupa los que cuentan la misma historia, por parecido de título.

    Se compara el título y no el cuerpo porque de un video subido lo único
    que guardamos es el título. Dos títulos distintos para la misma historia
    se escapan de aquí; para eso está el filtro de script_writer, que sí ve
    el texto antes de escribirlo.
    """
    sin_agrupar = list(registros)
    grupos = []
    while sin_agrupar:
        cabeza = sin_agrupar.pop(0)
        h_cabeza = cola.huella(cabeza.get("titulo_youtube", ""))
        grupo, resto = [cabeza], []
        for otro in sin_agrupar:
            h = cola.huella(otro.get("titulo_youtube", ""))
            if cola.parecido(h_cabeza, h) >= cola.PARECIDO_MINIMO:
                grupo.append(otro)
            else:
                resto.append(otro)
        sin_agrupar = resto
        if len(grupo) > 1:
            grupos.append(grupo)
    return grupos


def _vistas(p):
    return p["vistas"] if p.get("vistas") is not None else -1


def dias_desde_subida(p):
    """Días que lleva subido, o None si el registro no dice cuándo."""
    crudo = p.get("subido_en")
    if not crudo:
        return None
    try:
        cuando = datetime.strptime(crudo, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except Exception:
        return None
    return (datetime.now(timezone.utc) - cuando).total_seconds() / 86400.0


def es_maduro(p, dias_minimos):
    """True si ya se le dio tiempo suficiente para arrancar.

    Un registro sin fecha se queda fuera: no se puede saber si es de hace un
    año o de esta mañana, y en la duda no se borra. Con --dias-minimos 0 se
    revisa todo, que es lo que quieres cuando lo corres a mano y mirando.
    """
    if dias_minimos <= 0:
        return True
    dias = dias_desde_subida(p)
    return dias is not None and dias >= dias_minimos


def intentos_previos():
    """{apodo: cuántas veces se rehizo ya} según pipeline_state/relanzados.json.

    Solo cuentan los que se borraron para rehacerlos (relanzado=True): los
    de --duplicados se borran sin volver a grabarse, y no son un intento.
    """
    cuenta = {}
    for reg in publisher.cargar_json(RUTA_HISTORIAL, []):
        if not reg.get("relanzado"):
            continue
        apodo = limpiar_cola._apodo_de_archivo(os.path.basename(reg.get("ruta") or ""))
        if apodo:
            cuenta[apodo] = cuenta.get(apodo, 0) + 1
    return cuenta


def sobrantes_de_los_repetidos(registros, max_vistas, dias_minimos=0):
    """De cada grupo repetido, cuáles se pueden borrar.

    Se queda la copia con más vistas (a igualdad, la que se subió antes: es
    la que lleva tiempo indexada). De las otras solo se borran las que no
    pasaron de max_vistas — si una copia repetida sí arrancó, no se toca,
    aunque sea la segunda — y que ya llevan dias_minimos subidas.

    La copia que se queda se elige mirando el grupo entero, también los
    recién subidos: si el que más vistas tiene es el de ayer, el viejo sin
    vistas es el refrito y es el que sobra.
    """
    fuera = []
    for grupo in grupos_repetidos(registros):
        orden = sorted(grupo, key=lambda p: (-_vistas(p), p.get("subido_en") or ""))
        for p in orden[1:]:
            if (p.get("vistas") is not None and p["vistas"] <= max_vistas
                    and es_maduro(p, dias_minimos)):
                p["_se_queda"] = orden[0]
                fuera.append(p)
    return fuera


def sin_vistas(registros, max_vistas, dias_minimos=0, max_intentos=0):
    """Los no vistos que tiene sentido rehacer.

    Se dejan fuera tres cosas:

    - Las copias repetidas: rehacer una historia que ya está contada en otro
      video (y funcionando) vuelve a subir el refrito que hundió a la copia.
      Esas van por --duplicados, que borra sin rehacer.
    - Los que llevan menos de dias_minimos subidos, que no han fracasado
      todavía: nadie los ha visto porque no les ha tocado.
    - Las historias que ya se rehicieron max_intentos veces y siguen a cero.
      Esas no se tocan: se quedan en el canal y en el registro, para que al
      revisar quede claro cuáles ya se intentaron.
    """
    repetidos = {id(p) for p in sobrantes_de_los_repetidos(registros, max_vistas, dias_minimos)}
    gastados = intentos_previos() if max_intentos > 0 else {}
    elegidos = []
    for p in registros:
        if p.get("vistas") is None or p["vistas"] > max_vistas:
            continue
        if id(p) in repetidos or not es_maduro(p, dias_minimos):
            continue
        if gastados.get(apodo_de_registro(p), 0) >= max_intentos > 0:
            continue
        elegidos.append(p)
    return elegidos


# ---------------------------------------------------------
# Volver a la cola
# ---------------------------------------------------------
def apodo_de_registro(p):
    """El trozo de nombre de archivo que identifica la historia.

    De "salida/07_Mi_mama_me_levanto.mp4" saca "Mi_mama_me_levanto", que es
    lo mismo que genera generar_video_maestro a partir del título del guion.
    Así se empareja un video subido con su bloque en guion.txt.
    """
    return limpiar_cola._apodo_de_archivo(os.path.basename(p.get("ruta", "")))


def _apodo_de_bloque(bloque):
    return limpiar_cola.apodo(bloque)


def esta_en_la_cola(apodo, bloques=None):
    bloques = bloques if bloques is not None else limpiar_cola.bloques_del_guion()
    return any(_apodo_de_bloque(b) == apodo for b in bloques)


def buscar_en_respaldos(apodo):
    """El bloque de la historia en las copias .bak que dejó limpiar_cola.

    Si la historia ya se sacó de la cola, el texto no está perdido: cada
    limpieza guarda guion.txt.bak-fecha antes de reescribirlo. Se busca de la
    copia más reciente hacia atrás.
    """
    respaldos = sorted(glob.glob(os.path.join(BASE_DIR, "guion.txt.bak-*")), reverse=True)
    for ruta in respaldos:
        for b in limpiar_cola.bloques_del_guion(ruta):
            if _apodo_de_bloque(b) == apodo:
                return b, os.path.basename(ruta)
    return None, None


def devolver_a_la_cola(bloques_nuevos):
    """Añade bloques al final de guion.txt.

    Al final y no al principio para no renumerar lo que ya estaba: si tenías
    una selección a medias (`--historias 3,4`), sigue apuntando a lo mismo.
    """
    if not bloques_nuevos:
        return
    with open(limpiar_cola.RUTA_GUION, "a", encoding="utf-8") as f:
        for b in bloques_nuevos:
            f.write("\n" + limpiar_cola.SEPARADOR + "\n" + b.strip() + "\n")


def olvidar_el_render(p):
    """Quita el .mp4 y su rastro en resultado_lote.json.

    Sin esto, limpiar_cola volvería a sacar la historia de la cola en cuanto
    lo corrieras (ve el registro de que ya se grabó) y el video viejo seguiría
    en la pestaña Revisar del panel como si estuviera pendiente.
    """
    cfg = publisher.cargar_config()
    carpeta = cfg["carpeta_salida"]
    nombre = os.path.basename(p.get("ruta", ""))
    if not nombre:
        return

    ruta_local = os.path.join(carpeta, nombre)
    if os.path.exists(ruta_local):
        try:
            os.remove(ruta_local)
        except Exception as exc:
            logger.warning(f"No se pudo borrar {nombre} del teléfono: {exc}")

    ruta_lote = os.path.join(carpeta, "resultado_lote.json")
    datos = publisher.cargar_json(ruta_lote, None)
    if not isinstance(datos, dict):
        return
    antes = datos.get("completados", [])
    datos["completados"] = [v for v in antes
                            if os.path.basename(v.get("ruta", "")) != nombre]
    if len(datos["completados"]) != len(antes):
        publisher.guardar_json(ruta_lote, datos)


# ---------------------------------------------------------
# Borrar
# ---------------------------------------------------------
def borrar_de_youtube(servicio, registros, relanzar):
    """Borra del canal, limpia publicados.json y (si toca) rehace la cola.

    publicados.json se reescribe entero al final y no video a video: son
    pocos, y así un fallo a mitad no deja el archivo inconsistente.
    """
    publicados = publisher.cargar_json(publisher.RUTA_PUBLICADOS, [])
    ids_pedidos = {p["video_id"] for p in registros}
    borrados, fallados = [], []

    for p in registros:
        vid = p["video_id"]
        try:
            servicio.videos().delete(id=vid).execute()
            borrados.append(p)
            logger.info(f"  🗑️  {p.get('titulo_youtube', vid)}")
        except Exception as exc:
            # Si ya no existe en el canal, el registro local sobraba: se
            # trata como borrado para que deje de aparecer.
            if "404" in str(exc) or "videoNotFound" in str(exc):
                borrados.append(p)
                logger.info(f"  (ya no estaba en el canal) {p.get('titulo_youtube', vid)}")
            else:
                fallados.append((p, str(exc)))
                logger.warning(f"  ✗ No se pudo borrar {vid}: {exc}")
        time.sleep(0.3)

    ids_borrados = {p["video_id"] for p in borrados}
    restantes = [p for p in publicados
                 if not (p.get("video_id") in ids_borrados and p.get("video_id") in ids_pedidos)]
    publisher.guardar_json(publisher.RUTA_PUBLICADOS, restantes)

    vueltas, perdidas = [], []
    if relanzar:
        bloques = limpiar_cola.bloques_del_guion()
        # Apodos que siguen teniendo un video vivo en el canal. Si la historia
        # que se va a rehacer es una de esas, rehacerla crearía el duplicado
        # otra vez: se borra el video muerto pero no se vuelve a grabar.
        vivos = {apodo_de_registro(p) for p in restantes} - {""}
        recuperados = []
        for p in borrados:
            apodo = apodo_de_registro(p)
            olvidar_el_render(p)
            if not apodo:
                perdidas.append((p, "el registro no dice qué archivo era"))
            elif apodo in vivos:
                perdidas.append((p, "esta historia ya está publicada en otro video, no se rehace"))
            elif esta_en_la_cola(apodo, bloques):
                vueltas.append((p, "ya estaba en la cola"))
            else:
                bloque, de_donde = buscar_en_respaldos(apodo)
                if bloque:
                    recuperados.append(bloque)
                    vueltas.append((p, f"recuperada de {de_donde}"))
                else:
                    perdidas.append((p, "el guion ya no está ni en guion.txt ni en las copias .bak"))
        devolver_a_la_cola(recuperados)

    apuntar_en_el_historial(borrados, relanzar)
    return borrados, fallados, vueltas, perdidas


def apuntar_en_el_historial(borrados, relanzado):
    """Deja constancia de qué se borró y cuándo.

    Una vez borrado de YouTube no hay forma de saber que existió: el registro
    local desaparece con él. Esto guarda el título y las vistas que tenía,
    para no volver a subir lo mismo sin darse cuenta.
    """
    if not borrados:
        return
    historial = publisher.cargar_json(RUTA_HISTORIAL, [])
    cuando = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for p in borrados:
        historial.append({
            "titulo_youtube": p.get("titulo_youtube"),
            "video_id": p.get("video_id"),
            "vistas_al_borrar": p.get("vistas"),
            "ruta": p.get("ruta"),
            "borrado_en": cuando,
            "relanzado": bool(relanzado),
        })
    publisher.guardar_json(RUTA_HISTORIAL, historial)


# ---------------------------------------------------------
# Listados
# ---------------------------------------------------------
def _linea(p):
    v = p.get("vistas")
    vistas = "  ?" if v is None else f"{v:>5}"
    return f"  {vistas} vistas  {(p.get('titulo_youtube') or '(sin título)')[:56]}"


def listar_todo(registros, max_vistas=MAX_VISTAS_DEFECTO,
                dias_minimos=DIAS_MINIMOS_DEFECTO,
                max_intentos=MAX_INTENTOS_DEFECTO):
    print(f"\n  {len(registros)} video(s) subidos al canal:\n")
    for p in sorted(registros, key=lambda x: -_vistas(x)):
        print(_linea(p))

    desconocidos = [p for p in registros if p.get("vistas") is None]
    if desconocidos:
        print(f"\n  {len(desconocidos)} con «?»: YouTube no los reconoce (borrados a mano)")
        print("  o tienen las estadísticas ocultas. No entran en los filtros.")

    # Las cuentas de abajo reparten los que están a cero sin solaparse: cada
    # uno cae en una sola, así que si alguna sorprende se sabe por qué.
    ceros = [p for p in registros
             if p.get("vistas") is not None and p["vistas"] <= max_vistas]
    verdes = [p for p in ceros if not es_maduro(p, dias_minimos)]
    sin_fecha = [p for p in verdes if dias_desde_subida(p) is None]
    nuevos = [p for p in verdes if p not in sin_fecha]
    repes = sobrantes_de_los_repetidos(registros, max_vistas, dias_minimos)
    rehacibles = sin_vistas(registros, max_vistas, dias_minimos, max_intentos)
    ya_intentadas = (len(ceros) - len(verdes) - len(repes) - len(rehacibles))

    filas = [
        ("copias repetidas de una historia ya contada", len(repes)),
        ("historias que se pueden volver a grabar", len(rehacibles)),
    ]
    if nuevos:
        filas.append((f"todavía nuevos, menos de {dias_minimos} días subidos", len(nuevos)))
    if sin_fecha:
        filas.append(("sin fecha de subida, no se sabe si les tocó", len(sin_fecha)))
    if ya_intentadas > 0:
        veces = "una vez" if max_intentos == 1 else f"{max_intentos} veces"
        filas.append((f"ya se rehicieron {veces}, se dejan estar", ya_intentadas))

    # El ancho sale de las etiquetas que de verdad se van a imprimir, para que
    # los números queden en columna sin contar espacios a mano.
    ancho = max(len(etiqueta) for etiqueta, _ in filas)
    print(f"\n  Sin ninguna vista: {len(ceros)}")
    for etiqueta, cuantos in filas:
        print(f"    {etiqueta:<{ancho}}  {cuantos}")
    print("\n  python relanzar.py --duplicados    para ver las repetidas")
    print("  python relanzar.py --sin-vistas    para ver las que se pueden rehacer\n")


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Borra de YouTube lo que no arrancó y devuelve la historia a la cola.")
    ap.add_argument("--duplicados", action="store_true",
                    help="Las copias repetidas sin vistas. Se borran y no se rehacen.")
    ap.add_argument("--sin-vistas", dest="sin_vistas", action="store_true",
                    help="Los que no vio nadie. Se borran y la historia vuelve a la cola.")
    ap.add_argument("--max-vistas", type=int, default=MAX_VISTAS_DEFECTO,
                    help=f"Hasta cuántas vistas cuenta como «no visto» (por omisión {MAX_VISTAS_DEFECTO}).")
    ap.add_argument("--dias-minimos", dest="dias_minimos", type=int, default=DIAS_MINIMOS_DEFECTO,
                    help=f"Días que se le dan a un video antes de darlo por muerto "
                         f"(por omisión {DIAS_MINIMOS_DEFECTO}; 0 revisa todo).")
    ap.add_argument("--max-intentos", dest="max_intentos", type=int, default=MAX_INTENTOS_DEFECTO,
                    help=f"Cuántas veces se rehace la misma historia antes de dejarla "
                         f"(por omisión {MAX_INTENTOS_DEFECTO}; 0 sin límite).")
    ap.add_argument("--si", action="store_true", help="Hacerlo de verdad.")
    ap.add_argument("--refrescar-vistas", dest="refrescar_vistas", action="store_true",
                    help="Solo releer las vistas del canal y guardarlas para el panel.")
    args = ap.parse_args(argv if argv is not None else sys.argv[1:])

    if args.duplicados and args.sin_vistas:
        raise SystemExit(
            "Uno de los dos: --duplicados borra la copia repetida sin rehacerla,\n"
            "--sin-vistas rehace la historia. Juntos no está claro qué querrías.")

    # El permiso se comprueba antes de preguntarle nada a YouTube: si no está,
    # más vale decirlo ya que después de listar y confirmar.
    if args.si and not comprobar_permiso_de_borrado():
        return 1

    registros = subidos_con_vistas()
    if not registros:
        print("\n  No hay videos subidos con video_id en publicados.json.\n")
        return 0

    if args.refrescar_vistas:
        # subidos_con_vistas ya guardó la caché al leerlas.
        vistos = [p for p in registros if p.get("vistas") is not None]
        _, cuando = vistas_guardadas()
        print(f"\n  Vistas releídas de {len(vistos)} video(s) ({cuando}).")
        print("  El panel ya las puede pintar sin conectarse.\n")
        return 0

    if not (args.duplicados or args.sin_vistas):
        listar_todo(registros, args.max_vistas, args.dias_minimos, args.max_intentos)
        return 0

    if args.duplicados:
        elegidos = sobrantes_de_los_repetidos(registros, args.max_vistas, args.dias_minimos)
        relanzar = False
        titular = "copia(s) repetida(s) sin vistas"
    else:
        elegidos = sin_vistas(registros, args.max_vistas, args.dias_minimos, args.max_intentos)
        relanzar = True
        titular = "video(s) que no vio nadie"

    if not elegidos:
        print(f"\n  No hay {titular}. No hay nada que borrar.")
        if args.dias_minimos > 0:
            print(f"  (no se miran los subidos hace menos de {args.dias_minimos} días; "
                  f"para verlos todos: --dias-minimos 0)")
        print()
        return 0

    print(f"\n  {len(elegidos)} {titular}:\n")
    for p in sorted(elegidos, key=lambda x: -_vistas(x)):
        print(_linea(p))
        if p.get("_se_queda"):
            q = p["_se_queda"]
            print(f"            se queda: {(q.get('titulo_youtube') or '')[:44]} "
                  f"({_vistas(q)} vistas)")

    if relanzar:
        print("\n  Se borran del canal y la historia vuelve a la cola: el siguiente")
        print("  render la graba otra vez, con otro título y otra miniatura.")
    else:
        print("\n  Se borran del canal y NO se rehacen: la historia ya está contada")
        print("  en la copia que se queda.")

    if not args.si:
        print(f"\n  Esto era el listado. Para hacerlo:  python relanzar.py "
              f"{'--duplicados' if args.duplicados else '--sin-vistas'} --si\n")
        return 0

    servicio = publisher.obtener_servicio_youtube()
    print()
    borrados, fallados, vueltas, perdidas = borrar_de_youtube(servicio, elegidos, relanzar)

    print(f"\n   ✓ {len(borrados)} borrado(s) del canal, publicados.json actualizado.")
    if fallados:
        print(f"   ✗ {len(fallados)} no se pudieron borrar; siguen en el canal y en el registro.")
    if relanzar:
        if vueltas:
            print(f"   ✓ {len(vueltas)} historia(s) en la cola otra vez:")
            for p, como in vueltas:
                print(f"       • {(p.get('titulo_youtube') or '')[:48]} ({como})")
            print("\n     python generar_video_maestro.py    para volver a grabarlas")
        if perdidas:
            print(f"   ⚠️  {len(perdidas)} no volvieron a la cola (el video se borró igual):")
            for p, por_que in perdidas:
                print(f"       • {(p.get('titulo_youtube') or '')[:48]} — {por_que}")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
