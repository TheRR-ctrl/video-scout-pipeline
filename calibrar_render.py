"""
Mide cuántos videos a la vez aguanta este aparato y deja ese número puesto.

    python calibrar_render.py            # mide y guarda en config.json
    python calibrar_render.py --ver      # mide y solo lo enseña

Codifica un clip de prueba de pocos segundos con el MISMO codificador que
usa el render (la tarjeta NVIDIA en un PC, el chip del teléfono si está
encendido, si no el procesador), primero uno solo, luego dos a la vez, tres…
y cuenta cuántos fotogramas por segundo salen entre todos. Se para cuando:

  - uno más ya no gana al menos un 10 %: el procesador o la tarjeta ya van
    llenos, y más a la vez solo reparte lo mismo entre más;
  - o uno de ellos falla: la tarjeta (o el chip) tiene un tope de
    compresiones simultáneas, y pasado el tope ese video caería al
    procesador en el render de verdad.

Guarda el mejor en config.json → video.videos_a_la_vez (lo que se ve en
Ajustes → Música y video → Render) y el detalle en video.calibracion.

Lo que no mide: la voz, que sale de internet y no gasta procesador. En el
render de verdad, mientras un video espera la voz otro comprime, así que el
número medido es prudente, no justo.

Topes: 8 en un PC, 4 en el teléfono (más no cabe en su memoria y lo
calienta). Se mira en GitHub antes de escribirlo: check_nvenc_limit solo
cuenta sesiones de la tarjeta, no si más a la vez termina antes.
"""
import os
import sys
import time
import shutil
import tempfile
import argparse
import subprocess
from datetime import datetime

import almacen

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RUTA_CONFIG = os.path.join(BASE_DIR, "config.json")
ES_WINDOWS = os.name == "nt"
# Lo mismo que generar_video_maestro.ES_ANDROID, pero sin el falso positivo
# de un PREFIX suelto en Windows.
ES_ANDROID = not ES_WINDOWS and ("PREFIX" in os.environ or os.path.exists("/sdcard"))

TOPE_PC, TOPE_ANDROID = 8, 4
GANANCIA_MINIMA = 1.10       # uno más tiene que dar al menos un 10 % más
SEGUNDOS_CLIP = 4 if ES_ANDROID else 6


def tope():
    return TOPE_ANDROID if ES_ANDROID else TOPE_PC


def _config():
    datos = almacen.leer(RUTA_CONFIG, {}) or {}
    return datos if isinstance(datos, dict) else {}


def _flags_codificador(nombre):
    """Los mismos ajustes que ejecutar_render en generar_video_maestro."""
    if nombre == "tarjeta NVIDIA":
        return ["-c:v", "h264_nvenc", "-preset", "p4", "-pix_fmt", "yuv420p",
                "-rc", "vbr", "-cq", "23", "-b:v", "0", "-maxrate", "6M", "-bufsize", "12M"]
    if nombre == "chip de video":
        return ["-c:v", "h264_mediacodec", "-pix_fmt", "yuv420p", "-b:v", "4M"]
    vid = _config().get("video") or {}
    return ["-c:v", "libx264", "-preset", str(vid.get("preset", "veryfast")),
            "-crf", str(vid.get("crf", 23)), "-pix_fmt", "yuv420p"]


def _orden(flags, salida, segundos):
    # Un fondo que se mueve (testsrc2) con un desenfoque y un ajuste de color
    # encima: el render de verdad también filtra en el procesador (tarjeta,
    # subtítulos, recortes) antes de comprimir, y sin eso la prueba
    # sobrestimaría cuántos caben.
    return ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", f"testsrc2=size=1080x1920:rate=30:duration={segundos}",
            "-vf", "boxblur=2:1,eq=contrast=1.05:saturation=1.1",
            *flags, "-an", salida]


def _tanda(n, flags, carpeta, segundos):
    """n ffmpeg a la vez. Devuelve (segundos que tardó, cuántos fallaron)."""
    procs = []
    t0 = time.time()
    for i in range(n):
        salida = os.path.join(carpeta, f"prueba_{n}_{i}.mp4")
        procs.append(subprocess.Popen(_orden(flags, salida, segundos),
                                      stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True))
    fallos = 0
    for p in procs:
        _, err = p.communicate()
        fallos += p.returncode != 0
    return time.time() - t0, fallos


