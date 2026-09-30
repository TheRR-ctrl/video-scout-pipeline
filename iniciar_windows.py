"""
Arranque en Windows: deja todo listo y abre el panel.

No se llama a mano: lo lanza iniciar_windows.bat (doble clic), que antes
crea el entorno .venv con el Python que haya. Cada vez que arranca:

  1. Trae la última versión del código (git pull), si hay git.
  2. Instala o pone al día las librerías, solo si cambió requirements.txt.
  3. Comprueba que esté ffmpeg; si no, lo instala con winget.
  4. Si es la primera vez (no hay claves ni estado) y hay un respaldo en
     Descargas, ofrece restaurarlo (respaldo.py --restaurar).
  5. Arranca el panel y abre el navegador.

En el teléfono todo esto lo hacen instalar.sh y restaurar.sh; aquí no hay
bash ni pkg, por eso va en Python.
"""
import os
import sys
import shutil
import hashlib
import subprocess

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MARCA_REQUISITOS = os.path.join(BASE_DIR, ".venv", "requisitos.sha1")


def titulo(texto):
    print(f"\n  {texto}", flush=True)


def correr(cmd, **kw):
    return subprocess.run(cmd, cwd=BASE_DIR, **kw)


def actualizar_codigo():
    if not shutil.which("git") or not os.path.isdir(os.path.join(BASE_DIR, ".git")):
        return
    titulo("Buscando actualizaciones…")
    r = correr(["git", "pull", "--ff-only"], capture_output=True, text=True)
    salida = (r.stdout + r.stderr).strip()
    if r.returncode != 0:
        print("   ○ No se pudo actualizar (sigue con la versión que hay):")
        print("     " + salida.splitlines()[-1] if salida else "")
    elif "Already up to date" in salida or "Ya está actualizado" in salida:
        print("   ✓ ya está al día")
    else:
        print("   ✓ actualizado")


def instalar_librerias():
    with open(os.path.join(BASE_DIR, "requirements.txt"), "rb") as f:
        huella = hashlib.sha1(f.read()).hexdigest()
    try:
        with open(MARCA_REQUISITOS, encoding="utf-8") as f:
            if f.read().strip() == huella:
                return
    except OSError:
        pass
    titulo("Instalando librerías (la primera vez tarda unos minutos)…")
    r = correr([sys.executable, "-m", "pip", "install", "-q", "--disable-pip-version-check",
                "-r", "requirements.txt"])
    if r.returncode != 0:
        print("   ✗ Falló la instalación de librerías. Revisa el mensaje de arriba.")
        sys.exit(1)
    with open(MARCA_REQUISITOS, "w", encoding="utf-8") as f:
        f.write(huella)
    print("   ✓ librerías listas")


def comprobar_ffmpeg():
    if shutil.which("ffmpeg") and shutil.which("ffprobe"):
        return
    titulo("Falta ffmpeg (hace los videos). Instalándolo con winget…")
    if not shutil.which("winget"):
        print("   ✗ No hay winget. Instala ffmpeg a mano desde https://www.gyan.dev/ffmpeg/builds/")
        print("     y vuelve a abrir iniciar_windows.bat.")
        input("\n  Pulsa Enter para salir…")
        sys.exit(1)
    correr(["winget", "install", "-e", "--id", "Gyan.FFmpeg",
            "--accept-source-agreements", "--accept-package-agreements"])
    # winget cambia el PATH del sistema, pero esta ventana no lo ve hasta
    # abrir una nueva.
    print("\n   ✓ Instalado. Cierra esta ventana y vuelve a abrir iniciar_windows.bat.")
    input("\n  Pulsa Enter para salir…")
    sys.exit(0)


def ofrecer_restaurar():
    primera_vez = not (os.path.exists(os.path.join(BASE_DIR, "secretos.env"))
                       or os.path.exists(os.path.join(BASE_DIR, "pipeline_state", "publicados.json")))
    if not primera_vez:
        return
    import respaldo
    archivo = respaldo.buscar_respaldo()
    if not archivo:
        print("\n   ○ Primera vez aquí. Si tienes un respaldo del teléfono")
        print("     (video-scout-respaldo-FECHA.tar), ponlo en Descargas y vuelve a abrir.")
        return
    print(f"\n  Encontré un respaldo: {os.path.basename(archivo)}")
    if input("  ¿Restaurarlo ahora (claves, historias, estado y videos)? [S/n] ").strip().lower() in ("", "s", "si", "sí", "y"):
        respaldo.restaurar(archivo)


def main():
    os.chdir(BASE_DIR)
    print("\n  ===== Video Scout · Mesa de Revisión =====")
    actualizar_codigo()
    instalar_librerias()
    comprobar_ffmpeg()
    ofrecer_restaurar()
    titulo("Abriendo el panel en el navegador. Para cerrarlo, cierra esta ventana.\n")
    # Sin auto-apagado: en el PC el panel vive mientras la ventana esté abierta.
    return correr([sys.executable, "servidor.py", "--abrir", "--no-apagar"]).returncode


if __name__ == "__main__":
    sys.exit(main())
