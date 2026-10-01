"""
Por qué YouTube quitó (o limitó) un video, y rehacerlo corregido.

Lo que YouTube cuenta por la API es poco: si un video está «rechazado» dice
el motivo en una palabra (copyright, termsOfUse, duplicate…), y si lo quitó
del todo no dice nada. El motivo completo solo llega por correo y en Studio.
Así que esto junta tres cosas y no se las inventa:

  1. Lo que dijo YouTube (relanzar.py --problemas), y si lo pegas, el texto
     del correo o de Studio (--motivo).
  2. Con qué se hizo el video: el guion, el título y la descripción con que
     se subió, la música (y su licencia) y los tramos de fondo que llevaba
     (y si alguno se bajó de un canal ajeno con bajar_fondo.py).
  3. Gemini, que con todo eso propone la causa más probable, qué corregir y
     qué riesgo hay en volver a subirlo.

Y luego, si lo pides, lo rehace con esas correcciones: reescribe el guion
(Gemini), deja fuera los tramos de fondo o el fondo entero, aparta la
canción, lo graba y lo sube. Solo ese video.

Volver a subir algo que YouTube quitó por sus normas puede costar otra
advertencia (tres en 90 días cierran el canal). Por eso, con riesgo alto,
--rehacer se niega salvo con --aun-asi, y el panel lo pregunta antes.

Uso:
  python diagnosticar_youtube.py                    # diagnostica los que falten
  python diagnosticar_youtube.py --video ID         # ese video (otra vez)
  python diagnosticar_youtube.py --video ID --motivo "texto del correo"
  python diagnosticar_youtube.py --rehacer ID       # aplica y deja en la cola
  python diagnosticar_youtube.py --rehacer ID --grabar --subir
"""
import os
import re
import sys
import json
import shutil
import logging
import argparse
import subprocess
from datetime import datetime, timezone

import almacen
import secretos  # carga secretos.env si las claves no están en el entorno
import publisher
import relanzar
import limpiar_cola
import fondos_excluidos

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CARPETA_ESTADO = os.path.join(BASE_DIR, "pipeline_state")
RUTA_DIAGNOSTICOS = os.path.join(CARPETA_ESTADO, "diagnosticos_youtube.json")
RUTA_ATRIBUCION = os.path.join(CARPETA_ESTADO, "musica_atribucion.json")
RUTA_ORIGEN_FONDOS = os.path.join(CARPETA_ESTADO, "fondos_origen.json")
RUTA_NOMBRES_FONDOS = os.path.join(CARPETA_ESTADO, "fondos_nombres.json")
# Una canción con reclamación no vuelve a sonar: aquí no la toca la rotación
# (musica_usadas/ sí se puede devolver a mano) ni se vuelve a bajar, porque
# su id ya está en musica_historial.json.
CARPETA_MUSICA_FUERA = os.path.join(BASE_DIR, "musica_reclamada")
MODEL = "gemini-3.6-flash"

# Lo que se puede diagnosticar. "fallido" no: es una subida rota, y
# publisher.py ya la vuelve a subir sola.
TIPOS = ("quitado", "rechazado", "restringido", "bloqueado")

CAUSAS = {
    "derechos_musica": "la música tiene dueño (reclamación de derechos)",
    "derechos_fondo": "un clip del fondo tiene dueño (reclamación de derechos)",
    "contenido_reutilizado": "YouTube lo ve como contenido reutilizado o repetido",
    "contenido_sensible": "el tema o alguna parte de la historia incumple sus normas",
    "metadatos_enganosos": "el título o la descripción engañan o son spam",
    "desconocida": "no hay pistas suficientes para saberlo",
}

