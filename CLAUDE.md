# Cómo trabajar en este proyecto

## Antes de construir algo nuevo: mirar si ya existe

Regla fija, pedida por el dueño del proyecto: **antes de escribir una pieza
nueva, buscar en GitHub si ya hay algo que la resuelva.** No para copiarlo sin
más, sino para decidir a sabiendas: usarlo como dependencia, copiar la idea, o
descartarlo por un motivo concreto.

Lo que hay que contar al proponerlo:

- Qué se encontró y qué hace exactamente.
- Si corre en Termux sobre Android sin root — es el único sitio donde este
  proyecto se ejecuta de verdad. Node, Docker, CUDA, un servidor de WebUI o
  varios gigas de modelo quedan fuera por ahí, aunque el proyecto sea bueno.
- Qué costaría mantenerlo frente a lo que ya hay escrito aquí.
- La licencia.

Descartar algo también es un resultado válido; lo que no vale es no haber
mirado.

## Al terminar cada cambio: los comandos para actualizar el teléfono

Pedido por el dueño del proyecto: **cada vez que se termine una
actualización del código, cerrar con el bloque de Termux para ponerla en el
teléfono**, listo para copiar y pegar, aunque sea el mismo de siempre. Lo
normal:

```bash
cd ~/video-scout-pipeline && git pull
pkill -f servidor.py; panel
```

(`panel` lo crea `instalar_panel.sh`; si se usa la app de Android, basta con
cerrarla y volver a abrirla después del `git pull`.) Si el cambio trae algo
más —una dependencia nueva (`pip install …`), volver a correr
`instalar_panel.sh`, vaciar una caché—, va en el mismo bloque y en el orden
en que hay que hacerlo. Y solo cuando el cambio ya esté en `main`: antes de
mergear, `git pull` no trae nada.

## El contexto real

Todo esto se maneja desde un teléfono Android con Termux, sin PC. Eso manda
sobre cualquier decisión técnica: nada de dependencias pesadas, nada que
suponga una pantalla grande, y el panel tiene que leerse a 412px de ancho.

## Credenciales

Nunca en el código, nunca en el chat, nunca en la línea de comandos (acaban en
`~/.bash_history`). Viven solo en `secretos.env` y `tiktok_token.json`, ambos
en `.gitignore` y con permisos 600.

## Escritura de estado

Todo JSON de `pipeline_state/` se escribe con `almacen.guardar` (tmp +
`os.replace`). Android mata procesos a media escritura y un `publicados.json`
truncado hace que el publicador vuelva a subir el canal entero.

`almacen.cargar` revienta si el archivo está corrupto — es para el código que
decide. `almacen.leer` no revienta nunca — es para el panel, que solo pinta.

## El motor de HyperFrames

Dos cosas que el dueño del proyecto no tiene por qué recordar, así que
**recuérdaselas tú** cada vez que se toque este motor:

**`hyperframes_nucleo.py` es idéntico byte a byte en este repo y en
`video_generation`.** Si se cambia en uno, se copia al otro en el mismo
momento — un `md5sum` de los dos lo confirma. Para `VERSION_CLI` está
`subir_version_hyperframes.py`, que acepta las dos rutas a la vez justamente
para que no se quede a medias. `hyperframes_broll.py`, en cambio, es distinto
en cada repo **a propósito**: aquí un fondo por historia, allá por escena y en
lotes. No intentar igualarlos.

**Subir `VERSION_CLI` sin probarlo es apostar.** El render arranca Chrome
headless, que no existe en Android, así que desde el teléfono no se puede
comprobar que la versión nueva funcione. La versión sube en una rama, se lanza
el workflow *Fabricar fondos con IA* desde esa rama, y solo si salen clips
llega a `main`. Así fue como 0.8.29 acabó fijada sin haber renderizado nunca
nada.

Y si el cambio de versión es para arreglar un render feo, hay que vaciar
`pipeline_state/hyperframes_cache/`: la clave de caché no lleva la versión del
CLI dentro, así que los clips viejos se seguirían sirviendo y parecería que el
cambio no hizo nada.

## El chip de video de Android

`generar_video_maestro.py` puede renderizar con `h264_mediacodec` — el chip
de hardware del teléfono, que el ffmpeg de Termux expone porque está
compilado con `--enable-mediacodec`. Más rápido y con menos batería que
`libx264` por software. Vive apagado por omisión detrás de
`CONFIG["video"]["usar_chip_android"]` en `config.json`, y se enciende o
apaga desde el panel (Ajustes → Música y video → Render, solo aparece en Android) sin tocar
el archivo a mano. Cuál se usó de verdad se ve en la línea de avance del
render, en la tarjeta del trabajo: «Render (chip de video)» o «Render (procesador)», con la misma
etiqueta en la tarjeta; si el
chip falla y cae a CPU a media tanda, el cambio se ve ahí mismo.

