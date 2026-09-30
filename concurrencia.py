"""
Cuántos videos grabar a la vez: elegirlo solo, con datos de este aparato.

Tres capas, de la más barata a la más fina:

1. La medición (calibrar_render.py, botón «Medir»): un clip de prueba con
   1, 2, 3… a la vez. Es prudente —todos comprimen a la vez, sin las
   esperas del render de verdad— y sirve de punto de partida.

2. El historial (pipeline_state/rendimiento_render.json): cada tanda de
   render apunta cuántos videos terminó por hora y con cuántos a la vez de
   media. La siguiente empieza desde el que mejor rindió de verdad, que es
   lo que importa: no «que funcione», sino que termine más por hora.

3. El regulador, durante la tanda: cada pocos segundos mira el procesador,
   la tarjeta y la memoria. Si todos los huecos están ocupados y hay margen,
   abre uno más; si algo va al límite, cierra uno (no mata ningún video: solo
   no arranca el siguiente hasta que baje). Si la tarjeta rechaza una
   compresión, ese es su tope y no lo vuelve a pasar en esta tanda.
   La idea es la de los limitadores adaptativos (sumar de uno en uno,
   recortar al primer síntoma); los de GitHub son para peticiones HTTP y no
   miden procesador ni tarjeta, así que no sirven tal cual.

En Android el uso total del procesador no se puede leer (/proc/stat está
cerrado desde Android 8), así que allí no hay capa 3: se usa el historial o
la medición, y nunca se prueba más de lo medido — calentar el teléfono para
explorar no compensa.
"""
import os
import re
import time
import shutil
import subprocess
from datetime import datetime

import almacen

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RUTA_HISTORIAL = os.path.join(BASE_DIR, "pipeline_state", "rendimiento_render.json")
TANDAS_QUE_SE_GUARDAN = 40

# Umbrales del regulador. Por debajo de estos, hay sitio para uno más.
CPU_HAY_SITIO, GPU_HAY_SITIO, RAM_HAY_SITIO = 80, 85, 85
# Por encima de estos, sobra uno.
CPU_LLENO, RAM_LLENA = 95, 92
SEGUNDOS_ENTRE_DECISIONES = 15
# Tras abrir un hueco, esperar antes de abrir otro: el video nuevo pasa su
# primer tramo esperando la voz (internet, casi sin procesador), y midiendo
# enseguida parecería que sigue sobrando sitio. Bajar no espera.
SEGUNDOS_TRAS_SUBIR = 45


# ---------------------------------------------------------
# Lo que se puede medir del aparato
# ---------------------------------------------------------
class Medidor:
    """Uso del procesador, la tarjeta y la memoria de TODO el aparato, en %.
    None en lo que no se pueda leer (el procesador en Android, la tarjeta sin
    NVIDIA)."""

    def __init__(self):
        self._prev = None
        self._psutil = None
        try:
            import psutil
            psutil.cpu_percent(interval=None)      # la primera llamada da 0
            self._psutil = psutil
        except Exception:
            pass
        self._nvidia = shutil.which("nvidia-smi")

    def puede_medir_cpu(self):
        """En Android /proc/stat está cerrado: no hay con qué regular."""
        if self._psutil:
            return True
        try:
            with open("/proc/stat") as f:
                return f.readline().startswith("cpu")
        except OSError:
            return False

    def cpu(self):
        if self._psutil:
            return self._psutil.cpu_percent(interval=None)
        try:
            with open("/proc/stat") as f:
                campos = [float(x) for x in f.readline().split()[1:]]
        except (OSError, ValueError):
            return None
        ocioso, total = campos[3] + (campos[4] if len(campos) > 4 else 0), sum(campos)
        prev, self._prev = self._prev, (ocioso, total)
        if not prev or total <= prev[1]:
            return None
        return max(0.0, min(100.0, (1 - (ocioso - prev[0]) / (total - prev[1])) * 100))

    def ram(self):
        if self._psutil:
            return self._psutil.virtual_memory().percent
        try:
            with open("/proc/meminfo") as f:
                datos = dict(l.split(":", 1) for l in f if ":" in l)
            total = float(datos["MemTotal"].split()[0])
            libre = float(datos["MemAvailable"].split()[0])
            return (1 - libre / total) * 100
        except (OSError, KeyError, ValueError, ZeroDivisionError):
            return None

    def gpu(self):
        """El más alto entre uso general y el del compresor (NVENC): el
        render solo usa el compresor, que puede ir lleno con la GPU al 40 %."""
        if not self._nvidia:
            return None
        try:
            r = subprocess.run([self._nvidia, "--query-gpu=utilization.gpu,utilization.encoder",
                                "--format=csv,noheader,nounits"],
                               capture_output=True, text=True, timeout=2)
            nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", r.stdout.splitlines()[0])]
            return max(nums) if nums else None
        except Exception:
            return None


