"""
Tramos de los videos de fondo que no se usan, y fondos que no se usan nada.

El render corta trozos de 6-12 s de un fondo al azar, empezando en un punto
al azar (generar_video_maestro.crear_fondo_multi_corte). Si un fondo tiene
un tramo que no conviene —una persona, un logo, algo que YouTube pueda leer
mal: el aviso de "seguridad infantil" del canal salió de un clip de fondo—,
aquí se marca y ningún corte lo vuelve a pisar.

Se guarda en pipeline_state/fondos_excluidos.json, por nombre de archivo:

    {"fondo_vertical_3.mp4": {"entero": false, "tramos": [[42.0, 65.5]]}}

Se edita desde el panel (Ajustes → Música y fondos → «✂ Tramos»), mirando
el video y pulsando «Marcar inicio» / «Marcar fin».
"""
import os
import random

import almacen

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RUTA = os.path.join(BASE_DIR, "pipeline_state", "fondos_excluidos.json")


def cargar():
    """Las exclusiones tal cual; {} si no hay o el archivo está roto.

    almacen.leer y no cargar: un JSON roto aquí no debe tumbar un render.
    Lo peor que pasa es que esa vez se usan los tramos que había excluidos.
    """
    datos = almacen.leer(RUTA, {}) or {}
    return datos if isinstance(datos, dict) else {}


def _guardar(datos):
    # Sin entradas vacías: un fondo sin tramos ni "entero" no pinta nada ahí.
    limpio = {k: v for k, v in datos.items() if v.get("entero") or v.get("tramos")}
    almacen.guardar(RUTA, limpio)
    return limpio


def _de(datos, archivo):
    return datos.setdefault(os.path.basename(archivo), {"entero": False, "tramos": []})


def excluir_tramo(archivo, inicio, fin):
    inicio, fin = float(inicio), float(fin)
    if inicio > fin:
        inicio, fin = fin, inicio
    if fin - inicio < 0.5:
        raise ValueError("El tramo tiene que durar al menos medio segundo.")
    if inicio < 0:
        raise ValueError("El inicio no puede ser negativo.")
    datos = cargar()
    e = _de(datos, archivo)
    e["tramos"] = _unir(e.get("tramos", []) + [[round(inicio, 2), round(fin, 2)]])
    return _guardar(datos)


def quitar_tramo(archivo, indice):
    datos = cargar()
    e = _de(datos, archivo)
    tramos = e.get("tramos", [])
    if not 0 <= int(indice) < len(tramos):
        raise ValueError("Ese tramo ya no existe.")
    del tramos[int(indice)]
    return _guardar(datos)


def poner_entero(archivo, valor):
    datos = cargar()
    _de(datos, archivo)["entero"] = bool(valor)
    return _guardar(datos)


def _unir(tramos):
    """Ordena y funde los tramos que se pisan o se tocan."""
    out = []
    for a, b in sorted([float(a), float(b)] for a, b in tramos):
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


def usable(archivo, datos=None):
    """False si el fondo entero está fuera."""
    datos = cargar() if datos is None else datos
    return not (datos.get(os.path.basename(archivo)) or {}).get("entero")


def permitidos(archivo, duracion, margen_ini=0.5, margen_fin=1.0, datos=None):
    """Los tramos [a, b] del fondo que sí se pueden usar."""
    datos = cargar() if datos is None else datos
    e = datos.get(os.path.basename(archivo)) or {}
    if e.get("entero"):
        return []
    libres, cursor = [], margen_ini
    for a, b in _unir(e.get("tramos", [])):
        if a > cursor:
            libres.append([cursor, a])
        cursor = max(cursor, b)
    fin = duracion - margen_fin
    if fin > cursor:
        libres.append([cursor, fin])
    return libres


def elegir_inicio(archivo, duracion, clip_dur, datos=None, azar=random):
    """Un punto de inicio al azar cuyo corte [ss, ss+clip_dur] no pisa nada
    excluido, o None si al fondo no le queda un hueco tan largo.

    Cada hueco pesa según lo que ofrece, así que un fondo con un tramo
    excluido al principio no concentra todos los cortes al final.
    """
    huecos = [(a, b - clip_dur) for a, b in permitidos(archivo, duracion, datos=datos)
              if b - a >= clip_dur]
    if not huecos:
        return None
    pesos = [max(0.01, b - a) for a, b in huecos]
    a, b = azar.choices(huecos, weights=pesos)[0]
    return azar.uniform(a, b) if b > a else a