**El respaldo automático a CPU no cubre todo tipo de fallo.** Si
`h264_mediacodec` falla con error o deja el archivo vacío, esa misma tanda
cae sola a `libx264` — no hace falta que nadie se entere. Pero el fallo
típico de un chip mal soportado es el contrario: ffmpeg termina bien, el
archivo pesa lo normal, y el video sale con colores raros o rota. Eso el
pipeline **no lo detecta solo**. Si un video con el chip encendido sale mal
a la vista, el remedio es apagar el interruptor en Ajustes, no esperar a
que el pipeline se dé cuenta.

El bitrate por omisión (`4M`, con `-maxrate`/`-bufsize` a juego) está puesto
así a propósito y no más alto: `recomprimir.py` marca como "que pesan de
más" cualquier video de más de 100 MB, y con `duracion_max_short_sec` en
180 s un bitrate constante mayor puede pasarse solo de ese umbral —
disparando una recompresión por CPU después de cada render con el chip, que
anula toda la ganancia de haberlo usado. Ese bitrate sube en la misma
proporción que la resolución elegida (ver más abajo): a más píxeles, más
bits para no perder calidad, y por lo tanto más cerca de los 100 MB.

## La tarjeta NVIDIA del PC: el fallo que no se ve

En un PC el render final prueba `h264_nvenc` y, si falla, cae solo a
`libx264`. Ese respaldo esconde cualquier error de la orden: durante meses
`flags_gpu` llevó `-hwaccel cuda` después de las entradas, ffmpeg la
rechazaba entera («cannot be applied to output url») y **todos** los videos
salían por el procesador sin que nada lo dijera salvo la etiqueta
«Render (procesador)» de la tarjeta del trabajo. Las opciones que van en
`flags_gpu` son de salida; una de entrada (`-hwaccel`, `-ss` de entrada…)
va antes del `-i` o no va. El tope de bitrate (`bitrate_max_nvenc`) está
por la misma razón que el del chip: los 100 MB de `recomprimir.py`.

## Resolución adaptativa: 2K si el fondo lo aguanta

El render ya no está clavado en 1080x1920/1920x1080: si el fondo que le toca
a un video en concreto es nativamente más grande, `elegir_resolucion_render`
sube el lienzo entero (fondo, tarjeta de intro y subtítulos, que tienen que
salir todos del mismo tamaño) hasta ahí, sin pasar de "2K"
(`_TOPE_ALTO_RENDER`, hoy 2560 de lado largo). Hoy esto **no cambia nada en
la práctica**: todo el material de fondo del proyecto —lo que genera
HyperFrames, lo que baja `descargar_fondos.py`— nace en 1080p a propósito
(ver `docs/repos_revisados.md`), así que la función siempre devuelve la
resolución de siempre. Solo se activa el día que haya un fondo de verdad más
grande en la carpeta.

**Por qué escala hasta el más flojo de los fondos candidatos, no el más
fuerte.** `crear_fondo_multi_corte` corta trozos de cualquiera de los
archivos que le tocan a un video para dar variedad. Si de dos candidatos uno
es 2K y el otro 1080p, y el lienzo se pone en 2K, el trozo cortado del
archivo de 1080p sale escalado hacia arriba de mentira — lo mismo que ya se
descartó para el bitrate del chip, pero en resolución. Por eso el objetivo
real es el candidato más flojo del grupo, nunca el más fuerte.

**Un video puede salir a una resolución y el interruptor de Ajustes no lo
cambia.** Esto no tiene botón porque no hace falta uno: es puramente
material — si no hay un fondo más grande que 1080p en la carpeta, el
resultado es idéntico al de siempre. El interruptor del chip de Android
sigue siendo independiente de esto (y el bitrate del chip, arriba, ya
escala solo si algún día esto sí activa una resolución mayor).

## El panel como app: tres cosas que se rompen en silencio

**El nombre `panel-servidor` es un contrato entre dos archivos.** El APK
(`android/.../MainActivity.java`, constante `ARRANCADOR`) llama a un
ejecutable con ese nombre exacto en `$PREFIX/bin`, y quien lo crea es
`instalar_panel.sh`. Si se renombra en uno sin tocar el otro, la app compila
igual, instala igual, y falla en el teléfono con "se arrancó pero nadie
contesta" — que es lo más parecido a un fallo sin causa que hay aquí. Es a
propósito que la ruta del repo no esté dentro del APK: así mover la carpeta
no obliga a recompilar nada. El precio es este contrato, y por eso está
escrito.