# ---------------------------------------------------------
# El regulador de la tanda
# ---------------------------------------------------------
class Regulador:
    def __init__(self, inicial, tope, medidor=None, reloj=time.monotonic, ajustar=True):
        self.tope = max(1, tope)
        self.limite = max(1, min(inicial, self.tope))
        self.inicial = self.limite
        self.maximo = self.limite
        self.medidor = medidor
        self.reloj = reloj
        # Sin procesador medible (Android) no se ajusta nada durante la tanda.
        self.ajustar = bool(ajustar and medidor is not None and medidor.puede_medir_cpu())
        if self.ajustar:
            medidor.cpu()          # la primera lectura solo deja la referencia
        self._muestras = []
        self._ultima = reloj()
        self._no_subir_hasta = 0.0
        self._t_prev = reloj()
        self._area = 0.0           # activos × segundos, para la media
        self._t0 = reloj()
        self.motivo = "fijo" if not self.ajustar else "empezando"

    def tope_de_la_tarjeta(self, activos_comprimiendo):
        """La tarjeta rechazó una compresión: su tope son las que sí estaban
        comprimiendo. No se vuelve a pasar en esta tanda."""
        self.tope = max(1, min(self.tope, activos_comprimiendo))
        self.limite = min(self.limite, self.tope)
        self.motivo = f"tope de la tarjeta: {self.tope}"

    def tick(self, activos):
        """Se llama a menudo desde el bucle de la tanda. Devuelve el límite."""
        ahora = self.reloj()
        self._area += activos * (ahora - self._t_prev)
        self._t_prev = ahora
        if not self.ajustar:
            return self.limite
        m = self.medidor
        self._muestras.append((m.cpu(), m.gpu(), m.ram()))
        if ahora - self._ultima < SEGUNDOS_ENTRE_DECISIONES:
            return self.limite
        self._ultima = ahora
        cpu = _media(x[0] for x in self._muestras)
        gpu = _media(x[1] for x in self._muestras)
        ram = _media(x[2] for x in self._muestras)
        self._muestras = []
        if cpu is None:
            return self.limite
        if cpu > CPU_LLENO or (ram is not None and ram > RAM_LLENA):
            if self.limite > 1:
                self.limite -= 1
                self.motivo = f"bajando: {'procesador' if cpu > CPU_LLENO else 'memoria'} al límite"
        elif (ahora >= self._no_subir_hasta and activos >= self.limite and self.limite < self.tope
              and cpu < CPU_HAY_SITIO
              and (gpu is None or gpu < GPU_HAY_SITIO) and (ram is None or ram < RAM_HAY_SITIO)):
            self.limite += 1
            self._no_subir_hasta = ahora + SEGUNDOS_TRAS_SUBIR
            self.maximo = max(self.maximo, self.limite)
            self.motivo = f"subiendo: procesador al {cpu:.0f}%" + (f", tarjeta al {gpu:.0f}%" if gpu is not None else "")
        return self.limite

    def media(self):
        dur = self._t_prev - self._t0
        return self._area / dur if dur > 0 else float(self.limite)


def _media(valores):
    v = [x for x in valores if x is not None]
    return sum(v) / len(v) if v else None


# ---------------------------------------------------------
# El historial entre tandas
# ---------------------------------------------------------
def historial():
    datos = almacen.leer(RUTA_HISTORIAL, [])
    return [d for d in datos if isinstance(d, dict)] if isinstance(datos, list) else []


def apuntar_tanda(videos, segundos, regulador, codificador=None):
    """Una tanda terminada. Solo cuenta si dice algo: dos videos o más y más
    de un minuto (una tanda de uno no mide ningún «a la vez»)."""
    if videos < 2 or segundos < 60:
        return None
    entrada = {
        "fecha": datetime.now().isoformat(timespec="seconds"),
        "videos": videos, "segundos": round(segundos),
        "por_hora": round(videos / (segundos / 3600), 1),
        "media": round(regulador.media(), 1),
        "inicial": regulador.inicial, "maximo": regulador.maximo,
        "final": regulador.limite, "motivo": regulador.motivo,
        "codificador": codificador,
    }
    almacen.guardar(RUTA_HISTORIAL, (historial() + [entrada])[-TANDAS_QUE_SE_GUARDAN:])
    return entrada


def resumen():
    """{n: {"por_hora": media, "tandas": k}} por número a la vez (la media
    redondeada de cada tanda), para el panel y para elegir el inicio."""
    grupos = {}
    for t in historial():
        try:
            n = max(1, round(float(t["media"])))
            grupos.setdefault(n, []).append(float(t["por_hora"]))
        except (KeyError, TypeError, ValueError):
            continue
    return {n: {"por_hora": round(sum(v) / len(v), 1), "tandas": len(v)}
            for n, v in sorted(grupos.items())}


def punto_de_partida(tope, calibrado=None, por_omision=1):
    """Con cuántos empezar en automático: el que mejor rindió en tandas de
    verdad; si no hay historial, lo medido; si tampoco, lo de siempre."""
    r = resumen()
    if r:
        mejor = max(r.items(), key=lambda kv: kv[1]["por_hora"])[0]
        return max(1, min(tope, mejor))
    if calibrado:
        return max(1, min(tope, int(calibrado)))
    return max(1, min(tope, por_omision))
