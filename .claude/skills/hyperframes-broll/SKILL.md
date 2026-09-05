---
name: hyperframes-broll
description: Genera video de apoyo (b-roll, fondos, motion graphics) escribiendo composiciones HTML y renderizándolas a MP4 determinista con el CLI de HyperFrames. Úsala al escribir, depurar o extender `hyperframes_broll.py`, al crear un PerfilVisual nuevo, cuando un render de HyperFrames falle o salga en negro, o cuando haya que decidir entre este motor y uno generativo (Veo/Seedance) o Manim. Cubre el contrato de composición (data-start, data-duration, window.__timelines), las reglas de determinismo, los comandos del CLI y los fallos típicos.
---

# B-roll con HyperFrames (HTML → MP4)

[HyperFrames](https://hyperframes.heygen.com) (Apache-2.0, de HeyGen) convierte
un `index.html` en un MP4 determinista. La clave: **no reproduce la página**.
Le pide a Chrome headless un frame concreto a la vez —`seek(0)`, `seek(1/30)`,
`seek(2/30)`…— con el compositor pausado, y encadena los frames con ffmpeg.
Nunca llama a `play()`, así que el resultado no depende de la velocidad de la
máquina: **mismo HTML → mismo MP4**.

Por eso funciona bien con un LLM en el bucle: escribir HTML/CSS/GSAP se le da
mucho mejor que manejar un editor de video, y el resultado es un proyecto de
texto editable, no un artefacto opaco.

## Cuándo usar este motor

| | generativo (Veo, Seedance) | Manim | **HyperFrames** |
|---|---|---|---|
| Costo | de pago | gratis | gratis |
| Velocidad | minutos/clip | ~1 min/clip | ~3x tiempo real |
| Duración | fija (~8 s) | fija (~8 s) | **la que pidas** |
| Fuerte en | fotorrealismo | matemáticas, geometría | tipografía, tarjetas, datos, fondos abstractos |
| Débil en | costo, control | imágenes reales | imágenes reales |

Elige HyperFrames cuando el plano no necesite parecer una foto: fondos
abstractos bajo narración, kinetic type, gráficas, lower-thirds, tarjetas de
datos. Para un plano fotorrealista concreto, sigue siendo un modelo generativo.

## El módulo de este repo

`hyperframes_broll.py` (idéntico en `video-scout-pipeline` y `Video_Generation`
— si lo tocas en uno, cópialo al otro).

```python
generar_clip_cacheado(prompt_visual, aspecto="16:9", modelo=..., reintentos=3,
                      duracion_seg=None, perfil=PERFIL_NARRACION_REFLEXIVA) -> ruta | None
```

Misma interfaz que `veo_broll` / `manim_broll`, más `duracion_seg` y `perfil`.
Flujo interno: pide el HTML a Gemini → `hyperframes lint --json` → si hay
errores se los devuelve al modelo con el `fixHint` del propio linter → render →
cachea por hash de `perfil + aspecto + duración + prompt`.

### Perfiles visuales

Lo único específico de cada pipeline. Un `PerfilVisual` describe **qué se
ilustra, qué se superpone encima y qué zonas del cuadro deben quedar libres**:

- `PERFIL_NARRACION_REFLEXIVA` — video largo 16:9, subtítulos karaoke abajo,
  tarjeta de título arriba, duración exacta por escena (no loopea).
- `PERFIL_HISTORIA_VERTICAL` — Short 9:16, subtítulos grandes al centro,
  animación **cíclica** porque el clip se loopea para cubrir la historia.

Para un pipeline nuevo, rellena el dataclass; no toques el motor. Cambiar
`nombre` invalida la caché de ese perfil, que es lo que quieres al retocar la
dirección de arte.

## Contrato de composición

Lo mínimo que debe cumplir el HTML generado:

```html
<!doctype html>
<html lang="es">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=1920, height=1080" />
  <script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script>
  <style>
    body { margin: 0; }
    #root { position: relative; width: 1920px; height: 1080px; overflow: hidden; }
    .clip { position: absolute; inset: 0; }
  </style>
</head>
<body>
  <div id="root" data-composition-id="main" data-start="0" data-duration="6"
       data-width="1920" data-height="1080">
    <section id="escena" class="clip" data-start="0" data-duration="6">…</section>
  </div>
  <script>
    window.__timelines = window.__timelines || {};
    const tl = gsap.timeline({ paused: true });
    tl.fromTo("#algo", { opacity: 0 }, { opacity: 1, duration: 1.2, ease: "power3.out" }, 0.2);
    window.__timelines.main = tl;   // la clave = data-composition-id
  </script>
</body>
</html>
```

Reglas que importan:

- **`data-duration` de la raíz es la duración del render.** El compilador la lee
  antes de ejecutar los scripts, así que ningún script puede cambiarla.
- La timeline se registra en `window.__timelines[<data-composition-id>]`,
  **de forma síncrona** y **pausada** (`{ paused: true }`).
- `data-track-index` es solo una fila de la interfaz de Studio: no controla el
  orden de pintado ni impide solapes. Para capas, usa `z-index` de CSS.
- `data-start` admite tiempos relativos: `"otro-clip + 0.5"`.
- HyperFrames es dueño de la reproducción de medios: **nunca** llames a
  `play()`, `pause()` ni asignes `currentTime`.

### Determinismo (aquí se rompen casi todas las composiciones)

El render pide frames sueltos y fuera de orden. El estado visual en el segundo
`T` debe depender **solo de `T`**. Prohibido:

- `Date`, `performance.now()`, `Math.random()` sin semilla.
- `requestAnimationFrame`, `setTimeout`, `setInterval`.
- `repeat: -1` en GSAP o `animation: … infinite` en CSS.

Si algo "avanza solo", saldrá congelado o a saltos en el MP4.

## Comandos del CLI

```bash
npx hyperframes lint <dir> --json       # sin navegador, ~1 s: úsalo antes de renderizar
npx hyperframes render <dir> -o out.mp4 --fps 30 --quality standard --quiet
npx hyperframes snapshot <dir> --at 0,2,5   # frames sueltos para revisar sin render completo
npx hyperframes doctor                  # diagnostica la máquina (ffmpeg, Chrome, caché)
npx hyperframes info <dir> --json       # duración y metadatos de la composición
```

Flags de `render` que se usan aquí: `-o/--output`, `--fps` (1-240),
`--quality draft|standard|high`, `--format mp4|webm|mov|gif|png-sequence`
(WebM y MOV llevan transparencia, útil para overlays), `--resolution` para
supersamplear, `--docker` para render bit a bit reproducible.

En corridas desatendidas, exporta `HYPERFRAMES_NO_TELEMETRY=1`,
`DO_NOT_TRACK=1` y `HYPERFRAMES_NO_UPDATE_CHECK=1` — el módulo ya lo hace.

## Depurar un render que falla

1. **Empieza siempre por `lint --json`.** Devuelve `errorCount` y un array
   `findings` con `code`, `message` y **`fixHint`** — el fixHint es texto listo
   para pasarle al modelo, y sube mucho la tasa de acierto del reintento.
2. **`missing_gsap_script`** — la composición usa GSAP sin cargarlo. Es el fallo
   más común de un modelo.
3. **`missing_data_no_timeline`** — no registra `window.__timelines`. Ojo: no es
   solo cosmético, el render **espera 45 s** por esa timeline antes de rendirse,
   así que dispara el timeout del lote.
4. **Vídeo en negro o congelado** → casi siempre determinismo: busca `Math.random`,
   `rAF`, `setInterval` o `repeat: -1`.
5. **`FFmpeg not found` / `FFmpeg cannot start`** → ffmpeg y ffprobe deben estar
   en el PATH. Este error también aparece de forma intermitente bajo carga; el
   reintento del módulo lo absorbe.
6. **El clip dura menos de lo pedido** → el `data-duration` de la raíz no
   coincide con la duración de la timeline. La raíz manda.

## Límites que conviene tener presentes

- El render va a ~3x tiempo real a 1080p. Para fondos largos, genera una
  composición corta **cíclica** y loopéala con ffmpeg (es lo que hace
  `PERFIL_HISTORIA_VERTICAL`), en vez de renderizar los minutos enteros.
- Necesita salida a internet durante el render: la composición carga GSAP desde
  jsDelivr. Para renderizar sin red, hay que empotrar GSAP en el HTML.
- Requiere Node.js ≥ 22.
- No genera imágenes fotorrealistas. Si el guion pide "una playa al atardecer",
  este no es el motor.
