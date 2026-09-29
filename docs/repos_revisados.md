# Repos y técnicas revisadas antes de tocar cada etapa

Sigue la regla de `CLAUDE.md`: antes de escribir algo nuevo, mirar qué ya
existe. Va por etapa del pipeline, no por "proyectos parecidos" — buscar
"reddit to shorts pipeline" en genérico solo trae repos de Node/Docker que
se descartan en la primera línea por no correr en Termux. Un repo entero que
resuelva las seis etapas de este proyecto y quepa en un teléfono sin root no
existe; lo que sí hay son piezas sueltas por etapa, y de ahí sale lo de abajo.

## 1. Lectura de Reddit sin API — SE ADOPTÓ

**Qué se encontró.** No un repo concreto, sino una técnica documentada y
pública de Reddit: pedir varios subreddits en una sola llamada con
`r/sub1+sub2+.../top/.rss`. No es un bypass ni girar user-agents — es
sintaxis que Reddit expone para armar un feed combinado a mano, y cada
`<entry>` trae su subreddit real en `<category term="...">`, así que la
atribución por post no se pierde.

**Por qué importa aquí.** El límite de peticiones de Reddit es por IP, no
por subreddit. Con 36 subreddits pidiendo uno por uno, cada aviso de la
noche (buscar_diario.py) hacía 36 peticiones y algunas volvían con 429 —
se vio en vivo esta misma noche, con AITAH bloqueado. Agrupando de a 4
subreddits, la misma búsqueda hace 9 peticiones en vez de 36.

**Comprobado en esta sesión, contra reddit.com real, no en documentación:**
un escaneo completo de los 36 subreddits configurados, con el cambio puesto,
dio **36/36 leídos, 0 fallados, en unos 2 minutos** (antes: ~7 minutos con
429 sueltos). El límite real de resultados por petición son 100 — pedir más
no da más.

**Corre en Termux.** Sí — es un cambio de URL en `requests.get`, cero
dependencias nuevas.

**Costo de mantenimiento.** Ninguno adicional: mismo código, mismo
`requests`, misma librería estándar `xml.etree.ElementTree` que ya se usaba
para parsear el feed.

**Licencia.** No aplica — es sintaxis pública de la API de Reddit, no código
de terceros.

**Lo que cuesta.** Dentro de un mismo grupo, Reddit devuelve los mejores
posts del conjunto, no "los N mejores de cada subreddit". Un subreddit con
posts de menos puntuación puede quedar tapado por otro del mismo grupo si el
grupo es grande. Se probó agrupar los 36 en una sola petición: funciona, pero
solo 21 de los 36 subreddits llegaban a aparecer entre los 100 resultados
—los de menor tracción quedaban afuera siempre. Por eso el grupo se dejó en
4 (`subreddits_por_tanda` en `config_trends.json`), que reparte mucho mejor y
sigue bajando las peticiones de 36 a 9.

**Un efecto colateral que casi se cuela.** El campo `rank_en_subreddit`
—con el que luego se eligen los 20 candidatos finales de cada corrida—
se estaba contando como la posición dentro de la RESPUESTA DEL GRUPO, no
dentro de cada subreddit real. Eso reproducía, en la selección, el mismo
problema que el agrupado arregla en la lectura: el subreddit que domina un
grupo se quedaría con los ranks bajos y taparía a los demás del grupo a la
hora de elegir. Comprobado con los 36 subreddits reales: con el error, no
hay forma sencilla de medirlo sin instrumentar el código (no llegó a
publicarse así); una vez corregido para contar por subreddit real, los 20
candidatos finales de una corrida completa vinieron de **20 subreddits
distintos** — la misma diversidad de antes de tocar nada.

**Implementado en:** `trend_scout.py` (`obtener_posts_publicos`,
`escanear`), con pruebas en `prueba_grupos_reddit.py` (15, incluida la que
blinda que un subreddit con un solo post no pierda su rank 0 por compartir
grupo con uno de más tracción).

## 2. Subtítulos karaoke con timing por palabra — YA LO TENEMOS, y más fino

**Qué se encontró.** `rany2/edge-tts` (MIT, el mismo paquete que ya usa este
proyecto) trae un módulo `submaker.py` que arma subtítulos a partir de los
eventos `WordBoundary` de Edge TTS. Hay también repos externos
(`Edge-TTS-Subtitle-Dubbing`) que sincronizan audio a un SRT ya existente
con Numpy/Librosa.

**Decisión: no se adopta nada.** `generar_video_maestro.py` ya lee los
eventos `WordBoundary` directamente (`boundary="WordBoundary"` en
`Communicate()`) y arma el `.ass` de karaoke a mano
(`convertir_timing_a_karaoke_ass`), con el mismo timing por palabra que
ofrece el `submaker` de la librería — sin la capa intermedia. Añadir
`Edge-TTS-Subtitle-Dubbing` sumaría Numpy/Librosa para resolver un problema
(sincronizar audio a un SRT ajeno) que este proyecto no tiene: aquí el audio
y el timing salen de la misma llamada a Edge TTS, no de piezas separadas.

## 3. Codificación de video en Termux — IMPLEMENTADO, apagado por omisión, falta probarlo en el teléfono

**Qué se encontró.** El ffmpeg que empaqueta Termux trae
`--enable-mediacodec`, que expone el chip de codificación de video de
Android (`h264_mediacodec`) como códec de hardware. Comparado con la
codificación por software que usa hoy el proyecto (`libx264`), es
sustancialmente más rápido y gasta menos batería — es la diferencia entre
que el chip dedicado haga el trabajo o que lo haga la CPU a pulso.

**Qué se implementó.** `generar_video_maestro.py` ahora arma
`flags_chip_android` (`-c:v h264_mediacodec -b:v <bitrate>`) y, en Android,
lo intenta primero cuando `CONFIG["video"]["usar_chip_android"]` está en
`true`; si ese render falla (por el motivo que sea: modelo sin soporte,
driver raro), la misma tanda cae sola a `libx264` sin perder el video. La
clave vive en `config.json` bajo `"video"` (ver `config.example.json`) y por
omisión está en `false`: el comportamiento de hoy no cambia para nadie que
no toque nada. El panel (pestaña Ajustes → Más opciones → Render, solo visible cuando el
servidor detecta que corre en Android) trae un interruptor deslizante para
encenderlo o apagarlo sin tocar `config.json` a mano — pensado exactamente
para el caso de que el chip falle tan seguido que no valga la pena ni
intentarlo, tal como pidió el dueño del proyecto.