SCHEMA = {
    "type": "object",
    "properties": {
        "causa": {"type": "string", "enum": list(CAUSAS)},
        "confianza": {"type": "string", "enum": ["alta", "media", "baja"]},
        "explicacion": {"type": "string", "description": "2-4 frases en español llano: qué pasó y en qué te basas."},
        "partes_problema": {"type": "array", "items": {"type": "string"},
                            "description": "Frases o datos concretos del guion, título o descripción que lo provocan (citados). Vacío si no aplica."},
        "correcciones": {"type": "array", "items": {"type": "string"},
                         "description": "Cambios concretos para la versión nueva, en imperativo y en español."},
        "reescribir_guion": {"type": "boolean"},
        "cambiar_musica": {"type": "boolean"},
        "cambiar_fondo": {"type": "boolean"},
        "riesgo_resubir": {"type": "string", "enum": ["bajo", "medio", "alto"]},
        "por_que_riesgo": {"type": "string"},
    },
    "required": ["causa", "confianza", "explicacion", "partes_problema", "correcciones",
                 "reescribir_guion", "cambiar_musica", "cambiar_fondo",
                 "riesgo_resubir", "por_que_riesgo"],
}

SYSTEM = """Eres un revisor de políticas de YouTube para un canal de Shorts en español de
historias de Reddit narradas por voz sintética sobre un video de fondo (gameplay o
clips) con música. YouTube quitó, rechazó o limitó uno de sus videos. Recibes lo que
dijo YouTube y con qué se hizo el video.

Tu trabajo es decir la causa MÁS PROBABLE, sin inventar: si la evidencia no apunta a
nada, la causa es "desconocida" con confianza baja. Pistas que pesan:
- rejectionReason copyright/claim o bloqueo por países → casi siempre la música o un
  clip del fondo con dueño. Un fondo bajado de un canal ajeno es el primer sospechoso;
  la música de Jamendo con licencia CC también recibe reclamaciones de Content ID.
- duplicate → contenido repetido (la misma historia subida dos veces).
- termsOfUse/inappropriate, restricción de edad o quitado sin motivo → mira el guion:
  sexualidad con menores (aunque sea de pasada), autolesión, violencia gráfica,
  odio, acoso a una persona identificable, actividades peligrosas, datos personales.
- título que promete algo que la historia no cuenta → metadatos engañosos.

Las correcciones tienen que ser aplicables: qué quitar o cambiar del guion (sin
perder la historia), qué música o fondo cambiar. El riesgo de volver a subirlo es
alto si la causa es un tema que no se arregla reescribiendo (abuso de menores,
autolesión explícita) o si YouTube habló de normas de la comunidad y no se sabe qué
parte fue; medio si se sabe y se puede quitar; bajo si era solo música o fondo."""

