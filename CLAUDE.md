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
apaga desde el panel (Ajustes → Más opciones → Render, solo aparece en Android) sin tocar
el archivo a mano.

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
