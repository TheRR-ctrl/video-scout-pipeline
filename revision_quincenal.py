"""
Revisión quincenal: mira qué funcionó en el canal y rehace lo que no.

    python revision_quincenal.py           lo hace
    python revision_quincenal.py --ver     solo lista, no borra nada

Es lo mismo que revision_quincenal.sh (que el cron del teléfono llama por
ese nombre, y que ahora solo pasa aquí), escrito en Python para que corra
igual en Windows, donde no hay bash. Escribe la fecha de cada pasada: sin
ella, dos revisiones seguidas en revision.log no se distinguen.
"""
import os
import sys
import subprocess
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def paso(titulo, *args):
    print(f"\n-- {titulo} " + "-" * max(0, 58 - len(titulo)), flush=True)
    subprocess.run([sys.executable, "relanzar.py", *args], cwd=BASE_DIR)


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    hacerlo = [] if "--ver" in argv else ["--si"]

    print("\n" + "=" * 62)
    print(f"  Revisión del canal — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 62, flush=True)

    # Antes de nada, lo que YouTube quitó o limitó. Solo avisa (en el log y
    # en la pestaña Canal): volver a subir un video que YouTube quitó por un
    # clip de fondo puede costar otra advertencia, así que eso no se hace solo.
    paso("Lo que YouTube quitó o limitó", "--problemas")
    # Primero las repetidas: se borra el refrito y no se rehace, que la
    # historia ya está contada en la copia que sí funcionó. Si se hiciera al
    # revés, la historia repetida entraría por --sin-vistas y volvería a
    # grabarse.
    paso("Copias repetidas", "--duplicados", *hacerlo)
    # Después los que nadie vio. Las guardas van por omisión: no toca nada
    # de menos de 14 días ni rehace una historia que ya se intentó dos veces.
    paso("Los que no vio nadie", "--sin-vistas", *hacerlo)

    # No se llama al render aquí: la tanda «Escribir guiones y grabar» ya
    # corre pipeline.py --hasta video, y encuentra en la cola lo que esto
    # acabe de devolver. Grabar dos tandas el mismo día llenaría el teléfono.
    print("\n  Las historias que volvieron a la cola las graba la tanda")
    print("  «Escribir guiones y grabar» (python horario.py dice cuándo).")
    print("  Para no esperar:  python generar_video_maestro.py\n")
    return 0


if __name__ == "__main__":
    import dispositivo
    dispositivo.salir_si_desactivado()
    sys.exit(main())
