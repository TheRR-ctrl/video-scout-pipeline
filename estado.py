"""
Estado — muestra de un vistazo en qué punto está el pipeline: qué hay en
disco, cuántas historias van en cada etapa y qué credenciales faltan.

No modifica nada, solo lee. Sirve tanto para saber qué sigue como para
diagnosticar cuando algo no corre ("no se encontró guion.txt", etc.).

Uso:
  python estado.py
"""
import os
import sys
import re
import json
import glob
from datetime import datetime, timezone

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CARPETA_ESTADO = os.path.join(BASE_DIR, "pipeline_state")
RUTA_GUION = os.path.join(BASE_DIR, "guion.txt")

V, R, A, G, C, N = "\033[92m", "\033[91m", "\033[93m", "\033[90m", "\033[96m", "\033[0m"


def leer_json(ruta, default):
    try:
        with open(ruta, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def titulo(t):
    print(f"\n{C}{t}{N}\n{G}{'─' * 52}{N}")


def linea(etiqueta, valor, estado=""):
    color = {"ok": V, "mal": R, "aviso": A}.get(estado, "")
    print(f"  {etiqueta:<34}{color}{valor}{N}")


def contar_historias_guion():
    if not os.path.exists(RUTA_GUION):
        return None, 0, 0
    with open(RUTA_GUION, "r", encoding="utf-8") as f:
        contenido = f.read()
    bloques = [b for b in contenido.split("===NUEVA_HISTORIA===") if b.strip()]
    palabras = sum(
        len([l for l in b.splitlines() if l.strip() and not l.strip().startswith("#")])
        for b in bloques
    )
    con_cierre = sum(1 for b in bloques if len(b.strip().split("\n\n")) > 1)
    return len(bloques), len(contenido.split()), con_cierre


def diagnostico_rutas(carpeta_salida):
    """Compara lo que dice resultado_lote.json contra lo que hay en disco.
    Sirve cuando los números no cuadran (hay archivos, pero ninguna ruta
    registrada resuelve)."""
    import unicodedata
    lote = leer_json(os.path.join(carpeta_salida, "resultado_lote.json"), {})
    completados = lote.get("completados", [])
    reales = sorted(os.path.basename(m) for m in glob.glob(os.path.join(carpeta_salida, "*.mp4")))

    print(f"\n{C}Rutas registradas (primeras 5 de {len(completados)}){N}")
    print(f"{G}{'─' * 52}{N}")
    for v in completados[:5]:
        ruta = v.get("ruta", "")
        marca = f"{V}existe{N}" if os.path.exists(ruta) else f"{R}NO resuelve{N}"
        print(f"  {marca}  {ruta}")

    print(f"\n{C}Archivos .mp4 realmente en disco ({len(reales)}){N}")
    print(f"{G}{'─' * 52}{N}")
    for n in reales[:10]:
        print(f"  {n}")
    if len(reales) > 10:
        print(f"  ... y {len(reales) - 10} más")

    # ¿Coinciden los nombres, aunque las rutas completas no?
    regs = {unicodedata.normalize("NFC", os.path.basename(v.get("ruta", ""))) for v in completados}
    disc = {unicodedata.normalize("NFC", n) for n in reales}
    comunes = regs & disc
    print(f"\n{C}Cruce por nombre de archivo{N}")
    print(f"{G}{'─' * 52}{N}")
    print(f"  coinciden           {len(comunes)}")
    print(f"  solo en el registro {len(regs - disc)}")
    print(f"  solo en el disco    {len(disc - regs)}")
    if disc - regs:
        print(f"\n  {A}En disco pero sin registrar:{N}")
        for n in sorted(disc - regs)[:6]:
            print(f"    {n}")
    print()


def main():
    if "--rutas" in sys.argv:
        try:
            import generar_video_maestro as gvm
            cfg = gvm.cargar_config(os.path.join(BASE_DIR, "config.json"))
            diagnostico_rutas(cfg["carpeta_salida"])
        except Exception as exc:
            print(f"{R}No se pudo diagnosticar: {exc}{N}")
        return

    print(f"\n{C}Estado del pipeline{N}  ·  {datetime.now().strftime('%d/%m/%Y %H:%M')}")

    # ---------- config ----------
    try:
        import generar_video_maestro as gvm
        cfg = gvm.cargar_config(os.path.join(BASE_DIR, "config.json"))
        carpeta_salida = cfg["carpeta_salida"]
        subs = cfg["subtitulos"]
    except Exception as exc:
        print(f"{R}No se pudo cargar la configuración: {exc}{N}")
        return

    titulo("Historias")

    n_bloques, n_palabras, _ = contar_historias_guion()
    if n_bloques is None:
        linea("guion.txt", "no existe", "mal")
        print(f"\n  {A}Sin guion.txt no hay nada que renderizar.{N}")
        print(f"  {G}Se crea corriendo: python script_writer.py{N}")
        print(f"  {G}(y antes python trend_scout.py, que busca las historias){N}")
    else:
        linea("guion.txt", f"{n_bloques} historia(s), ~{n_palabras} palabras", "ok")

    candidatos = leer_json(os.path.join(CARPETA_ESTADO, "candidatos.json"), None)
    if candidatos is None:
        linea("candidatos.json", "no existe", "aviso")
    else:
        linea("candidatos.json", f"{len(candidatos)} sin guion todavía",
              "ok" if candidatos else "aviso")

    vistos = leer_json(os.path.join(CARPETA_ESTADO, "historial_vistos.json"), [])
    linea("historial de posts vistos", f"{len(vistos)} post(s)")

    # ---------- videos ----------
    titulo("Videos")

    if not os.path.isdir(carpeta_salida):
        linea("carpeta de salida", f"no existe: {carpeta_salida}", "mal")
        completados = []
    else:
        mp4s = glob.glob(os.path.join(carpeta_salida, "*.mp4"))
        linea("carpeta de salida", carpeta_salida)
        linea("archivos .mp4 en disco", f"{len(mp4s)}", "ok" if mp4s else "aviso")
        lote = leer_json(os.path.join(carpeta_salida, "resultado_lote.json"), {})
        completados = lote.get("completados", [])
        # resultado_lote.json puede citar videos cuyo archivo ya no está (los
        # borró la retención de 7 días, o se limpiaron a mano). Distinguirlo
        # importa: si no, "pendientes de publicar" cuenta videos inexistentes
        # y publisher.py los rechaza uno por uno al no encontrarlos.
        #
        # La ruta guardada puede no resolver aunque el archivo SÍ esté: en la
        # SD card los nombres con acentos pueden quedar normalizados distinto
        # (NFC vs NFD), y entonces os.path.exists() falla sobre un archivo que
        # se ve perfectamente en el listado. Por eso, si la ruta no resuelve,
        # se reintenta comparando el nombre ya normalizado.
        import unicodedata

        def normaliza(s):
            return unicodedata.normalize("NFC", s)

        reales = {normaliza(os.path.basename(m)): m for m in mp4s}

        vivos, recuperados = [], 0
        for v in completados:
            ruta = v.get("ruta", "")
            if os.path.exists(ruta):
                vivos.append(v)
                continue
            real = reales.get(normaliza(os.path.basename(ruta)))
            if real:
                v = dict(v, ruta=real)
                vivos.append(v)
                recuperados += 1

        muertos = len(completados) - len(vivos)
        linea("renderizados (resultado_lote)", f"{len(completados)}")
        if recuperados:
            linea("  ...hallados por nombre", f"{recuperados}", "aviso")
        if muertos:
            linea("  ...cuyo archivo ya no existe", f"{muertos}", "aviso")
        completados = vivos

    publicados = leer_json(os.path.join(CARPETA_ESTADO, "publicados.json"), [])
    rechazados = leer_json(os.path.join(CARPETA_ESTADO, "rechazados.json"), [])
    procesados = {p.get("ruta") for p in publicados} | {r.get("ruta") for r in rechazados}
    pendientes = [v for v in completados if v.get("ruta") not in procesados]

    linea("pendientes de publicar", f"{len(pendientes)}", "ok" if pendientes else "")
    linea("publicados en YouTube", f"{len(publicados)}")
    if rechazados:
        linea("rechazados", f"{len(rechazados)}", "aviso")

    # Retención local: cuáles están por borrarse.
    ahora = datetime.now(timezone.utc)
    por_borrar = []
    for p in publicados:
        if p.get("_borrado_local") or not p.get("subido_en"):
            continue
        try:
            subido = datetime.strptime(p["subido_en"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        dias = 7 - (ahora - subido).days
        if dias <= 2:
            por_borrar.append((dias, p.get("titulo_youtube", "?")))
    if por_borrar:
        print()
        for dias, t in sorted(por_borrar):
            cuando = "hoy" if dias <= 0 else f"en {dias} día(s)"
            linea(f"  se borra {cuando}", t[:38], "aviso")

    # ---------- estilo ----------
    titulo("Estilo de subtítulos")
    linea("estilo", subs["estilo"])
    fuente_ok = False
    if os.path.isdir(gvm.RUTA_FUENTES):
        ttfs = glob.glob(os.path.join(gvm.RUTA_FUENTES, "*.ttf"))
        fuente_ok = bool(ttfs)
        linea("fuentes/", f"{len(ttfs)} archivo(s)", "ok" if fuente_ok else "mal")
    else:
        linea("fuentes/", "no existe — libass elegirá otra fuente", "mal")
    linea("fuente", subs["fuente"], "ok" if fuente_ok else "aviso")
    linea("itálica", "sí" if subs.get("italica") else "no")
    linea("velo blanco del fondo", f"{cfg.get('velo_blanco_fondo', 0.0)}")

    # Cómo quedaría la subida ahora mismo, con las tres formas de permitir
    # datos combinadas — es lo que decide si publisher.py sube o se aplaza.
    try:
        import publisher
        cfg_pub = publisher.cargar_config()
        solo_wifi = cfg_pub.get("solo_wifi", True)
        por_entorno = os.environ.get("SUBIR_CON_DATOS") == "1"
        if not solo_wifi:
            linea("subir con datos móviles", "permitido (config)", "aviso")
        elif por_entorno:
            linea("subir con datos móviles", "permitido (SUBIR_CON_DATOS=1)", "aviso")
        else:
            linea("subir con datos móviles", "bloqueado — solo con WiFi", "ok")
    except Exception:
        pass

    # ---------- credenciales ----------
    titulo("Credenciales")

    # Las claves pueden venir del entorno o de secretos.env. Distinguirlo
    # importa: una que solo está en el entorno desaparece al abrir otra
    # sesión de Termux o al correr desde cron, y el pipeline sigue adelante
    # con la metadata de respaldo sin decir por qué.
    try:
        import secretos
        for clave, tiene, origen in secretos.estado():
            opcional = clave == "JAMENDO_CLIENT_ID"
            if tiene:
                linea(clave, f"ok (desde {origen})", "ok")
            elif opcional:
                linea(clave, "sin configurar (opcional)")
            else:
                linea(clave, "FALTA", "mal")
        if not os.path.exists(secretos.RUTA_SECRETOS):
            print(f"  {G}  tip: guárdalas en secretos.env y dejan de perderse entre sesiones{N}")
    except Exception:
        linea("GEMINI_API_KEY",
              "configurada" if os.environ.get("GEMINI_API_KEY") else "FALTA",
              "ok" if os.environ.get("GEMINI_API_KEY") else "mal")
    for archivo, etiqueta in (("client_secret.json", "client_secret.json"),
                              ("youtube_token.json", "youtube_token.json")):
        existe = os.path.exists(os.path.join(BASE_DIR, archivo))
        linea(etiqueta, "presente" if existe else "FALTA", "ok" if existe else "mal")

    # ---------- assets ----------
    titulo("Material de fondo")
    vert = [f for p in ("fondo_vertical*", "fondo_gameplay*")
            for f in glob.glob(os.path.join(BASE_DIR, p))]
    horiz = glob.glob(os.path.join(BASE_DIR, "fondo_horizontal*"))
    # Cualquiera de los dos sirve para ambos formatos: el renderizador escala
    # y recorta al centro. Solo es un problema no tener ninguno.
    hay_algo = bool(vert or horiz)
    linea("marcados para shorts", f"{len(vert)} archivo(s)",
          "ok" if vert else ("aviso" if hay_algo else "mal"))
    linea("marcados para largos", f"{len(horiz)} archivo(s)",
          "ok" if horiz else ("aviso" if hay_algo else "mal"))
    if hay_algo and not (vert and horiz):
        falta = "shorts" if not vert else "videos largos"
        sobra = "horizontales" if not vert else "verticales"
        print(f"  {G}  sin material propio para {falta}: se recortan los {sobra}{N}")
    elif not hay_algo:
        print(f"  {A}  sin videos de fondo no se puede renderizar nada{N}")
        print(f"  {G}  ponlos con: python vincular_fondos.py{N}")
    musica = glob.glob(os.path.join(BASE_DIR, "musica_*.mp3"))
    linea("pistas de música", f"{len(musica)}", "ok" if musica else "aviso")

    # La plantilla es un .png y .gitignore ignora *.png, así que no sobrevive
    # a un reclonado del repo. Sin ella el video sí sale, pero con la tarjeta
    # de respaldo en vez de la tuya — conviene que se note.
    plantilla = next(
        (p for p in ("tarjeta_plantilla.png", "tarjeta_plantilla.jpg", "Tarjeta de inicio.png")
         if os.path.exists(os.path.join(BASE_DIR, p))),
        None,
    )
    linea("plantilla de la tarjeta", plantilla or "falta — se usa la de respaldo",
          "ok" if plantilla else "aviso")

    base = (cfg.get("sonido_transicion") or "").strip()
    if base:
        efectos = [
            f for f in glob.glob(os.path.join(BASE_DIR, base + "*"))
            if f.lower().endswith((".mp3", ".wav", ".m4a", ".ogg", ".aac"))
        ]
        linea("efecto de transición", f"{len(efectos)} archivo(s)" if efectos else "falta — no suena nada",
              "ok" if efectos else "aviso")
        if not efectos:
            print(f"  {G}  ponle un {base}.mp3 en esta carpeta para que suene tras la intro{N}")
    else:
        linea("efecto de transición", "desactivado")

    # ---------- qué sigue ----------
    titulo("Qué sigue")
    if n_bloques is None:
        print(f"  {V}python trend_scout.py{N}    busca historias nuevas en Reddit")
        print(f"  {V}python script_writer.py{N}  las convierte en guion.txt")
    elif not completados or len(pendientes) == 0 and n_bloques > len(completados):
        print(f"  {V}python generar_video_maestro.py{N}  renderiza las {n_bloques} historia(s)")
    elif pendientes:
        print(f"  {V}python publisher.py{N}  sube los {len(pendientes)} video(s) pendiente(s)")
    else:
        print(f"  {G}Todo al día. Corre 'python pipeline.py' para una ronda completa.{N}")
    print()


if __name__ == "__main__":
    main()
