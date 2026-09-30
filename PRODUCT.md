# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Una sola persona: el dueño del canal, que no es programador. Maneja todo el
pipeline él mismo, a partes iguales desde el teléfono Android (en ratos
cortos, a veces con una mano) y desde un PC con Windows (sesiones largas con
el panel abierto mientras renderiza). Los dos tienen que sentirse igual de
cuidados.

## Product Purpose

«Mesa de Revisión» es el panel del pipeline Video Scout: convierte historias
de Reddit y YouTube en Shorts narrados para su canal de YouTube (y los deja
listos para TikTok). El panel es donde decide y vigila; el trabajo pesado
(buscar, escribir guiones con Gemini, grabar con ffmpeg, subir) lo hacen
scripts que el panel lanza.

Éxito: revisar lo grabado y dejar la siguiente tanda en marcha en pocos
toques, entender de un vistazo qué está pasando (qué corre, qué falló y qué
toca hacer) y no subir nunca nada por error.

## Positioning

No es un editor de video ni un SaaS: es la mesa de control de una fábrica
casera que corre en el propio teléfono (Termux) o en el PC, con un único
operador. Todo lo que hace lo hace sobre archivos y procesos de su aparato.

## Operating Context

Tareas por frecuencia (las tres primeras tienen que quedar a un toque):

1. **Revisar y aprobar videos**: verlos, retocar título/descripción,
   rehacer o borrar, publicar pendientes.
2. **Lanzar y vigilar tandas**: buscar historias, escribir guiones,
   grabar; seguir el avance del render (varios videos a la vez, con qué
   codificador, cuánto falta), pausar o abortar.
3. **Ver cómo va el canal**: subidos, vistas, pendientes de TikTok,
   avisos de YouTube (videos quitados o limitados).
4. Ajustes, menos a menudo: estilo de subtítulos, música y fondos, fuentes,
   claves, horario automático, respaldos, desactivar el aparato.

El panel se repinta cada 1,5 s con datos vivos del servidor; los trabajos
largos (renders de minutos u horas) se siguen mientras se hace otra cosa.
Los fallos se avisan en el momento.

## Capabilities and Constraints

- Un solo archivo, `web/index.html` (HTML + CSS + JS sin framework ni build),
  servido por Flask (`servidor.py`). Nada que compilar ni dependencias
  nuevas: se actualiza con `git pull`.
- Tiene que leerse y usarse a 412 px de ancho (teléfono) y aprovechar un PC
  a 1100 px o más (barra lateral, atajos de teclado).
- Corre también dentro de un WebView de Android (la app): sin selector de
  archivos ni descargas allí.
- Siete vistas: Revisar, Cola, Estilo, Subidos, Canal, TikTok, Ajustes
  (con subgrupos). Tarjeta del trabajo en curso, cola de espera, aviso de
  «siguiente paso», alertas de fallo, aparato desactivado (☢️) y apagar (⏻).
- Tema claro/oscuro/automático y color de acento elegibles por el usuario.
- El service worker no cachea el panel (ver CLAUDE.md).
- Idioma: español, trato de tú, frases cortas y concretas.

## Brand Commitments

Ninguna identidad visual obligatoria: el usuario pidió cambiarla entera.
El nombre del producto puede seguir siendo «Mesa de Revisión», pero no es
un compromiso.

## Evidence on Hand

Datos reales del propio aparato (videos, historias, estadísticas del
canal). No hay testimonios, clientes ni marca que mostrar, y no se deben
inventar.

## Product Principles

1. Lo que está pasando se ve sin buscarlo: qué corre, qué falló, qué toca.
2. Nada irreversible sin confirmación clara (borrar, publicar, desactivar).
3. Igual de usable con el pulgar en el teléfono que con ratón y teclado.
4. El panel acompaña trabajos largos: nunca pierde lo que estás mirando al
   repintarse.
5. Explica en lenguaje llano; nada de jerga técnica si hay una palabra normal.

## Accessibility & Inclusion

Uso con una mano y bajo el sol en el teléfono: contraste alto, objetivos
táctiles amplios, nada que dependa solo del color.
