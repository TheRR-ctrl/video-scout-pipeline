---
version: 1
slug: "web-index-html"
primary_target: "web/index.html"
related_targets: []
---

# Mesa de Revisión — panel (web/index.html)

Scope: el panel entero (siete vistas, tarjeta del trabajo, cola, avisos, diálogos). Visitor mode: Operate.
Audience/job: un único operador, teléfono (412 px, ratos cortos, sol) y PC (sesiones largas). Tareas a un toque: revisar y aprobar videos, lanzar y vigilar tandas, ver el canal.
Constraints: un solo HTML sin build ni dependencias; la misma DOM/JS (solo cambia la piel y la cabecera/tarjeta); tema claro/oscuro/auto y acento elegibles; WebView de Android; sin fuentes de internet.

## Direction contract

THESIS: El panel es el control central de un canal de TV. Lo que se graba está AL AIRE (tally rojo), lo que espera aprobación está en PREVIO (tally verde), lo demás es una pared de monitores y un banco de teclas. Rechaza el tablero genérico de tarjetas redondeadas con acento turquesa.

OWN-WORLD: Oscuro de sala de control: suelo carbón, módulos de rack mate separados por filetes de 1 px, negro de monitor para el video. Claro «estudio con luces»: gris hormigón frío, módulos blancos. Tally rojo (al aire), verde (previo/listo), ámbar (en espera). Rótulos UMD en Bebas Neue en mayúsculas; cuerpo en la sans del sistema; timecode y contadores en mono tabular. Teclas de mezclador con luz propia al estar activas; estado siempre con texto o forma además del color.

STORY: De un vistazo sabe qué está al aire, qué espera en previo y qué toca; corta lo siguiente al aire (aprobar, grabar, publicar) sin buscarlo.

FIRST VIEWPORT: Cabecera: glifo y «Mesa de Revisión» en rótulo; a la derecha la lámpara AL AIRE (encendida roja con un trabajo en marcha, apagada «EN ESPERA» si no) y el reloj de estudio HH:MM:SS; debajo, las pestañas como fila de teclas de bus. Luego el monitor de PROGRAMA (trabajo en curso: timecode transcurrido, barra de línea de tiempo con cabezal) y, en Revisar, la pared de monitores con rótulo UMD y marco tally; el monitor de PREVIO del video elegido con sus teclas de acción.

FORM: sala de control de TV, n.º 1 de mi lista (IMPECCABLE'S PICK elegida por el usuario), seed af8722d2. Interacción firma: la lámpara AL AIRE se enciende con rampa de brillo al empezar un trabajo y el cabezal recorre la línea de tiempo del monitor de programa.

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance
