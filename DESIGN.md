---
name: Mesa de Revisión
description: El panel del pipeline Video Scout, como la sala de control de un canal de TV.
colors:
  suelo-carbon: "#0b0d10"
  modulo-rack: "#15181c"
  modulo-rack-alto: "#1c2025"
  filete: "#2b3037"
  tinta: "#eef0f3"
  tinta-tenue: "#a6aeb8"
  tinta-apagada: "#7a838e"
  tecla-senal: "#4aa3ff"
  tally-aire: "#ff3b30"
  tally-previo: "#34d26d"
  tally-espera: "#ffb224"
  fallo: "#ff5a4f"
  negro-monitor: "#060708"
  estudio-hormigon: "#e4e7eb"
  estudio-modulo: "#fbfcfd"
  estudio-tecla: "#0a5cc2"
  estudio-aire: "#d7261b"
  estudio-previo: "#12803d"
  estudio-espera: "#a86400"
typography:
  display:
    fontFamily: "Rotulo (Bebas Neue), Arial Narrow, Roboto Condensed, sans-serif"
    fontSize: "30px"
    fontWeight: 400
    lineHeight: 1
    letterSpacing: "0.015em"
  headline:
    fontFamily: "Rotulo (Bebas Neue), Arial Narrow, sans-serif"
    fontSize: "17px"
    fontWeight: 400
    lineHeight: 1
    letterSpacing: "0.07em"
  title:
    fontFamily: "system-ui, -apple-system, Segoe UI, Roboto, sans-serif"
    fontSize: "17px"
    fontWeight: 700
    lineHeight: 1.25
  body:
    fontFamily: "system-ui, -apple-system, Segoe UI, Roboto, sans-serif"
    fontSize: "14px"
    fontWeight: 400
    lineHeight: 1.5
  label:
    fontFamily: "ui-monospace, SF Mono, Cascadia Mono, Menlo, Consolas, monospace"
    fontSize: "10.5px"
    fontWeight: 600
    letterSpacing: "0.07em"
rounded:
  monitor: "3px"
  tecla: "4px"
  modulo: "6px"
spacing:
  xs: "6px"
  sm: "10px"
  md: "14px"
  lg: "18px"
components:
  button-tecla:
    backgroundColor: "{colors.modulo-rack-alto}"
    textColor: "{colors.tinta}"
    rounded: "{rounded.tecla}"
    padding: "8px 13px"
    height: "36px"
  button-tecla-go:
    backgroundColor: "{colors.tecla-senal}"
    textColor: "{colors.suelo-carbon}"
    rounded: "{rounded.tecla}"
  tab-bus:
    backgroundColor: "{colors.modulo-rack-alto}"
    textColor: "{colors.tinta-tenue}"
    typography: "{typography.headline}"
    rounded: "{rounded.tecla}"
  tab-bus-on:
    backgroundColor: "{colors.tecla-senal}"
    textColor: "{colors.suelo-carbon}"
  card-modulo:
    backgroundColor: "{colors.modulo-rack}"
    rounded: "{rounded.modulo}"
    padding: "14px"
  monitor-programa:
    backgroundColor: "{colors.negro-monitor}"
    textColor: "{colors.tinta}"
    rounded: "{rounded.modulo}"
  pill-tally:
    textColor: "{colors.tinta-tenue}"
    typography: "{typography.label}"
    rounded: "{rounded.monitor}"
---

# Design System: Mesa de Revisión

## Overview

**Creative North Star: "La sala de control"**

El panel es el control central de un canal de TV de una sola persona. Lo que
se está grabando está **al aire**, lo que espera el visto bueno está en
**previo**, y lo demás es una pared de monitores y un banco de teclas. Cada
estado tiene su luz, y la luz siempre va con palabra o forma al lado: nunca
color solo.

Oscuro es la sala a media luz: suelo carbón, módulos de rack mate separados
por filetes de 1 px, video en negro de monitor. Claro es el mismo estudio
con las luces encendidas: hormigón frío y módulos blancos, pero el monitor
sigue negro, como en la vida. Densidad de herramienta: todo lo que importa
cabe a 412 px con el pulgar y se estira a una barra lateral en PC.

Rechaza el tablero genérico de tarjetas redondeadas con acento turquesa, los
brillos de colores y el adorno sin función.

**Key Characteristics:**
- Tres tallies con significado fijo: rojo al aire, verde previo, ámbar en espera.
- Rótulos UMD en Bebas Neue mayúscula; cuerpo en la sans del sistema; tiempos y contadores en mono tabular.
- Teclas de mezclador con relieve y pulsación (`scale(.97)`), no botones planos.
- El trabajo en curso es un monitor de programa: tira UMD, timecode, línea de tiempo con cabezal.
- Iconos dibujados de Lucide, del color del texto.

## Colors

Neutros fríos casi sin croma; el color está reservado a los tallies y a la tecla que elige el usuario.

### Primary
- **Tecla Señal** (#4aa3ff; #0a5cc2 en claro): acciones y selección — pestaña activa, tecla principal, casillas. Es el acento por omisión (`senal`); el usuario puede cambiarlo en Ajustes → Apariencia, así que nada de significado de estado puede depender de él.

### Secondary
- **Tally Aire** (#ff3b30; #d7261b en claro): algo se está grabando o corriendo ahora. Lámpara de la cabecera, borde del monitor de programa, pill «Al aire».
- **Tally Previo** (#34d26d; #12803d en claro): listo y esperando visto bueno. Marco del video elegido en la pared de monitores y la tira «Previo».
- **Tally Espera** (#ffb224; #a86400 en claro): en pausa, aviso, aparato desactivado.
- **Fallo** (#ff5a4f): error de un trabajo, borrar.

### Neutral
- **Suelo Carbón** (#0b0d10) / **Estudio Hormigón** (#e4e7eb): fondo de página.
- **Módulo de Rack** (#15181c) / **Módulo Estudio** (#fbfcfd): tarjetas y listas.
- **Módulo Alto** (#1c2025): teclas y campos.
- **Filete** (#2b3037): separaciones de 1 px entre módulos.
- **Tinta / Tinta Tenue / Tinta Apagada** (#eef0f3 / #a6aeb8 / #7a838e): texto, secundario, metadatos.
- **Negro Monitor** (#060708): el video y el monitor de programa, en los dos temas.

### Named Rules
**The Tally Rule.** Rojo, verde y ámbar solo dicen «al aire», «previo» y «en espera». Nunca se usan como decoración ni como acento. «Al aire» es publicar (subir a YouTube o TikTok) y nada más; cualquier otro trabajo en marcha enciende la lámpara en el azul de la tecla con lo que hace («Grabando», «Escribiendo guiones»…).

**The Sin Halo Rule.** Las luces encendidas son planas con un filo interior claro (`inset 0 0 0 1px rgba(255,255,255,.35)`), nunca un resplandor difuso de color.

## Typography

**Display Font:** Rotulo = Bebas Neue (servida desde `fuentes/` por `/letra/`, sin internet), con Arial Narrow de respaldo
**Body Font:** system-ui
**Label/Mono Font:** ui-monospace

**Character:** El rótulo condensado de una tira UMD encima de un texto de trabajo neutro; el mono pone los números en columna como en un timecode.

### Hierarchy
- **Display** (400, 30px, 1): título de cada vista («6 videos grabados»).
- **Headline** (400, 17px, 1, 0.07em): encabezados de sección y pestañas, en mayúscula.
- **Title** (700, 17px): títulos de tarjeta y del trabajo en curso.
- **Body** (400, 14px, 1.5): explicaciones, máximo ~70ch.
- **Label** (600, 10.5px, 0.07em, mayúscula): pills, rótulos de monitor, contadores. Metadatos, nunca frases.

### Named Rules
**The Números en Columna Rule.** Todo tiempo o contador va en mono con `tabular-nums`, para que no baile al repintarse cada 1,5 s.

## Layout

Una columna a 412 px: cabecera (glifo, rótulo, lámpara y reloj, tres teclas), fila de pestañas que se parte en dos líneas, y debajo el contenido. Desde 1100 px la cabecera y las pestañas pasan a una barra lateral fija y las pestañas se apilan como teclas de bus. En Revisar, el monitor de previo toma el ancho que le deja la altura de la pantalla en proporción 9:16 y las acciones van a su derecha. Ritmo de espaciado de 6 / 10 / 14 / 18 px. Nunca desplazamiento horizontal de página; la pared de monitores sí desliza por dentro.

## Elevation & Depth

Casi plano: la profundidad la dan los filetes y el relieve de las teclas, no las sombras. Los módulos llevan un borde de 1 px y como mucho una sombra corta de contacto.

### Shadow Vocabulary
- **Relieve** (`inset 0 1px 0 rgba(255,255,255,.05), inset 0 -1px 0 rgba(0,0,0,.4)`): teclas y módulos; el canto de una pieza física.
- **Contacto** (`0 1px 0 rgba(0,0,0,.5), 0 2px 4px rgba(0,0,0,.3)`): toasts y diálogos, lo único que flota.

### Named Rules
**The Borde o Sombra Rule.** Un módulo con filete de 1 px no lleva además una sombra ancha y difusa.

## Shapes

Esquinas casi rectas, de equipo de rack: 3 px en monitores y pills, 4 px en teclas, 6 px en módulos. Los LED son cuadrados, no círculos (salvo el punto de la lámpara AL AIRE). La línea de tiempo lleva marcas cada 10 %.

## Components

### Buttons
- **Shape:** tecla de mezclador (4px), mínimo 36px de alto; 16px de letra en pantallas táctiles para que Android no haga zoom.
- **Primary (`.btn.go`):** fondo Tecla Señal, texto oscuro.
- **Hover / Focus:** hover solo con puntero fino (`@media (hover:hover) and (pointer:fine)`); `:active` con `scale(.97)` en 100 ms `cubic-bezier(.23,1,.32,1)`.
- **Danger:** texto y borde Fallo.

### Chips
- **Style (`.pill`):** mono 10.5px mayúscula, 3px, con un LED cuadrado delante del color de su estado; si lleva icono, el icono sustituye al LED.

### Cards / Containers
- **Corner Style:** 6px. **Background:** Módulo de Rack. **Border:** filete de 1px. **Internal Padding:** 14px.
- **Siguiente paso:** módulo con borde de acento y la pill «Siguiente» en línea antes del título (nunca encima).

### Inputs / Fields
- **Style:** Módulo Alto, filete de 1px, 4px; los `select` llevan flecha dibujada.
- **Focus:** borde de acento.

### Navigation
- **Style:** pestañas como teclas de bus en Rotulo mayúscula con contador mono; la encendida en Tecla Señal con canto inferior más oscuro. En PC, columna en la barra lateral.

### Monitor de programa (signature)
El trabajo en curso: fondo Negro Monitor en los dos temas, tira UMD arriba («Al aire» / «En pausa» / «Terminado» / «Fallo» y timecode HH:MM:SS), borde del color del tally, línea de tiempo con marcas y cabezal blanco. La versión mini es una franja inferior (lower third) cuando se mira otra vista.

### Pared de monitores (signature)
Miniaturas 9:16 con rótulo UMD (número en Rotulo). La elegida lleva anillo Previo de 2px y rótulo verde; las ya subidas, atenuadas con ✓ gris.

## Do's and Don'ts

### Do:
- **Do** poner palabra o forma junto a cada luz de estado (tally + texto).
- **Do** escribir los iconos de botón como emoji al inicio del texto («🗑 Borrar») y añadir el dibujo de Lucide a `ICONO_DE`/`TRAZOS` si falta.
- **Do** mantener el video en Negro Monitor también en el tema claro.
- **Do** probar cada vista a 412px y a 1440px, en los dos temas.

### Don't:
- **Don't** usar resplandores de color (`box-shadow: 0 0 Npx <color>`) ni texto con degradado.
- **Don't** usar rojo, verde o ámbar para algo que no sea su estado.
- **Don't** poner una etiqueta pequeña encima de un título; si hace falta, va en línea.
- **Don't** añadir bordes laterales gruesos de color a avisos o tarjetas.
- **Don't** cargar fuentes o iconos de internet: el panel funciona sin conexión.