**Lo que falta, y por qué no se firma como "listo" sin ello.** Esta sesión
no tiene un teléfono Android para comprobar que `h264_mediacodec` produce
un video correcto con los filtros que ya se aplican (subtítulos quemados,
overlays, espacio de color). Esto importa más de lo que parece: el respaldo
a `libx264` solo salta si ffmpeg termina con error o deja un archivo vacío
(`archivo_valido`). El fallo típico de un chip de hardware mal soportado es
el contrario — termina bien, el archivo pesa lo normal, pero el video sale
con colores raros o fotogramas rotos. Ese caso el pipeline **no lo detecta
solo**: el interruptor de Ajustes es el único remedio, así que si un video
sale mal a la vista, apagarlo ahí mismo — no esperar a que el pipeline se dé
cuenta, porque no se va a dar cuenta.

El bitrate por omisión (`4M`, con `-maxrate`/`-bufsize` a juego) se eligió
para acercarse al peso que ya deja `-crf 23`, y no al `6M` que se probó
primero: `recomprimir.py` marca como "que pesan de más" a partir de 100 MB,
y con `duracion_max_short_sec` en 180 s un video a 6 Mbps constantes puede
pasarse de ese umbral él solo — lo que dispararía una recompresión por CPU
después de cada render con el chip, anulando la ganancia de velocidad y
batería que es todo el punto de usarlo. Con `4M` un short de 180 s pesa como
mucho ~90 MB, dentro del umbral. Aun así, el tamaño real hay que
confirmarlo con un render de verdad en el teléfono: la ruta de CPU no se
tocó, así que encender el interruptor es la única forma de que este código
corra; apagado, el comportamiento es idéntico al de antes de este cambio.

*(Nota agregada después: con la resolución adaptativa de `CLAUDE.md`
—"Resolución adaptativa: 2K si el fondo lo aguanta"— este bitrate ya no es
fijo, sube en la misma proporción que la resolución elegida para ese video.
El riesgo de pasarse de los 100 MB de `recomprimir.py` no es solo del chip
entonces: a "2K" también `-crf 23` por software pesa más que a 1080p, por
las mismas razones. Hoy ninguno de los dos casos se activa —todo el material
de fondo del proyecto nace en 1080p— pero el día que sí haya un fondo más
grande, vale la pena revisar el peso real de un short completo antes de
confiar en el umbral de siempre.)*

**Para probarlo:** encender el interruptor en Ajustes, dejar correr un
render normal, y comparar el resultado a ojo (colores, nitidez de los
subtítulos) y el tiempo/batería que tardó frente a un render por CPU. Si el
video sale mal o el chip falla en varios videos seguidos, apagar el
interruptor deja todo como estaba, sin tocar código.

## 4. Reintentos ante cuota de Gemini agotada — YA LO TENEMOS

**Qué se encontró.** Patrones genéricos de backoff exponencial y rotación
de varias claves de API para repartir la cuota gratuita entre cuentas.

**Decisión: no se adopta.** `script_writer.py` (`motivo_error_gemini`) ya
distingue en detalle los fallos propios de este proyecto — clave inválida,
API apagada en el proyecto de Google Cloud, clave restringida por
aplicación (el caso típico en Termux: una clave restringida a Android o a
un referrer web que Termux no cumple), cuota agotada — y decide con eso si
la historia vuelve intacta a la cola o si se le apunta un intento fallido.
Es más específico que cualquier backoff genérico, porque ya sabe leer los
mensajes de error concretos de la API de Gemini. Rotar varias claves
serviría para estirar la cuota gratuita, pero es una decisión de cuenta
(pedir una segunda clave, gestionar dos proyectos de Google Cloud), no de
código — se deja anotado por si el dueño del proyecto quiere una segunda
clave el día que la cuota se quede corta.

## 5. Transcripción de YouTube cuando falla `youtube-transcript-api` — YA IDENTIFICADO, no se toca hoy

**Qué se encontró.** `youtube-transcript-api` puede bloquearse desde IPs de
datacenter; `yt-dlp` es la alternativa más robusta para sacar subtítulos
(`--write-auto-sub --skip-download`), sin necesitar API key.

**Decisión: no se cambia `youtube_scout.py` hoy**, porque no está fallando
— sigue funcionando con `youtube-transcript-api` (ver
`YouTubeTranscriptApi`). Esto ya se había hablado en la sesión: si algún día
`youtube-transcript-api` empieza a fallar, `yt-dlp` es el plan B natural.
Queda instalado como herramienta suelta (`pkg install yt-dlp`) para usar a
mano mientras tanto, sin tocar el scout.

## 6. Buscar canales de YouTube por nombre desde el panel — SE ADOPTÓ (API ya usada en el proyecto)

**Qué se encontró.** No hace falta un repo ni una técnica nueva: la misma
YouTube Data API v3 que ya usa `videos_por_api` para `youtube_busquedas`
(`search().list(part="snippet", ...)`) tiene un modo `type="channel"` que
busca canales por texto en vez de videos. El nombre del canal (`customUrl`,
el `@handle` público) solo sale de `channels().list`, no de `search().list`,
así que hacen falta las dos llamadas — mismo patrón de dos pasos que
`_detalles_de_videos` ya usa para las vistas.

**Por qué importa aquí.** Agregar un canal a mano (Ajustes → Más opciones → Fuentes, ver
sesión de "canales/subreddits manuales") pedía copiar el `@handle` exacto
desde el navegador — fácil de escribir mal desde el teclado del teléfono.
Buscar por nombre y elegir de una lista quita ese paso.

**Corre en Termux.** Sí — mismo cliente (`googleapiclient`) que ya usa el
resto del archivo, cero dependencias nuevas.