**Probar el APK desde el teléfono no se puede**, igual que pasa con
`VERSION_CLI` de HyperFrames. Que compila lo dice el workflow; que Termux
acepte el intent `RUN_COMMAND` solo se ve instalándolo. Así que lo que falle
ahí tiene que caer siempre en la pantalla de ayuda (`assets/ayuda.html`) con
un estado que diga cuál de los tres pasos se rompió — nunca en una pantalla
en blanco, que desde aquí es indistinguible de cualquier otra cosa.

**El service worker no cachea el panel, y tiene que seguir sin hacerlo.**
`web/sw.js` guarda solo `apagado.html` y los iconos. Meter ahí `index.html`
parece una mejora obvia —arrancaría más rápido— y es la forma de romper el
panel sin que nadie se entere: pesa 150 KB, cambia en cada `git pull`, y un
panel viejo hablando con una API nueva falla de maneras que no se parecen a
un problema de caché. Lo mismo con `/api/`, `/video/`, `/audio/` y
`/miniatura/`: son datos vivos. Si algún día cambia lo que sí se guarda, hay
que subir `CACHE` (`mesa-v1`) o los teléfonos seguirán sirviendo lo viejo.

## Hay dos apps de Android, y solo una funciona hoy

`android/app` es la que anda: un WebView que le pide a Termux que arranque
`servidor.py`. `android/nativa` lleva **CPython dentro del APK** (Chaquopy) y
no necesita Termux para nada. La segunda es un experimento con **un muro sin
resolver** (antes eran dos; el de Gemini ya cayó), y por eso todavía no
sustituye a la primera.

**Ningún botón del panel funciona en la app nativa.** `ACCIONES`, en
`servidor.py`, lanza cada etapa como `[sys.executable, "trend_scout.py"]`.
Dentro de Chaquopy `sys.executable` **está vacío**: Python va como librería,
no como ejecutable, y no se puede lanzar un proceso de Python. Arreglarlo
significa correr las etapas dentro del proceso (`runpy` en un hilo, salida
redirigida) y perder la cancelación de verdad — matar un proceso es fiable,
parar un hilo no. Toca el corazón de `servidor.py`, que es el mismo que usa
Termux, así que no se ha hecho. Lo mismo alcanza a `horario.py`: sus
órdenes de cron son `"{py} buscar_diario.py"`, y ahí no hay `{py}` — ni
cron.

**Gemini ya no pasa por un SDK: se llama por REST, y eso no se deshace.**
`gemini.py` habla con la API por `requests` e imita los nombres del SDK
(`Client`, `types.GenerateContentConfig`, `types.Part`, `errors.APIError`)
para que las llamadas de los ocho archivos que lo usan no cambiaran. Volver
a `google-genai` traería otra vez `pydantic-core` —Rust compilado, sin rueda
de Android— y con él el fallo que más cuesta diagnosticar de todo este
repo: pip no da error, se pone a **retroceder** versión por versión durante
media hora en silencio. Si alguien añade `google-genai` al bloque `pip` de
`android/nativa/build.gradle` y la compilación "se cuelga", es esto.

Dos cosas de `gemini.py` que parecen detalles y no lo son. **El texto de
`APIError` incluye el cuerpo JSON crudo de Google** porque todo el manejo de
errores del proyecto lo lee así: `motivo_error_gemini` busca
`API_KEY_INVALID`, y `hyperframes_broll` busca `RESOURCE_EXHAUSTED` y el
`retryDelay`. "Limpiar" ese mensaje rompe el reintento por cuota y la
detección de clave inválida a la vez, y sin ruido. **Y los `type` de los
`SCHEMA_*` se pasan a mayúsculas** antes de mandarlos: los esquemas están
escritos en minúsculas, el REST los quiere en mayúsculas, y si no se
convierten Google responde 400 sin decir qué campo le molesta.

**El chip de video no existe en la app nativa.** `h264_mediacodec` llega por
JNI y necesita una JVM; el ffmpeg que va dentro es un ejecutable y no tiene
ninguna. No hay que tocar nada (`usar_chip_android` ya viene en `false` y el
respaldo a libx264 es automático), pero renderizar ahí es por CPU siempre.

