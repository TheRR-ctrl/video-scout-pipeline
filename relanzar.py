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
"""
import io
import os
import re
import sys
import json
import time
import glob
import logging
import argparse
import contextlib
from datetime import datetime, timezone

import cola
import publisher
import limpiar_cola

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RUTA_TOKEN = os.path.join(BASE_DIR, "youtube_token.json")
RUTA_HISTORIAL = os.path.join(publisher.CARPETA_ESTADO, "relanzados.json")

# El permiso que hace falta para videos.delete. youtube.upload deja subir
# pero no borrar, y youtube.readonly solo deja mirar.
PERMISO_BORRADO = "https://www.googleapis.com/auth/youtube.force-ssl"

# Cuántas vistas cuentan como «no lo vio nadie». 0 es literal: ni una. El
# canal no tiene videos entre 60 y 300 vistas, así que subirlo a 50 no
# cambiaría a quién señala; queda como palanca por si el reparto cambia.
MAX_VISTAS_DEFECTO = 0

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
    return con_id


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


def sobrantes_de_los_repetidos(registros, max_vistas):
    """De cada grupo repetido, cuáles se pueden borrar.

    Se queda la copia con más vistas (a igualdad, la que se subió antes: es
    la que lleva tiempo indexada). De las otras solo se borran las que no
    pasaron de max_vistas — si una copia repetida sí arrancó, no se toca,
    aunque sea la segunda.
    """
    fuera = []
    for grupo in grupos_repetidos(registros):
        orden = sorted(grupo, key=lambda p: (-_vistas(p), p.get("subido_en") or ""))
        for p in orden[1:]:
            if p.get("vistas") is not None and p["vistas"] <= max_vistas:
                p["_se_queda"] = orden[0]
                fuera.append(p)
    return fuera


def sin_vistas(registros, max_vistas):
    """Los no vistos que tiene sentido rehacer.

    Se dejan fuera las copias repetidas: rehacer una historia que ya está
    contada en otro video (y funcionando) vuelve a subir el refrito que
    hundió a la copia. Esas van por --duplicados, que borra sin rehacer.
    """
    repetidos = {id(p) for p in sobrantes_de_los_repetidos(registros, max_vistas)}
    return [p for p in registros
            if p.get("vistas") is not None and p["vistas"] <= max_vistas
            and id(p) not in repetidos]


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


def listar_todo(registros):
    print(f"\n  {len(registros)} video(s) subidos al canal:\n")
    for p in sorted(registros, key=lambda x: -_vistas(x)):
        print(_linea(p))

    desconocidos = [p for p in registros if p.get("vistas") is None]
    if desconocidos:
        print(f"\n  {len(desconocidos)} con «?»: YouTube no los reconoce (borrados a mano)")
        print("  o tienen las estadísticas ocultas. No entran en los filtros.")

    repes = sobrantes_de_los_repetidos(registros, MAX_VISTAS_DEFECTO)
    rehacibles = sin_vistas(registros, MAX_VISTAS_DEFECTO)
    print(f"\n  Sin ninguna vista: {len(repes) + len(rehacibles)}")
    print(f"    copias repetidas de una historia ya contada: {len(repes)}")
    print(f"    historias que se pueden volver a grabar:     {len(rehacibles)}")
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
    ap.add_argument("--si", action="store_true", help="Hacerlo de verdad.")
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

    if not (args.duplicados or args.sin_vistas):
        listar_todo(registros)
        return 0

    if args.duplicados:
        elegidos = sobrantes_de_los_repetidos(registros, args.max_vistas)
        relanzar = False
        titular = "copia(s) repetida(s) sin vistas"
    else:
        elegidos = sin_vistas(registros, args.max_vistas)
        relanzar = True
        titular = "video(s) que no vio nadie"

    if not elegidos:
        print(f"\n  No hay {titular}. No hay nada que borrar.\n")
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
