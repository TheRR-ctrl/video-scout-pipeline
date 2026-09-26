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
no toque nada. El panel (pestaña Ajustes → Render, solo visible cuando el
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

**Por qué importa aquí.** Agregar un canal a mano (Ajustes → Fuentes, ver
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