def elegir_codificador(carpeta):
    """Con qué comprimiría el render de verdad aquí."""
    if ES_ANDROID:
        vid = _config().get("video") or {}
        return "chip de video" if vid.get("usar_chip_android") else "procesador"
    # En el PC el render prueba la tarjeta y, si no puede, cae al
    # procesador: lo mismo aquí, con un clip de un segundo.
    _, fallos = _tanda(1, _flags_codificador("tarjeta NVIDIA"), carpeta, 1)
    return "procesador" if fallos else "tarjeta NVIDIA"


def medir(ver_solo=False):
    if not shutil.which("ffmpeg"):
        print("❌ Falta ffmpeg.")
        return 1
    carpeta = tempfile.mkdtemp(prefix="calibrar_")
    try:
        codificador = elegir_codificador(carpeta)
        flags = _flags_codificador(codificador)
        fotogramas = SEGUNDOS_CLIP * 30
        print(f"🧪 Midiendo cuántos videos a la vez aguanta este "
              f"{'teléfono' if ES_ANDROID else 'equipo'} (Render ({codificador}), hasta {tope()}).")
        resultados, elegido, motivo = [], 1, ""
        for n in range(1, tope() + 1):
            # La barra del panel sale del primer porcentaje de la línea.
            print(f" ├─ 🧪 [{(n - 1) / tope() * 100:5.1f}%] Probando {n} a la vez…", flush=True)
            segundos, fallos = _tanda(n, flags, carpeta, SEGUNDOS_CLIP)
            if fallos:
                if n == 1:
                    print(f"❌ Ni uno solo sale con {codificador}. Mira que ffmpeg funcione.")
                    return 1
                motivo = (f"con {n} a la vez {fallos} no pudo empezar: es el tope de "
                          f"{'la tarjeta' if codificador != 'procesador' else 'este aparato'}")
                break
            fps = n * fotogramas / segundos
            resultados.append({"n": n, "fps": round(fps, 1), "segundos": round(segundos, 1)})
            print(f" ├─ {n} a la vez: {fps:6.1f} fotogramas/s en total ({segundos:.1f} s)", flush=True)
            if n > 1 and fps < resultados[-2]["fps"] * GANANCIA_MINIMA:
                motivo = (f"con {n} a la vez ya no gana ni un {int((GANANCIA_MINIMA - 1) * 100)} %: "
                          f"{'el procesador' if codificador == 'procesador' else 'el aparato'} va lleno")
                break
            elegido = n
        else:
            motivo = f"sigue ganando hasta el tope de {tope()}"

        mejor = next(r for r in resultados if r["n"] == elegido)
        base = resultados[0]["fps"]
        print(" ├─ 🧪 [100.0%] Hecho")
        print(f"\n✅ {elegido} a la vez ({mejor['fps']:.0f} fotogramas/s, "
              f"{mejor['fps'] / base:.1f}× más que de uno en uno). Motivo: {motivo}.")
        if ver_solo:
            print("(--ver: no se cambió nada)")
            return 0
        cfg = _config()
        vid = dict(cfg.get("video") or {})
        # En automático (lo de fábrica) la medición es solo el punto de
        # partida, mientras no haya tandas de verdad que digan otra cosa: no
        # se pisa el modo. Con un número fijo puesto, se cambia por el medido.
        automatico = not isinstance(vid.get("videos_a_la_vez", "auto"), int)
        if not automatico:
            vid["videos_a_la_vez"] = elegido
        vid["calibracion"] = {
            "fecha": datetime.now().isoformat(timespec="seconds"),
            "codificador": codificador, "elegido": elegido, "motivo": motivo,
            "resultados": resultados,
        }
        cfg["video"] = vid
        almacen.guardar(RUTA_CONFIG, cfg)
        print("   En automático, es el punto de partida hasta que haya tandas de verdad que comparar."
              if automatico else
              "   Queda puesto en Ajustes → Música y video → Render → «Videos a la vez».")
        return 0
    finally:
        shutil.rmtree(carpeta, ignore_errors=True)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Mide cuántos videos a la vez aguanta este aparato.")
    ap.add_argument("--ver", action="store_true", help="Solo medir y enseñarlo, sin guardarlo.")
    args = ap.parse_args(argv)
    return medir(args.ver)


if __name__ == "__main__":
    sys.exit(main())
