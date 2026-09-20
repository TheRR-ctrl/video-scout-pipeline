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

## Imports que parecen sobrar

`import secretos` e `import ruido` se importan por su efecto, no por su
contenido. pyflakes los marca y no respeta `# noqa`. No se borran.
