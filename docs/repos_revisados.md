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