SCHEMA_REESCRITURA = {
    "type": "object",
    "properties": {
        "titulo_hook": {"type": "string", "description": "Primera frase que se narra; sin clickbait engañoso."},
        "cuerpo": {"type": "string", "description": "La historia completa reescrita, en español, lista para narrar."},
    },
    "required": ["titulo_hook", "cuerpo"],
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("diagnosticar_youtube")


# ---------------------------------------------------------
# Evidencia
# ---------------------------------------------------------
def diagnosticos():
    datos = almacen.leer(RUTA_DIAGNOSTICOS, {}) or {}
    return datos if isinstance(datos, dict) else {}


def _guardar_diagnostico(video_id, entrada):
    datos = diagnosticos()
    datos[video_id] = entrada
    almacen.guardar(RUTA_DIAGNOSTICOS, datos)


def _registro(video_id):
    for p in publisher.cargar_json(publisher.RUTA_PUBLICADOS, []):
        if p.get("video_id") == video_id:
            return p
    return None


def _problema(video_id):
    datos = almacen.leer(relanzar.RUTA_PROBLEMAS, {}) or {}
    for x in (datos.get("videos") or []) if isinstance(datos, dict) else []:
        if x.get("video_id") == video_id:
            return x
    return None


def _del_lote(ruta):
    """El registro del render (cuerpo, música, fondos) de ese archivo."""
    carpeta = publisher.cargar_config()["carpeta_salida"]
    lote = almacen.leer(os.path.join(carpeta, "resultado_lote.json"), {}) or {}
    nombre = os.path.basename(ruta or "")
    for v in lote.get("completados", []) if isinstance(lote, dict) else []:
        if os.path.basename(v.get("ruta", "")) == nombre:
            return v
    return {}


def bloque_original(registro):
    """El bloque de guion.txt (con su cabecera) de ese video: en la cola, en
    el historial de lo ya grabado o en las copias .bak."""
    apodo = relanzar.apodo_de_registro(registro)
    if not apodo:
        return None
    for ruta in (limpiar_cola.RUTA_GUION, limpiar_cola.RUTA_HISTORIAL_GUION):
        for b in limpiar_cola.bloques_del_guion(ruta):
            if limpiar_cola.apodo(b) == apodo:
                return b
    bloque, _ = relanzar.buscar_en_respaldos(apodo)
    return bloque


def _titulo_y_cuerpo(bloque):
    lineas = [l for l in (bloque or "").splitlines() if l.strip() and not l.lstrip().startswith("#")]
    return (lineas[0].strip() if lineas else ""), "\n".join(lineas[1:]).strip()


def evidencia(video_id, motivo_usuario=""):
    """Todo lo que se sabe del video, sin preguntar a nadie."""
    p = _registro(video_id) or {"video_id": video_id}
    problema = _problema(video_id) or {}
    lote = _del_lote(p.get("ruta"))
    bloque = bloque_original(p)
    titulo_guion, cuerpo = _titulo_y_cuerpo(bloque)
    if not cuerpo:
        titulo_guion, cuerpo = lote.get("titulo", ""), lote.get("cuerpo", "")
    meta = (publisher.cargar_json(publisher.RUTA_METADATA, {}) or {}).get(
        os.path.basename(p.get("ruta", "")), {}) or {}

    musica = p.get("musica_archivo") or lote.get("musica_archivo")
    atrib = (almacen.leer(RUTA_ATRIBUCION, {}) or {}).get(musica or "", {}) if musica else {}
    origenes = almacen.leer(RUTA_ORIGEN_FONDOS, {}) or {}
    fondos = []
    for c in p.get("fondos") or lote.get("fondos") or []:
        org = origenes.get(c.get("original") or "") or origenes.get(c.get("archivo") or "")
        fondos.append({**c, "bajado_de": org})

    return {
        "video_id": video_id,
        "youtube": {"tipo": problema.get("tipo"), "detalle": problema.get("detalle"),
                    "estado_youtube": p.get("estado_youtube")},
        "motivo_usuario": (motivo_usuario or "").strip()[:2000],
        "titulo_youtube": p.get("titulo_youtube") or meta.get("titulo_youtube"),
        "descripcion_youtube": meta.get("descripcion_youtube"),
        "hashtags": meta.get("hashtags"),
        "titulo_guion": titulo_guion,
        "guion": cuerpo[:12000],
        "hay_guion": bool(cuerpo),
        "fuente_url": p.get("fuente_url") or lote.get("fuente_url"),
        "musica": {"archivo": musica, "artista": atrib.get("artista"), "titulo": atrib.get("titulo"),
                   "licencia": atrib.get("licencia_url")} if musica else None,
        "fondos": fondos,
        "rehecho_antes": bool(p.get("rehace")),
    }


# ---------------------------------------------------------
# Diagnóstico
# ---------------------------------------------------------
def por_reglas(ev):
    """Lo que se puede decir sin Gemini (sin clave o sin cuota)."""
    yt = ev["youtube"]
    texto = f"{yt.get('detalle') or ''} {yt.get('estado_youtube') or ''} {ev['motivo_usuario']}".lower()
    ajenos = [f for f in ev["fondos"] if f.get("bajado_de")]
    if re.search(r"derechos|copyright|claim|reclamaci|pa[ií]s", texto):
        if ajenos:
            causa, conf = "derechos_fondo", "media"
        elif ev["musica"]:
            causa, conf = "derechos_musica", "media"
        else:
            causa, conf = "desconocida", "baja"
    elif "duplic" in texto:
        causa, conf = "contenido_reutilizado", "media"
    elif re.search(r"normas|inapropiad|\+18|mayores|terms", texto):
        causa, conf = "contenido_sensible", "baja"
    else:
        causa, conf = "desconocida", "baja"
    return {
        "causa": causa, "confianza": conf,
        "explicacion": "Diagnóstico sin Gemini, solo por lo que dijo YouTube. "
                       + CAUSAS[causa][0].upper() + CAUSAS[causa][1:] + ".",
        "partes_problema": [], "correcciones": [],
        "reescribir_guion": causa == "contenido_sensible",
        "cambiar_musica": causa == "derechos_musica",
        "cambiar_fondo": causa == "derechos_fondo",
        "riesgo_resubir": "alto" if causa in ("contenido_sensible", "desconocida") else "medio",
        "por_que_riesgo": (
            "Sin saber qué parte fue, volver a subirlo puede costar otra advertencia. Pega el "
            "motivo del correo de YouTube y analízalo otra vez." if causa in ("contenido_sensible", "desconocida")
            else "Cambiando la música o el fondo debería pasar, pero sin Gemini no se ha revisado el guion."),
        "con_gemini": False,
    }


def _cliente():
    import gemini as genai
    import script_writer
    return script_writer.ClienteConRespaldo(genai.Client()), genai


def con_gemini(ev):
    client, genai = _cliente()
    datos = {k: v for k, v in ev.items() if k not in ("hay_guion",)}
    resp = client.models.generate_content(
        model=MODEL,
        contents="Video afectado:\n" + json.dumps(datos, ensure_ascii=False, indent=1),
        config=genai.types.GenerateContentConfig(
            system_instruction=SYSTEM, response_mime_type="application/json",
            response_schema=SCHEMA),
    )
    out = json.loads(resp.text)
    out["con_gemini"] = True
    return out


def diagnosticar(video_id, motivo_usuario=""):
    ev = evidencia(video_id, motivo_usuario)
    try:
        d = con_gemini(ev)
    except Exception as exc:          # sin clave, sin cuota, sin red: reglas
        logger.warning(f"Gemini no pudo opinar ({str(exc)[:160]}); diagnóstico por reglas.")
        d = por_reglas(ev)
    # Sin guion no hay nada que reescribir, diga lo que diga.
    if not ev["hay_guion"]:
        d["reescribir_guion"] = False
    entrada = {**d, "cuando": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "titulo": ev["titulo_youtube"], "motivo_usuario": ev["motivo_usuario"],
               "hay_guion": ev["hay_guion"], "musica": (ev["musica"] or {}).get("archivo"),
               "fondos_ajenos": sorted({f.get("original") or f.get("archivo")
                                        for f in ev["fondos"] if f.get("bajado_de")}),
               "rehecho": (diagnosticos().get(video_id) or {}).get("rehecho")}
    _guardar_diagnostico(video_id, entrada)
    return entrada


def _pintar(video_id, d):
    # La primera línea la reconoce errores.py si hiciera falta; el resto es
    # lo que se lee en la tarjeta del trabajo.
    print(f"\n🩺 {d.get('titulo') or video_id}")
    print(f"   Causa probable: {CAUSAS.get(d['causa'], d['causa'])} (confianza {d['confianza']})")
    print(f"   {d['explicacion']}")
    for x in d.get("partes_problema") or []:
        print(f"   · «{x[:140]}»")
    for x in d.get("correcciones") or []:
        print(f"   → {x}")
    print(f"   Riesgo de volver a subirlo: {d['riesgo_resubir']} — {d['por_que_riesgo']}")


# ---------------------------------------------------------
# Rehacer
# ---------------------------------------------------------
def _enlace_actual(original, apuntado):
    """El nombre de enlace que tiene HOY ese fondo (se renumeran)."""
    nombres = almacen.leer(RUTA_NOMBRES_FONDOS, {}) or {}
    for enlace, orig in nombres.items():
        if orig == original:
            return enlace
    return apuntado


def corregir_fondo(ev):
    """Fuera los tramos que llevaba el video; un fondo bajado de un canal
    ajeno, entero. Devuelve qué se hizo, en palabras."""
    hechos, enteros = [], set()
    for c in ev["fondos"]:
        archivo = _enlace_actual(c.get("original"), c.get("archivo"))
        if not archivo:
            continue
        if c.get("bajado_de"):
            if archivo not in enteros:
                fondos_excluidos.poner_entero(archivo, True)
                enteros.add(archivo)
                hechos.append(f"fondo {c.get('original') or archivo} fuera entero "
                              f"(bajado de {c['bajado_de'].get('url', 'un enlace')})")
        elif archivo not in enteros:
            ini = float(c.get("desde") or 0)
            fondos_excluidos.excluir_tramo(archivo, ini, ini + float(c.get("segundos") or 0))
            hechos.append(f"tramo {ini:.0f}-{ini + float(c.get('segundos') or 0):.0f}s de "
                          f"{c.get('original') or archivo} fuera")
    if not ev["fondos"]:
        hechos.append("este video es de antes de que se apuntaran los fondos: no se sabe qué "
                      "tramo fue; quítalo a mano en Ajustes → «✂ Tramos que no se usan»")
    return hechos


def corregir_musica(ev):
    m = (ev["musica"] or {}).get("archivo")
    if not m:
        return []
    ruta = os.path.join(BASE_DIR, m)
    if not os.path.exists(ruta):
        return [f"la canción {m} ya no estaba en la carpeta"]
    os.makedirs(CARPETA_MUSICA_FUERA, exist_ok=True)
    shutil.move(ruta, os.path.join(CARPETA_MUSICA_FUERA, m))
    return [f"canción {m} apartada a musica_reclamada/ (no vuelve a sonar)"]


def reescribir(ev, d):
    client, genai = _cliente()
    peticion = (
        "Reescribe esta historia para volver a subirla a YouTube sin el problema que hizo que "
        "la quitaran. Conserva la trama, el narrador y el tono; cambia solo lo necesario.\n\n"
        f"Problema: {d['explicacion']}\n"
        f"Partes que lo provocan: {json.dumps(d.get('partes_problema') or [], ensure_ascii=False)}\n"
        f"Correcciones: {json.dumps(d.get('correcciones') or [], ensure_ascii=False)}\n\n"
        f"Título/hook actual: {ev['titulo_guion']}\n\nHistoria:\n{ev['guion']}"
    )
    resp = client.models.generate_content(
        model=MODEL, contents=peticion,
        config=genai.types.GenerateContentConfig(
            response_mime_type="application/json", response_schema=SCHEMA_REESCRITURA))
    out = json.loads(resp.text)
    if len((out.get("cuerpo") or "").split()) < 40:
        raise RuntimeError("Gemini devolvió una historia demasiado corta")
    return out["titulo_hook"].strip(), out["cuerpo"].strip()


def _cabecera(bloque, video_id, cuerpo, emocion=None):
    """La cabecera del bloque original (voz, emoción, fuente, autor) más la
    marca de que rehace ese video. Sin original, la voz sale del texto (no
    se inventa: una narradora con voz de hombre se oye en el primer segundo)."""
    cab = [l for l in (bloque or "").splitlines()
           if l.lstrip().startswith("#") and not re.match(r"#\s*Rehace:", l.strip())]
    if not cab:
        import narrador
        genero = narrador.detectar_genero_narrador(cuerpo) or "masculino"
        cab = [f"# Genero: {'Femenino' if genero == 'femenino' else 'Masculino'}",
               f"# Emocion: {emocion or 'drama'}"]
    return "\n".join(cab + [f"# Rehace: {video_id}"])


def rehacer(video_id, aun_asi=False, grabar=False, subir=False):
    d = diagnosticos().get(video_id)
    if not d:
        raise SystemExit("❌ Ese video no tiene diagnóstico todavía: pulsa «Analizar» primero.")
    if d.get("rehecho"):
        raise SystemExit(f"❌ Ya se rehizo el {d['rehecho'].get('cuando', '')[:10]}: "
                         f"«{d['rehecho'].get('titulo', '')}».")
    if d["riesgo_resubir"] == "alto" and not aun_asi:
        raise SystemExit("❌ Riesgo alto de otra advertencia al volver a subirlo "
                         f"({d['por_que_riesgo']}). No se rehace sin confirmarlo.")
    if subir:
        import dispositivo
        dispositivo.salir_si_desactivado()

    ev = evidencia(video_id, d.get("motivo_usuario", ""))
    registro = _registro(video_id) or {"video_id": video_id}
    bloque = bloque_original(registro)
    hechos = []

    if d.get("cambiar_fondo"):
        hechos += corregir_fondo(ev)
    if d.get("cambiar_musica"):
        hechos += corregir_musica(ev)

    titulo, cuerpo = ev["titulo_guion"], ev["guion"]
    if not cuerpo:
        raise SystemExit("❌ No encuentro el guion de ese video (ni en la cola, ni en el "
                         "historial, ni en las copias): no se puede rehacer.")
    if d.get("reescribir_guion"):
        print("✍️  Reescribiendo el guion con las correcciones…", flush=True)
        titulo, cuerpo = reescribir(ev, d)
        hechos.append("guion reescrito con las correcciones")
    emocion = _del_lote(registro.get("ruta")).get("emocion")
    nuevo = f"{_cabecera(bloque, video_id, cuerpo, emocion)}\n{titulo}\n{cuerpo}"

    # Lo viejo fuera antes de poner lo nuevo: el .mp4 y su rastro en el lote
    # (si no, limpiar_cola sacaría la historia nueva por «ya grabada») y el
    # registro de publicados.json (YouTube ya no lo tiene).
    if registro.get("ruta"):
        relanzar.olvidar_el_render(registro)
    publicados = publisher.cargar_json(publisher.RUTA_PUBLICADOS, [])
    publisher.guardar_json(publisher.RUTA_PUBLICADOS,
                           [p for p in publicados if p.get("video_id") != video_id])
    relanzar.apuntar_en_el_historial([registro], True)
    relanzar.ignorar_problema(video_id)
    relanzar.devolver_a_la_cola([nuevo])
    numero = len(limpiar_cola.bloques_del_guion())

    d["rehecho"] = {"cuando": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "titulo": titulo, "numero": numero, "hechos": hechos}
    _guardar_diagnostico(video_id, d)
    print(f"✅ «{titulo}» en la cola como historia {numero}.")
    for h in hechos:
        print(f"   · {h}")

    if not grabar:
        return 0
    print(f"🎬 Grabando la historia {numero}…", flush=True)
    r = subprocess.run([sys.executable, "generar_video_maestro.py", "--historias", str(numero)],
                       cwd=BASE_DIR)
    if r.returncode != 0:
        raise SystemExit("❌ El render falló; la historia sigue en la cola para grabarla a mano.")
    archivo = next((os.path.basename(v.get("ruta", "")) for v in reversed(
        (almacen.leer(os.path.join(publisher.cargar_config()["carpeta_salida"],
                                   "resultado_lote.json"), {}) or {}).get("completados", []))
        if v.get("rehace") == video_id), None)
    if not subir:
        return 0
    if not archivo:
        raise SystemExit("❌ No encuentro el video nuevo en el registro del render; súbelo "
                         "desde Revisar con «Publicar pendientes».")
    print(f"⬆️  Subiendo {archivo}…", flush=True)
    r = subprocess.run([sys.executable, "publisher.py", "--solo", archivo], cwd=BASE_DIR)
    return r.returncode


def main(argv=None):
    ap = argparse.ArgumentParser(description="Por qué YouTube quitó un video, y rehacerlo corregido.")
    ap.add_argument("--video", help="Diagnosticar ese video (aunque ya tenga diagnóstico).")
    ap.add_argument("--motivo", default="", help="El motivo que dio YouTube por correo o en Studio.")
    ap.add_argument("--rehacer", metavar="ID", help="Aplicar las correcciones y volver a la cola.")
    ap.add_argument("--grabar", action="store_true", help="Con --rehacer: grabarlo al momento.")
    ap.add_argument("--subir", action="store_true", help="Con --grabar: subirlo al terminar.")
    ap.add_argument("--aun-asi", action="store_true", help="Rehacer aunque el riesgo sea alto.")
    args = ap.parse_args(argv)

    if args.rehacer:
        return rehacer(args.rehacer, args.aun_asi, args.grabar, args.grabar and args.subir)
    if args.video:
        d = diagnosticar(args.video, args.motivo)
        _pintar(args.video, d)
        return 0

    ya = diagnosticos()
    pendientes = [x for x in relanzar.problemas_guardados()[0]
                  if x.get("tipo") in TIPOS and x.get("video_id") not in ya]
    if not pendientes:
        print("\n  Nada que diagnosticar: no hay videos quitados sin analizar.\n"
              "  (Si no has mirado el canal hace tiempo: Canal → «🔎 Mirar ahora».)\n")
        return 0
    for x in pendientes:
        _pintar(x["video_id"], diagnosticar(x["video_id"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