**El zip del pipeline se arma solo, y por eso se puede olvidar.** La tarea
`empaquetarPipeline` mete en el APK `*.py`, `web/`, `fuentes/` y los
`config.*.ejemplo` de la raíz del repo. Un archivo `.py` nuevo entra solo;
**una carpeta nueva no**. Si algún día el pipeline necesita otro directorio,
hay que añadirlo a los `include` de esa tarea o la app arrancará sin él, y
el fallo aparecerá a mitad de una tanda.

## Windows: funciona, pero con tres cuidados

El teléfono sigue siendo el sitio de verdad; Windows es un extra
(`iniciar_windows.bat` → `iniciar_windows.py`). Tres cosas que se rompen en
silencio si se olvidan:

- **Los trabajos corren con `PYTHONUTF8=1`** (`servidor.ENTORNO_HIJOS`). Sin
  eso, en Windows Python escribe en cp1252 y el primer emoji o acento de un
  script revienta la tanda. Un `open()` nuevo sin `encoding=` pasa lo mismo.
- **Pausar y abortar van por `psutil`** en Windows (no hay `killpg` ni
  SIGSTOP). `psutil` está en `requirements.txt` con `sys_platform ==
  "win32"`: quitarle el marcador lo haría instalar en Termux, donde tiene
  que compilarse.
- **Nada que el panel lance puede ser `bash`.** Por eso la revisión es
  `revision_quincenal.py`; el `.sh` se queda solo como envoltorio porque el
  cron del teléfono lo llama por ese nombre.

## Desactivar un aparato: la marca no viaja en el respaldo

⏻ → «Desactivar» (`dispositivo.py`) existe para que el teléfono y el PC no
publiquen a la vez: cada uno lleva su propio `publicados.json`. Mientras
dure, `lanzar` no arranca nada salvo `respaldo.py` (exportar y restaurar
son justo lo que se hace al pasarse de uno a otro), `horario.lineas_cron`
devuelve cero líneas y los scripts del cron y de subida salen al empezar
con `dispositivo.salir_si_desactivado()`.

**La marca es `desactivado.json` en la raíz y no en `pipeline_state/` a
propósito**, y `respaldo.py` la excluye por nombre: si viajara en el
respaldo, exportar desde el teléfono ya desactivado dejaría desactivado
también el PC al restaurar. Un script nuevo que corra solo o que suba
algo lleva esa misma llamada en su `__main__`.

## El horario de cron: el panel elige cuándo, nunca qué

`horario.py` escribe el crontab con lo que se elige en Ajustes → Horario
automático. **Los comandos viven fijos en `horario.TAREAS` y el panel no
puede cambiarlos, y tiene que seguir así.** Todas las interfaces de crontab
que hay en GitHub dejan escribir la orden a mano (ver
`docs/repos_revisados.md`, sección 10); aquí eso convertiría un POST al
panel en algo que corre solo cada media hora sin que nadie mire. Una tarea
nueva se añade a `TAREAS` en el código.

Si cambia el formato de las líneas que genera (la ruta de python, el orden
de los campos), los teléfonos con el cron ya puesto verán «no coincide»
hasta pulsar «Guardar y aplicar» o repetir `bash instalar_cron.sh`: eso va
en el bloque de actualización de ese cambio.

## TikTok: la auditoría no se va a pasar

Cada cierto tiempo vuelve la idea de mandar la app a revisión para desbloquear
el modo directo. **No se manda.** TikTok excluye explícitamente las apps de uso
personal y las herramientas para subir a la cuenta que uno mismo maneja, que es
exactamente lo que es esto; un envío solo deja un rechazo en el historial de la
app. Las reglas citadas y las salidas reales están en
`AUDITORIA_TIKTOK.md`.

El modo borrador tampoco sirve, y esto está comprobado en el teléfono, no
deducido de la documentación: el vídeo se sube al buzón, y al tocar la
notificación **la app lo vuelve a descargar** al móvil para abrir el editor.
Como aquí el render se hace en el propio teléfono, es subirlo y bajarlo para
acabar donde ya estabas. Publicar a mano desde la galería cuesta menos.

Así que hoy la API de TikTok no aporta nada a este proyecto. La etapa se queda
en el repo, funcionando, porque el día que el render salga del teléfono la
cosa cambia: con el vídeo fabricado en el runner, el buzón deja de ser un
viaje redondo y pasa a ser el transporte, y con `PULL_FROM_URL` el móvil ni
siquiera sube nada. Si se llega a eso, los permisos son `user.info.basic` y
`video.upload` — `video.publish` no se pide nunca.

## Imports que parecen sobrar

`import secretos` e `import ruido` se importan por su efecto, no por su
contenido. pyflakes los marca y no respeta `# noqa`. No se borran.