**Costo de mantenimiento.** Ninguno adicional. Eso sí, **requiere
`YOUTUBE_API_KEY`** (igual que `youtube_busquedas`): sin clave, el botón de
buscar no aparece en el panel y solo queda pegar el `@handle`/URL a mano,
que es la vía que ya existía y la que de verdad usa este proyecto la
mayoría del tiempo (ver la nota de `--diagnostico` sobre "sin clave solo se
ve el RSS").

**Licencia.** No aplica — es la misma API de Google que ya está en uso,
dentro de la cuota gratuita ya contemplada.

## 7. Convertir el panel en una app de Android — SE ADOPTÓ una técnica, se descartaron cuatro proyectos

**El problema, dicho con precisión.** El panel ya se podía "agregar a
pantalla de inicio" desde Chrome. Lo que faltaba no era el icono: era que
tocar ese icono sirviera de algo con el servidor apagado. Y sobre todo,
**arrancar `servidor.py`**, que es lo único que ninguna página web puede
hacer — ningún navegador deja que una página lance un proceso, y es lo que
impide que cualquier web ejecute cosas en tu teléfono.

### Lo que se adoptó

**El intent `RUN_COMMAND` de Termux**
([wiki oficial de termux-app](https://github.com/termux/termux-app/wiki/RUN_COMMAND-Intent)).
No es un repo ni una librería: es la puerta que Termux expone a propósito
para que otra app le encargue un comando. Se le manda un intent a
`com.termux/com.termux.app.RunCommandService` con la ruta del ejecutable y
`RUN_COMMAND_BACKGROUND`, y Termux lo corre.

- **Corre en Termux sin root.** Es literalmente Termux quien lo ejecuta.
  Pide dos cosas y las dos son del usuario, no del código: el permiso
  `com.termux.permission.RUN_COMMAND` (de los que Android pregunta en
  caliente) y `allow-external-apps=true` en `~/.termux/termux.properties`.
  Esa línea vale para **cualquier** app con ese permiso, así que
  `instalar_panel.sh` no la pone sola: hace falta `--app`.
- **Mantenimiento.** Unas 40 líneas de Java en `MainActivity.pedirArranque`.
  Lo único que puede cambiar bajo los pies es el nombre del servicio de
  Termux, que lleva años igual.
- **Licencia.** No aplica: es la API pública de Termux (la app es GPLv3, pero
  aquí no se usa ni una línea de su código — se le habla por intent).

**Segundo requisito, y este no es de Android sino de Chrome:** una PWA solo
es instalable de verdad si hay manifiesto **y** un service worker con
manejador de `fetch`. Había manifiesto y no service worker, así que "agregar
a pantalla de inicio" dejaba un marcador con barra de navegador. `127.0.0.1`
cuenta como contexto seguro, así que no hace falta HTTPS —
[MDN](https://developer.mozilla.org/en-US/docs/Web/Progressive_web_apps/Guides/Making_PWAs_installable).
Implementado en `web/sw.js`.

### Lo que se descartó, y por qué

**PWABuilder / Bubblewrap (Google, Apache-2.0).** Es *la* forma oficial de
empaquetar una PWA como APK, con una TWA (Trusted Web Activity). **No sirve
aquí, y no por peso:** una TWA exige que el sitio esté en HTTPS y verifique
su dominio con Digital Asset Links. `http://127.0.0.1:8770` no tiene dominio
ni puede tener certificado, así que la verificación no existe. Aparte,
Bubblewrap necesita Node y el SDK de Android, que en el teléfono no hay.

**Plantillas de WebView con build en GitHub Actions** — `ASTRALLIBERTAD/web-app`,
`gandil123/Android-WebviewWebapp-Template`, `GothwadTech/webview`. De aquí
salió **la idea** (WebView + compilación en el runner, que es exactamente lo
que hace `.github/workflows/apk.yml`), pero no el código: todas parten de que
el frontend va *dentro* del APK, en `assets/`, y aquí el panel tiene que
servirlo Python porque es una API viva, no HTML suelto. Y ninguna sabe nada
de Termux, que es la única parte que costaba. Adoptar una plantilla habría
sido adaptarla más de lo que costó escribir las ~300 líneas de
`MainActivity.java`.

**`shiaho777/web-to-app` (Unlicense).** El más tentador de los cuatro,
porque compila APKs **en el propio teléfono**, sin PC ni SDK ni Node — que
es la restricción de este proyecto. Se descarta por arquitectura, no por
capricho: su modo "server-based app" mete el runtime y el servidor *dentro*
del APK, en el sandbox de la app. Aquí eso significaría una segunda copia de
Python y del pipeline entero, separada de la de Termux — que es la que tiene
`pipeline_state/`, los cron de `instalar_cron.sh` y los secretos. Dos copias
del proyecto en el mismo teléfono es peor problema que el que resuelve.
Además nada de lo que se hace ahí queda en el repo: el APK saldría de tocar
una app a mano, sin nada que revisar en un diff.

**`termux/termux-gui` (GPL-3.0).** Deja que un programa de Termux dibuje
widgets nativos de Android desde Python. Descartado sin dudarlo: obligaría a
reescribir el panel entero —150 KB de HTML que ya funciona— en otro
paradigma, y sumaría otro APK-plugin que instalar. Se gana una interfaz
nativa que nadie ha pedido y se pierde la que hay.

**`termux/termux-widget` — ya estaba y se queda.** `instalar_panel.sh` ya
crea el atajo "🎬 Panel de videos". No compite con el APK: el widget arranca
el servidor y abre Chrome; la app hace las dos cosas en una. Quien no quiera
instalar un APK sigue teniendo el widget, y quien lo instale puede tener los
dos — la app entra directa si el puerto ya contesta, lo arrancara quien lo
arrancara.

**Lo que no se puede comprobar desde aquí.** Que el APK compila lo dice el
workflow en cada push, y se compiló antes de subirlo (SDK 35, AGP 8.7.3).
Que Termux acepte el intent en un teléfono de verdad solo se ve instalándolo
— es la misma trampa que `VERSION_CLI` de HyperFrames: sin Android delante,
subir la versión es apostar.

## 8. Revisión de septiembre de 2026 — una idea que vale la pena, una a medias, una descartada

Tres cosas salieron del uso real esta semana: una historia de 937 palabras
que el log del teléfono marca como `Video 1 aplazado: ~6.0 min estimados`
porque los largos siguen bloqueados (93 de 500 suscriptores, ver
`formato.py`); una advertencia de YouTube por "seguridad infantil" causada
por un clip de fondo de stock, no por la narración; y la duda de si la
música debería bajar sola bajo la voz.

### Partir historias largas en partes — SE ADOPTÓ LA IDEA, NO EL CÓDIGO

**Qué se encontró.** `TerzicScript/shorts-flow` parte una historia en
"Parte 1, Parte 2…" a partir de una duración objetivo;
`Subset28/shorts-pipeline` tiene un `split --parts N`. Los dos parten el
**video ya renderizado**, ninguno documenta cómo elige el punto de corte, y
ninguno tiene archivo de licencia — así que su código no se puede tomar
aunque se quisiera.

**Corre en Termux.** `shorts-flow` no: depende de Kokoro y faster-whisper,
que es torch. `shorts-pipeline` recomienda Docker. La idea, en cambio, no
necesita nada nuevo.

**Cómo encajaría aquí.** Partiendo el **texto antes del TTS**, no el video
después: cortar un video a mitad de frase es peor que elegir el corte en el
guion. Gemini ya hace algo parecido en `script_writer.py`
(`segmentar_transcripcion`), pero con otro contrato: aquella separa
anécdotas *distintas* de un podcast, esto partiría *una* historia en tramos
seguidos terminados en suspenso. Se copiaría el patrón (prompt + esquema
JSON), no la función.

**Por qué vale la pena.** Los números del propio canal, en `formato.py`:
48 shorts sumaron 29.000 vistas y 18 largos, 43. Hoy una historia larga se
queda en `guion.txt` sin producir nada hasta los 500 suscriptores; partida
en tres, saldría ya en el formato que funciona.

**La restricción que decide el diseño.** El nombre del archivo sale del
título (`n_arch`, recortado a 120 caracteres) y `limpiar_cola.py` empareja
por ese nombre sin el número delante. Si las partes solo se distinguen por
un "(Parte 2 de 3)" al final de un título largo, el recorte se lo come, las
tres partes quedan con el mismo apodo, y al grabarse la primera la limpieza
borraría las otras dos de la cola. La marca de parte tiene que ir donde el
recorte no llegue — delante del título, o recortando antes de añadirla.

**Cómo quedó.** El dueño del proyecto eligió hacerlo, y vive en
`partir_historias.py`:

- **Gemini no reescribe, solo elige dónde cortar.** Recibe las frases
  numeradas con las palabras acumuladas y devuelve los números de frase
  donde termina cada parte, buscando el suspenso. Si falla, no hay clave o
  los cortes no cumplen los límites, se corta a partes iguales. Lo que se
  publica es palabra por palabra lo que ya estaba escrito.
- **Cuándo se parte.** Cuando el cuerpo pasa de lo que cabe en un short
  (`DURACION_MAX_SHORT_SEC` × `PALABRAS_POR_SEGUNDO`, 468 palabras con 180 s)
  y los largos están bloqueados. Cada parte llena como mucho el 80%, para
  el título leído y el error de la estimación. Más de cuatro partes ya no
  se parte: espera a los largos.
- **La marca de parte va delante del título** ("Parte 2 de 3 — …"), por la
  restricción de arriba, y `publisher.py` pone "(Parte 2/3)" en el título
  de YouTube por su cuenta: el publicador no sube un video cuyo título ya
  está en el canal, y Gemini escribe títulos parecidos para las partes de
  una misma historia.
- **Solo si el dueño lo decide.** Por omisión nada se parte solo: cada
  historia larga tiene su botón «✂ Partir» en la Cola. El modo automático
  (`partir_automatico`, apagado por omisión) hace que además
  `script_writer.py` parta las nuevas al escribirlas (se añaden al final de
  `guion.txt`, no renumeran nada) y que la tanda de mantenimiento parta las
  de la cola. Partir las que ya estaban en la cola sí
  renumera: si detrás hay historias ya grabadas, el render las volvería a
  grabar, porque reconoce lo hecho por "NN_Titulo.mp4". Por eso esa pasada
  quita antes lo ya grabado, igual que `limpiar_cola.py`, y en la tanda de
  mantenimiento va justo detrás de la limpieza.
- **El orden de publicación no salía solo.** El publicador sube por número
  de historia, y ese número es la posición en `guion.txt`, que cambia con
  cada limpieza: si la parte 1 se graba hoy y una limpieza renumera antes
  de grabar las otras dos, esas pueden quedar con número menor y subirse
  antes. `publisher.en_orden_de_serie` reordena cada serie dentro de los
  huecos que ya ocupaba. Lo que no se controla es `max_subidas_por_corrida`:
  con una subida al día, una serie de tres tarda tres días en salir entera.
- **`relanzar.py` deja las partes en paz.** La revisión quincenal borra
  los videos sin vistas y devuelve la historia a la cola; con una parte,
  la 2 volvería a subirse sola después de la 3.

### Filtrar el material de stock por lo que dice su ficha — VIABLE, a medias

**Qué se encontró.** Los servicios de detección de menores en imagen
(Sightengine y parecidos) son APIs de pago en la nube, y las herramientas
libres de detección de caras (OpenScrub y similares) piden GPU. Nada de eso
cabe aquí.

**Lo que sí hay, sin coste.** Las dos fuentes describen cada clip en texto:
Pixabay devuelve un campo `tags` ("girl, driving, truck"), y en la
respuesta de búsqueda de Pexels la `url` de la página lleva un slug
descriptivo (`https://www.pexels.com/video/video-of-forest-1448735/`); su
campo `tags` viene vacío. Ese slug ya se guarda hoy, como `pagina`, en
`pipeline_state/fondos_atribucion.json`. Rechazar al buscar los clips cuya
ficha nombra personas (child, kid, girl, boy, baby, teen, woman, man,
people, driver…) son unas veinte líneas en `descargar_fondos.py`, sin
dependencias, y el mismo filtro pasado sobre `fondos_atribucion.json`
señalaría los clips ya descargados que habría que borrar del teléfono.

**Por qué "a medias".** Es un filtro por palabras sobre lo que escribió
quien subió el clip, no una revisión de la imagen: un clip titulado
"woman driving" puede mostrar a una menor, y uno sin personas en el título
puede tenerlas. Reduce el riesgo, no lo elimina. Para las fichas antiguas
de Pixabay, `fondos_atribucion.json` no guardó los `tags`, así que la
revisión retroactiva solo cubre bien lo que vino de Pexels.

**Lo que ya se hizo.** El PR que quitó del tema `carretera` las búsquedas
que devolvían gente al volante ataca la causa directa; esto sería la red
por debajo, para cualquier tema.

**Licencia.** No aplica — son las mismas APIs que ya se usan, dentro de sus
términos.

### Bajar la música bajo la voz con `sidechaincompress` — DESCARTADO

**Qué se encontró.** Es un filtro que trae ffmpeg de serie, así que
correría en Termux, y varios proyectos lo usan (umbral ≈0.02–0.05, ratio
8–10, ataque 50 ms, liberación 400–500 ms), siempre con `asplit=2` sobre la
voz para usarla a la vez de llave y de mezcla.

**Por qué no.** Aquí no hay nada que bajar: la música va a `volumen_musica`
0.04 contra la locución a 0.5, con `normalize=0` — diez veces por debajo
todo el rato. El comentario de `generar_video_maestro.py` junto al `amix`
explica por qué esos niveles son los que son. Añadiría una etapa de filtro
y un `asplit` a cada render en la CPU del teléfono para una diferencia que
no se oye.

## 9. Conectar servicios desde el panel — NO SE ADOPTÓ NADA, se escribió

**El problema.** Poner una clave era saberse el nombre exacto de la
variable, buscar por tu cuenta la página donde se saca, pegarla a ciegas y
enterarse en el siguiente render de que no servía.

**Qué se encontró.** `jthop/flask-api-key` y parecidos resuelven otro
problema: autenticar a quien llama a *tu* API. `akdinesh2003/API-Key-Validator`
prueba claves de otros servicios (OpenAI, AWS…) en una aplicación aparte.
Ninguno encaja en un panel que ya existe y tiene que leerse a 412 px.

**Qué se hizo.** `conectar.py`, ~200 líneas sin dependencias nuevas
(`requests` ya estaba): un catálogo con para qué sirve cada clave, su enlace
y sus pasos, y una prueba real contra cada servicio con la llamada más barata
que tiene. Reutiliza `secretos.revisar_clave_api` para reconocer las
credenciales equivocadas (un client secret de OAuth pegado como clave, por
ejemplo). El panel lo enseña en Ajustes → Conectar servicios. Corre en
Termux sin nada más.

## 10. Horario de las tandas desde el panel — SE ADOPTÓ UNA IDEA, NO EL CÓDIGO

**El problema.** Las cinco tareas de cron estaban escritas a mano dentro de
`instalar_cron.sh`. Cambiar "publicar a las 9" por "publicar a las 10" era
editar un script de bash desde el teclado del teléfono, y un error de
sintaxis en una línea de crontab no avisa: cron la ignora y la tanda
simplemente deja de salir.

**Qué se encontró.**

- [`alseambusher/crontab-ui`](https://github.com/alseambusher/crontab-ui)
  (MIT): el más completo, con copias de seguridad y registro de cada tarea.
  Es Node, así que queda fuera en Termux.
- [`fluxkompensator/CronUI`](https://github.com/fluxkompensator/CronUI)
  (Flask): lista, edita y crea tareas cualesquiera. Pide correr como root y
  es un servidor aparte en el puerto 5000: otro proceso que Android puede
  matar, y otra página que no es el panel.
- [`benjcabalona1029/python-crontab-ui`](https://github.com/benjcabalona1029/python-crontab-ui):
  lo mismo sobre FastAPI y `python-crontab`. Otro servidor, otra dependencia.
- [`doctormo/python-crontab`](https://github.com/doctormo/python-crontab)
  (LGPL-3.0): la librería para leer y escribir el crontab. Para cinco líneas
  con una marca al final basta `crontab -l` / `crontab -`, que es lo que ya
  hacía `instalar_cron.sh`.

Además, todos dejan escribir **cualquier orden** en el crontab desde una
página web. Aquí eso sobra y es peligroso: el panel escucha en el teléfono
y lo que se meta en el crontab corre solo, sin que nadie mire.

**Qué se adoptó.** La idea de pausar una tarea sin borrarla, que tienen
crontab-ui y CronUI. Nada de su código.

**Qué se hizo.** `horario.py`, sin dependencias nuevas. Los comandos son
fijos en `TAREAS`; desde el panel solo se cambian días, hora y si la tarea
está activa, y todo pasa por `validar` antes de escribirse. Los días del
mes van del 1 al 28, porque un 30 o un 31 se saltaría en silencio los meses
que no lo tienen. Lo de fábrica genera exactamente las líneas que ya
escribía `instalar_cron.sh` (los siete días como `*`, el `python` sin
resolver), así que un teléfono con el cron puesto por la versión anterior
cuenta como al día y no ve un aviso falso. `instalar_cron.sh` ahora llama a
`horario.py --aplicar` en vez de llevar las líneas dentro. En el panel:
Ajustes → Horario automático.

## 11. Quitar Termux de en medio: que la app corra el pipeline ella sola — SE ADOPTÓ Chaquopy, y ffmpeg hay que compilarlo

**Qué se quería.** La app del §7 no arranca nada por su cuenta: le encarga
`servidor.py` a Termux con el intent `RUN_COMMAND`. Eso obliga a tener Termux
instalado, con `allow-external-apps=true` y el repo clonado dentro. La
pregunta era si la app puede llevar el pipeline entero.

Son dos problemas distintos y se resolvieron distinto: **el intérprete de
Python** y **ffmpeg**.

### 11.1 Python dentro del APK — SE ADOPTÓ `chaquo/chaquopy`

**Qué es.** Un plugin de Gradle que mete CPython y sus dependencias dentro
del APK, y deja llamar a Python desde Java y al revés. Versión 17.0, Python
3.13 hasta 3.8, `minSdk` 24, arm64-v8a y x86_64.

**Licencia: MIT.** Esto es nuevo y cambia la decisión: hasta la 12.0.1
Chaquopy era de pago para apps de código cerrado. Desde entonces, y gracias
al patrocinio de Anaconda, es libre sin restricciones.

**Corre en Android sin root.** Es su único objetivo. Y lo que importa aquí:
las dependencias con código nativo del proyecto tienen rueda de Android en
el repositorio de Chaquopy — se comprobó que están **pillow** y
**cryptography** (y de paso numpy y lxml). El resto de `requirements.txt`
—flask, requests, google-genai, edge-tts, google-api-python-client,
youtube-transcript-api— es Python puro y sale de PyPI.

`subprocess` **no** está en su lista de módulos no soportados (sí lo están
`crypt`, `grp`, `curses`, `tkinter`… y `multiprocessing`, que aquí no se
usa). Eso es lo que permite que las llamadas a ffmpeg sigan siendo las
mismas.

**Coste de mantenimiento.** Un plugin más en el Gradle y las dependencias
declaradas dos veces (en `requirements.txt` y en el bloque `pip`). A cambio,
cero cambios en los `.py`: ver más abajo.

**Se miraron las alternativas y se descartaron por forma, no por calidad:**

- **`python-for-android`** (Kivy, MIT) tiene un *bootstrap* de WebView
  pensado exactamente para esto: un servidor Python local y una WebView
  apuntándole. Si este proyecto empezara hoy sería un candidato de primera.
  Se descarta porque **ya hay una app Android nativa** (módulo `:app`, con
  su Activity, su servicio y su WebView) compilándose con Gradle en el
  runner: p4a traería su propia cadena de construcción (buildozer/p4a) y
  habría que rehacer lo que ya funciona.
- **BeeWare / Briefcase** (BSD). Se descarta por el mismo motivo —está
  pensado para proyectos que nacen dentro de él— y por un dato que además
  refuerza la elección de arriba: **Briefcase usa Chaquopy** para Android
  desde la 0.3.10, en lugar de su antiguo `Python-Android-support`.
- **Pyodide / Python en WebAssembly.** Correría dentro de la WebView sin
  tocar el APK, pero no tiene `subprocess` ni sistema de archivos real, así
  que no puede llamar a ffmpeg ni escribir los videos. No sirve para esto.
- **UserLAnd o `proot-distro` dentro de la app.** Es cambiar Termux por otro
  Linux emulado: el mismo problema con otro nombre, y más peso.

### 11.2 ffmpeg — NO HAY NADA QUE SIRVA, HAY QUE COMPILARLO

El pipeline llama a `ffmpeg` y `ffprobe` **31 veces**. Sin ellos no hay
video.

**`arthenica/ffmpeg-kit` está retirado.** Era la respuesta obvia —traía un
paquete `full-gpl` con libass y x264— pero su autor lo archivó y **el 1 de
abril de 2025 retiró los binarios de Maven Central, CocoaPods y npm**. En
julio de 2026 apareció **FFmpegKitNext**, del mismo autor, como continuación
oficial, pero se distribuye como código fuente, no como binarios listos. Hay
además forks de la comunidad (`SixtyTwoPlus/ffmpeg-kit`,
`moizhassankh/ffmpeg-kit-android-16KB`).

**Y aunque no lo estuviera, ffmpeg-kit es una librería, no un ejecutable.**
Usarla significaría reescribir las 31 llamadas para que pasen por JNI, y
mantener ese camino **solo para Android** — mientras Termux y el runner de
GitHub siguen usando `subprocess`. Los mismos `.py` corren en los tres
sitios; partirlos en dos es exactamente como se pudre este tipo de código.

**Binarios ya compilados: ninguno sirve.**

- `Khang-NT/ffmpeg-binary-android` (LGPL) sí da **ejecutables** y arm64,
  pero sin libass, sin fontconfig y sin freetype, sobre una base de ffmpeg
  3.3.2 (2017). **libass no es opcional aquí**: es quien dibuja los
  subtítulos karaoke que arma `convertir_timing_a_karaoke_ass`.
- `cropsly/ffmpeg-android` sí trae x264, libass, fontconfig, freetype y
  fribidi —el juego exacto— pero está congelado en "Android 4.1+".
- `guardianproject/android-ffmpeg`, `cmeng-git/ffmpeg-android`: la técnica
  está, los binarios son viejos.

**Conclusión: existe la técnica, no existe el artefacto.** Se escribió
`android/nativa/ffmpeg/construir.sh`, que compila ffmpeg y ffprobe para
arm64 con x264, libass, freetype, fribidi y harfbuzz, y un workflow que lo
hace en el runner (compilar esto en el teléfono no es posible).

**Licencias.** Con x264 el binario es **GPL**, y por eso el script pasa
`--enable-gpl --enable-version3`. Para una app personal que se instala a
mano da igual; si algún día se distribuyera habría que publicar el código
—que ya está publicado— y la receta de compilación, que es ese script.

### 11.3 Lo que se descubrió por el camino: el chip de video se pierde

**`h264_mediacodec` no funciona en un ffmpeg ejecutable.** El soporte de
mediacodec en ffmpeg pasa por JNI y necesita una JVM viva
(`av_jni_set_java_vm`); un binario suelto no tiene ninguna. Es el motivo de
fondo del issue #73 de `mobile-ffmpeg`, y no tiene arreglo mientras ffmpeg
sea un ejecutable.

No rompe nada —`usar_chip_android` ya viene en `false` y el pipeline cae
solo a libx264 (ver `CLAUDE.md`)— pero el coste es real: renderizar por CPU
es más lento y gasta más batería. Es, de hecho, **lo único que la app con
Termux hace mejor** que la app nativa.

Recuperarlo pide llamar a ffmpeg como librería desde el proceso de la app,
que es donde sí hay JVM. Con Chaquopy se puede llamar a Java desde Python,
así que el camino existe — pero es el refactor de las 31 llamadas que este
apartado acaba de descartar para el caso general.

### 11.4 Los dos muros que solo aparecen al construirlo (uno ya cayó)

Ninguno de los dos salió de leer documentación: salieron de compilar. El
segundo ya está resuelto —y resolverlo mejoró el proyecto entero, no solo
Android—; el primero sigue en pie y es el que decide si esta app llega a
sustituir a la de Termux.

**1. El panel lanza un proceso de Python por cada botón, y en la app no hay
ninguno.** `servidor.py` tiene una lista blanca de acciones (`ACCIONES`) y
cada una es `[sys.executable, "trend_scout.py"]` — un proceso nuevo, con su
salida en vivo, que se puede cancelar. Dentro de Chaquopy **`sys.executable`
está vacío**: CPython va como librería (`libpython3.12.so`), no como
ejecutable, y no hay forma de lanzar un proceso de Python. Es el issue #96
de chaquopy.

O sea: la app puede servir el panel y enseñar el estado, pero **ningún botón
que lance una etapa funciona** tal y como está escrito hoy.

La salida es correr las etapas **dentro del proceso**, importando el módulo
en un hilo en vez de lanzarlo: `Trabajo` pasaría de `subprocess.Popen` a
`runpy` con la salida redirigida. Lo que se pierde es la cancelación real
—matar un proceso es fiable, parar un hilo no— y la pausa. No es un cambio
pequeño y toca el corazón de `servidor.py`, que es el mismo que usa Termux,
así que no se ha hecho aquí.

Y el mismo muro alcanza a algo que acaba de entrar en `main`: `horario.py`
escribe el crontab con órdenes `"{py} buscar_diario.py"`. Sin ejecutable de
Python no hay `{py}` que valga, y además en Android no hay cron. Las tandas
automáticas de la app tendrían que ir por `WorkManager`, que es otra pieza
que no existe todavía.

**2. Gemini no se podía instalar en el APK — RESUELTO, y se escribió.**

`google-genai` exige `pydantic >= 2.12`, que depende de `pydantic-core`: una
extensión compilada en **Rust**, sin rueda de Android y ausente del
repositorio de Chaquopy.

El síntoma, además, no decía nada: pip se ponía a **retroceder** versión por
versión buscando una combinación que no pidiera pydantic —que no existe,
porque google-genai siempre la ha usado— y la compilación se quedaba media
hora bajando metadatos de `urllib3` y de `idna` sin un solo mensaje de
error. Aquí se dejó correr 22 minutos antes de entender qué pasaba.

Afectaba a lo más importante del pipeline: `script_writer.py` escribe
**todos** los guiones con Gemini, y `calidad_ia.py`, `publisher.py`,
`partir_historias.py`, `rehacer_guiones.py`, `preparar_metadata.py` y
`hyperframes_broll.py` también lo usan.

**Qué se hizo: `gemini.py`, un cliente REST sobre `requests`.** Y la
decisión que lo hizo barato: **imita los nombres del SDK** —`Client`,
`types.GenerateContentConfig`, `types.Part`, `errors.APIError`— así que de
los ocho archivos solo cambió la línea del `import`. Las llamadas, que son
lo que hay que leer para entender el pipeline, se quedaron como estaban.

Se miró primero si había algo hecho: no hace falta. La superficie que este
repositorio usa del SDK resultó ser diminuta —generar contenido con
instrucción de sistema y respuesta JSON con esquema, más subir, consultar y
borrar un archivo— y son 300 líneas contra una dependencia que arrastra
Rust compilado. La alternativa era compilar una rueda de `pydantic-core`
para Android con las recetas de `chaquopy/server/pypi`: cross-compilar Rust
y mantener esa rueda cada vez que pydantic suba de versión, para no ganar
nada.

**Lo que costó no estaba a la vista, y son las dos cosas que se probaron
con un servidor de mentira antes de tocar nada más:**

- **El SDK normalizaba los esquemas y el REST no.** Los `SCHEMA_*` del repo
  están escritos en minúsculas (`"type": "object"`), y la API los quiere en
  mayúsculas. Sin convertirlos, Google responde 400 sin decir qué campo le
  molesta.
- **Todo el manejo de errores del proyecto lee el *texto* de la excepción.**
  `motivo_error_gemini` busca `API_KEY_INVALID`, `SERVICE_DISABLED` o
  `PERMISSION_DENIED`; `hyperframes_broll` busca `RESOURCE_EXHAUSTED` y saca
  el `retryDelay` con un regex. Por eso `APIError.__str__` devuelve el
  código HTTP y el cuerpo JSON crudo: esas cadenas vienen dentro y ese
  código sigue funcionando sin tocarlo.

**Se gana también fuera de Android.** `google-genai` y `pydantic` salen de
`requirements.txt`, así que Termux y el runner instalan menos y arrancan
antes. `requests` ya estaba.

### 11.5 El precio que hay que decir en voz alta

**Se deja de actualizar con `git pull`.** Hoy el dueño del proyecto se mete
en Termux, hace `git pull` y ya está corriendo lo nuevo. Con el pipeline
dentro del APK, cada cambio de una línea de Python exige compilar un APK en
el runner, bajarlo al teléfono e instalarlo. Para un proyecto que se toca
casi a diario desde el propio móvil, eso no es un detalle: es el cambio más
grande de los tres.

Por eso la app nativa se añade **al lado** de la de Termux y no en su lugar.
Son dos formas de usar el mismo repo, y hoy la que se actualiza en diez
segundos sigue siendo la de Termux.

## 12. Diccionario de errores en el panel — NO SE ADOPTÓ NADA, se escribió

**El problema.** Lo que falla sale en el panel tal cual lo escupe la
librería: un `429` con el JSON de Google, un `invalid_grant`, un
`Errno 28`. Desde el teléfono no se sabe qué es ni en qué pantalla se
arregla.

**Qué se encontró.** `aroberge/friendly-traceback` (MIT) explica en lenguaje
llano las excepciones de Python, pensado para quien aprende a programar:
dice qué es un `KeyError`, no que la cuota de Gemini se renueva a diario ni
que el token de YouTube caduca si la app sigue en «Prueba». Lo que hace
falta aquí son los fallos de *estos* servicios, y eso no lo trae nadie.
Buscar "error message explainer" en GitHub solo devuelve librerías de
expresiones regulares.

**Qué se hizo.** `errores.py`, sin dependencias: una lista de entradas con
los patrones que delatan cada fallo en la salida y tres frases (qué es, qué
pasa, qué hacer), de lo concreto a lo genérico. El servidor la aplica a la
salida de cada trabajo y el panel la enseña debajo de la tarjeta y en «Ya
terminados»; «📖 Errores», en la Cola, abre el diccionario entero con
buscador. Una entrada nueva es añadir un dict a `CATALOGO`.

## 13. Qué hacer cuando Gemini agota la cuota del día

**El problema.** Con la cuota del día gastada, la escritura de guiones se
corta y la cadena buscar → guiones → grabar se queda a medias hasta el día
siguiente.

### Lo que está puesto: los otros modelos gratis de Gemini — SE ESCRIBIÓ

Google cuenta la cuota **por proyecto y por modelo**, así que cada modelo
del plan gratis trae la suya. `ClienteConRespaldo`, en `script_writer.py`,
se hace pasar por el cliente de Gemini: si `MODEL` contesta cuota agotada,
pasa al siguiente de `MODELOS_RESPALDO` y sigue con él el resto de la
corrida. El orden: primero los Flash (3.8, 3.7, 3.5; unas 20 peticiones al
día cada uno según lo que publica AI Studio en septiembre de 2026, texto
mejor) y al final los Flash-Lite (3.5 y 3.1; unas 500 al día, texto más
llano). Un modelo que no exista en la cuenta —Google los retira— contesta
404 y se salta, así que la lista puede quedarse vieja sin romper nada.

Al día siguiente se vuelve solo a `MODEL`, porque no se guarda nada. Si se
agotan todos, la corrida se corta como siempre y la cola queda intacta.
Gratis y se renueva cada día: por eso ganó a lo de abajo.

Las cifras no salen de la documentación de Google —que remite al panel de
AI Studio, porque varían por cuenta— sino de guías de terceros que las
copian de ahí. Las de cada uno se ven en aistudio.google.com/rate-limit.

### La idea aparcada: DeepSeek con su crédito gratis — NO ACTIVO

Se llegó a escribir y se quitó a petición del dueño del proyecto; el código
está entero en el commit `4dfd80b` (`deepseek.py`), por si algún día hace falta.

- **Qué hacía.** Mismo truco de hacerse pasar por el cliente de Gemini, pero
  saltando a DeepSeek (`deepseek-flash`, API compatible con la de OpenAI, en
  `https://api.deepseek.com`). Antes de cada llamada preguntaba el saldo
  (`GET /user/balance`, que no gasta) y **solo seguía si quedaba
  `granted_balance`**, para no tocar nunca saldo pagado. El esquema JSON se
  describía en el prompt, porque DeepSeek no lo acepta como parámetro; su
  modo `response_format: json_object` exige la palabra "json" en el prompt.
- **Por qué se aparcó.** El regalo de DeepSeek (5 M de tokens, unos 8 $) es
  **de una sola vez y caduca a los ~30 días** de crear la cuenta; no se
  renueva. Flash-lite cubre el mismo hueco gratis y todos los días.
- **Si se retoma.** Con `requests`, no con el SDK de OpenAI: arrastra
  `pydantic-core`, Rust sin rueda de Android (§11.4). Kimi (Moonshot) se
  miró y tampoco tiene nivel gratis permanente; además cuesta bastante más.

## 14. Tramos de los fondos que no se usan — NO SE ADOPTÓ NADA, se escribió

**El problema.** El render corta trozos de 6-12 s de cualquier fondo, desde
un punto al azar. Si un fondo tiene una escena que no conviene —el aviso de
"seguridad infantil" del canal salió de un clip de fondo—, la única salida
era borrar el archivo entero.

**Qué se encontró.** `Denperidge/Youtube-Clipgen` saca trozos al azar sin
repetir metraje, pero no deja vetar tramos; `coderefinery/ffmpeg-editlist`
y `slhck/ffmpeg-black-split` cortan con listas fijas o por negro, no eligen
al azar dentro de lo permitido. Ninguno encaja en `crear_fondo_multi_corte`.

**Qué se hizo.** `fondos_excluidos.py`: por archivo, una lista de tramos
fuera y un "no usar entero", en `pipeline_state/fondos_excluidos.json`. El
render elige el punto de inicio solo dentro de los huecos libres (pesados
por lo que ofrece cada uno), acorta el corte si el hueco más grande no llega
y salta el fondo si no le queda ninguno. En el panel, Ajustes → Música y
fondos → «✂ Tramos que no se usan» abre cada fondo en un reproductor con
«Marcar inicio» / «Marcar fin»; va en una hoja aparte del repintado para
que el video no se reinicie mientras se marca. `/fondo/<archivo>` solo sirve
archivos de la lista de fondos.

## 15. Grabar solo con batería o cargador — NO SE ADOPTÓ NADA, se escribió

**El problema.** Lo que se graba solo (la cadena del panel, el cron) arranca
aunque el teléfono esté al 30 % y desenchufado, y un render largo lo deja
seco.

**Qué se encontró.** Todo lo que hay en GitHub alrededor de Termux y la
batería son envoltorios de `termux-battery-status` para mirarla:
`cobrasanjay1/Termux_Battery_Status` (avisa por voz al 80 %),
`NathanielJS1541/Termux-Battery-Monitor` (registra la carga),
`usmannasution80/termux-charging-monitor`, `BuriXon-code/Termux-Battery` (la
pinta bonita en la terminal). Ninguno decide si algo puede correr ni espera
al cargador para arrancarlo; depender de uno sería traer un script entero
para usar dos campos del JSON (`percentage`, `plugged`).

**Qué se hizo.** `bateria.py` lee esos dos campos (con timeout: sin la app
Termux:API la orden se queda colgada) y decide. Si toca esperar, lo apunta
en `pipeline_state/grabar_al_cargar.json`; el panel enseña la espera en la
Cola y un hilo del servidor (`vigilar_cargador`) mira la batería cada
minuto, **solo mientras haya espera** —despertar Termux:API sin motivo
también gasta—, y graba en cuanto se conecta el cargador. Si la batería no
se puede leer, no se frena nada.

## 16. Subidas que se cortan — SE COPIÓ LA IDEA de la muestra oficial

**El problema.** Con la conexión del teléfono, una subida de 30 MB se corta
a veces a mitad. Antes eso contaba como fallo y la corrida siguiente volvía
a mandar el archivo entero; y si YouTube lo recibía roto, se quedaba así en
el canal.

**Qué se encontró.** La muestra oficial `youtube/api-samples`
(`python/upload_video.py`) reintenta `next_chunk()` hasta 10 veces, con
espera aleatoria creciente, ante errores de red y 500/502/503/504. Es la
forma que Google documenta, y es la idea que se copió; el código no, porque
es Python 2 (`except X, e`, `httplib`), el repo está archivado y no tiene
archivo de licencia en la raíz. No guarda la sesión entre corridas ni mira si
el video llegó bien. `tokland/youtube-upload` (GPL-3, último push en abril de
2024, 114 issues abiertos) es una CLI entera para subir. Depender de ella
sería traer una herramienta completa por un bucle de reintentos, y con GPL.

**Qué se hizo.** `publisher.subir_video` reintenta con la misma idea, y
además guarda la URL de la sesión en `pipeline_state/subida_en_curso.json`
para retomarla en la corrida siguiente si el proceso muere. Para eso
usa `request._in_error_state = True`, un atributo privado de
`googleapiclient`: hace que la librería pregunte primero cuánto llegó
(`bytes */total`). Si una versión nueva de la librería lo cambiara, lo
peor que pasa es que se vuelve a subir de cero. Después, `verificar_subida`
mira `uploadStatus`/`processingDetails` y `subir_y_verificar` borra y
vuelve a subir lo que YouTube dio por `failed`.

