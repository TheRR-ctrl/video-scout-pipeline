# Instalar en un teléfono

Todo corre en Termux. No hace falta PC en ningún paso.

## 1. Termux

Instálalo desde **F-Droid**, no desde Google Play: la versión de Play está
abandonada desde 2020 y `pkg install` falla en ella.

<https://f-droid.org/packages/com.termux/>

## 2. Traer el proyecto e instalarlo

Tres órdenes, copiadas tal cual:

```bash
pkg install -y git
git clone -b claude/contenido-automatico-gemini-vhga6y https://github.com/TheRR-ctrl/video-scout-pipeline
bash video-scout-pipeline/instalar.sh
```

Tarda unos minutos (compila Pillow). Android te pedirá permiso de
almacenamiento por el camino: dale a **Permitir**, porque sin eso los videos
no salen a la galería y no puedes pasarlos a TikTok.

Al terminar, el script te dice **qué falta**. Eso es lo importante: `git` trae
el código, pero no las credenciales ni el material.

## 3. Lo que git no trae, y cómo traerlo

Las claves y los videos están en `.gitignore` a propósito — una clave de API
subida a un repo queda en el historial para siempre, y los fondos son cientos
de megas que no pintan nada en git.

| Qué | Para qué | Cómo conseguirlo |
|---|---|---|
| `secretos.env` | Gemini escribe los guiones y la metadata | Clave gratis en [aistudio.google.com/apikey](https://aistudio.google.com/apikey). Ponla desde el panel: **Ajustes → Credenciales** |
| `client_secret.json` | Permiso para subir a YouTube | Google Cloud Console → OAuth de escritorio. Cópialo a la carpeta del proyecto |
| `youtube_token.json` | Tu sesión de YouTube | `python generar_youtube_token.py` — una sola vez, se abre el navegador del teléfono |
| `fondo_*.mp4` | El video que va detrás | Los tuyos. Cópialos con nombre `fondo_vertical_1.mp4`, `fondo_vertical_2.mp4`… |
| `musica_*.mp3` | Música de fondo | `python actualizar_musica.py` las baja de Jamendo |

### Si vienes de otro teléfono

Lo que no se puede regenerar son `secretos.env`, `client_secret.json` y
`youtube_token.json`. Pásalos por cable, por Drive o como prefieras, déjalos
en Descargas del teléfono nuevo y luego:

```bash
cd ~/video-scout-pipeline
cp ~/storage/downloads/secretos.env ~/storage/downloads/client_secret.json ~/storage/downloads/youtube_token.json .
chmod 600 secretos.env
```

Los fondos y la música igual, pero esos también se pueden volver a bajar:

```bash
cp ~/storage/downloads/fondo_*.mp4 .
python actualizar_musica.py
```

## 4. Usarlo

```bash
panel               # abre el panel en el navegador
python pipeline.py  # una tanda completa sin tocar nada
```

Casi todo lo de aquí abajo tiene su botón en **Ajustes → Mantenimiento**, con
la orden escrita al lado. La terminal sigue sirviendo para lo mismo; la lista
del panel está para no tener que acordarse de ellas.

De dónde salen las historias:

```bash
python trend_scout.py                 # busca en Reddit
python youtube_scout.py               # busca en YouTube (anécdotas y confesiones)
python trend_scout.py --estado        # qué hay en la cola
python trend_scout.py --diagnostico   # por qué un escaneo no trajo nada
python youtube_scout.py --diagnostico
```

Las dos fuentes llenan la misma cola y `pipeline.py` corre las dos. Para
elegir canales de YouTube o apagar esa fuente, copia
`config_trends.ejemplo.json` como `config_trends.json` y edítalo.

### Vaciar la cola de lo ya grabado

`guion.txt` guarda todo lo que Gemini escribió, y renderizar no borra nada de
ahí: con las semanas la pestaña **Cola** acumula historias que ya tienen
video. Para quitarlas:

```bash
python limpiar_cola.py       # dice qué quitaría
python limpiar_cola.py --si  # lo hace, guardando antes una copia
```

Empareja por título contra los `.mp4` de la carpeta de salida **y** contra
`resultado_lote.json`, así que también quita las que se grabaron y luego se
borraron del teléfono a los 7 días.

Ojo con la numeración: el video se llama `NN_Titulo.mp4`, donde `NN` es la
posición dentro de `guion.txt`. Al quitar historias, las que quedan se
renumeran — los videos ya hechos conservan su nombre, pero un `--historias 44`
anotado de antes deja de apuntar a lo mismo. Hazlo cuando no tengas
selecciones a medias.

### Borrar un video del teléfono

En la pestaña **Revisar**, debajo del video, está «🗑 Borrar del teléfono».
Borra el `.mp4` y su miniatura, y nada más: lo que ya se subió sigue en
YouTube, y la anotación de que esa historia se grabó se queda donde está —
si se borrara, `limpiar_cola.py` dejaría de saberlo y la historia volvería a
la cola para renderizarse otra vez.

El aviso antes de borrar cambia según el caso. Si el video ya está subido,
solo estás tirando la copia local. Si no, es la única que hay: para
recuperarlo habría que renderizarlo de nuevo desde la historia.

Al lado está **«Borrar los N subidos»**, que hace lo mismo de golpe con todos
los que ya están en YouTube — que es de donde sale el sitio de verdad, sin ir
uno por uno. Solo toca los subidos: los que aún no se han publicado, y los
que `publisher.py` rechazó (que tampoco llegaron a subirse), se quedan donde
están.

### Videos largos: por qué no se hacen ahora

Los 18 videos largos del canal sumaron 43 vistas entre todos. Los 48 shorts,
29.000. No es que gustaran menos: un canal sin base de suscriptores no recibe
tráfico de «sugeridos» ni de «inicio», que es de donde vive el formato largo,
mientras que el feed de Shorts empuja el video a desconocidos sin que nadie te
conozca. Renderizar cinco minutos en el teléfono para sacar dos vistas es
tirar el trabajo.

Así que están bloqueados, pero con una condición y no a mano: cuando el canal
llegue a 500 suscriptores se abren solos. Para ver en qué estado están:

```bash
python formato.py
```

Mientras estén bloqueados, una historia que no quepa en un short **no se
pierde**: el render la salta con un `⏸️ aplazado` y la deja en la cola. El día
que se abran, se graba sola en el siguiente lote.

Para abrirlos antes de llegar al umbral, en `config.json`:

```json
"forzar_largos": true
```

### Historias que no se cuentan dos veces

Antes, dos posts distintos de Reddit que contaban la misma historia entraban
los dos a la cola: se comparaban por el id del post, no por el texto. La
segunda versión salía como refrito y el feed no la repartía. Ahora se comparan
también las palabras del cuerpo, y al buscar aparece cuántas se descartaron
por ser la misma historia que una ya contada.

Con lo mismo, `script_writer.py` tira las historias de temas que el feed de
Shorts no empuja (maltrato infantil, suicidio, violencia grave, contenido
sexual, conspiraciones). Gemini etiqueta el tema al escribir, y la historia
descartada no llega al guion. La lista se puede cambiar en `config.json`:

```json
"temas_bloqueados": ["suicidio_autolesion", "violencia_grave"]
```

### Fondos nuevos sin salir a grabarlos

Los videos de fondo los pones tú: metes gameplay en la SD y
`vincular_fondos.py` los enlaza al repo. Eso sigue igual, pero ahora se puede
completar con material de archivo de [Pexels](https://www.pexels.com), que es
gratis, permite uso comercial y deja modificar.

Hace falta una clave (gratis, sin tarjeta) en
[pexels.com/api](https://www.pexels.com/api/) → *Your API Key*. Va en
`secretos.env`, que no se sube al repo:

```
PEXELS_API_KEY=lo_que_te_den
```

Desde el panel, pestaña Ajustes: **Ver fondos nuevos en Pexels** enseña qué
bajaría sin gastar datos, y **Bajar esos fondos** los trae. A mano:

```bash
python descargar_fondos.py --ver          # qué bajaría
python descargar_fondos.py                # bajarlos
python descargar_fondos.py --tema lluvia  # solo un tema
python descargar_fondos.py --horizontal   # para los videos largos
```

Los temas que busca son lluvia, ciudad, carretera, abstracto y naturaleza:
textura y movimiento lento, nada que le robe atención a la narración. Guarda
tres clips por tema y no baja lo que ya tiene.

Dos topes pensados para un teléfono: ningún clip pasa de 60 MB (se corta a
mitad de la descarga si se pasa, y no deja el archivo a medias), y se baja la
versión más cercana a 1080×1920 sin pasarse, porque el render escala y recorta
de todos modos y un 4K solo gastaría datos para tirar píxeles.

Los clips nuevos no sustituyen al gameplay: el render elige entre **todos** los
`fondo_vertical*` que encuentre, así que esto solo añade variedad a la baraja.
Quién grabó cada uno queda anotado en
`pipeline_state/fondos_atribucion.json` por si quieres citarlo, aunque la
licencia de Pexels no lo exige.

### Medir cómo salió cada video

Los fallos que hacen daño no se ven mirando el video por encima: el audio que
se corta a mitad de frase, el volumen doce decibelios por debajo de lo normal,
el medio segundo en negro del principio —que además es el fotograma que
YouTube ofrece como miniatura—. Antes se subían igual y se descubrían por las
vistas, que para entonces ya no explican nada.

```bash
python calidad.py                   # los que aún no se han medido
python calidad.py --todos           # otra vez, todos
python calidad.py --solo 3          # solo la historia 3
python calidad.py --archivo v.mp4   # un archivo suelto
```

Corre sola detrás de cada render dentro de `pipeline.py`, y el resultado sale
en **Revisar**, junto al video, en la tarjeta «Cómo salió el archivo».

Lo que mide, todo con ffmpeg y en una sola pasada (en un teléfono cada pasada
es descodificar el video entero):

- **Narración incompleta.** Compara la duración real con las palabras del
  guion a 2,6 palabras por segundo. Si falta más del 30%, el TTS entregó menos
  texto del que se le dio. El render ya tiene una guarda propia, pero con un
  margen tan ancho (palabras/6.0) que una narración cortada por la mitad la
  pasa entera.
- **Volumen.** YouTube y TikTok normalizan a unos −14 LUFS. Más bajo y tu
  video suena flojo al lado del siguiente; más alto y te lo bajan ellos.
- **Picos** por encima de −0,5 dBFS, que el recodificado de la plataforma
  convierte en saturación.
- **Silencios** largos en medio, y sobre todo al principio, que es donde se
  decide si alguien se queda.
- **Fotogramas en negro** y **imagen congelada** (el fondo se acabó antes que
  la narración).
- **Resolución** equivocada para el formato.

No frena la publicación a propósito: un umbral mal puesto dejaría el canal
parado sin que nadie se entere. Deja el defecto anotado y lo escribe en el log.

Y lo que esto **no** hace: predecir si un video va a funcionar. Que no tenga
defectos no hace que el feed lo reparta. Sirve para no subir algo roto.

### Que Gemini mire un video de vez en cuando

Lo de arriba son números. Esto es una opinión, cuesta cuota y datos, y por eso
va sobre una muestra: **un video por corrida del pipeline**, el más reciente
que Gemini no haya mirado (que es el que todavía puedes rehacer sin que se
note).

```bash
python calidad_ia.py                  # el más reciente sin analizar
python calidad_ia.py --cuantos 3      # los tres más recientes
python calidad_ia.py --solo 4         # esa historia
python calidad_ia.py --video-entero   # subiendo el mp4, no fotogramas
python calidad_ia.py --con-datos      # sin esperar al wifi
```

Contesta cuatro cosas: si los tres primeros segundos dan alguna razón para
quedarse, si los subtítulos se leen sobre ese fondo en un teléfono, si la
historia cumple lo que el título promete, y —lo más útil— qué cambiaría si
solo pudiera cambiar una cosa. Sale en **Revisar**, debajo de las medidas y
separado de ellas a propósito: mezclarlas haría que la opinión se leyera con
la autoridad del número.

Normalmente va **por fotogramas**: siete imágenes, cuatro de ellas en los tres
primeros segundos, más el guion y lo que ya midió `calidad.py`. Son unos pocos
miles de tokens y menos de un megabyte. Con `--video-entero` se sube el mp4:
ve el ritmo y oye el audio, pero un video de 60 s son unos 16.000 tokens y
15-30 MB de subida.

Se salta sola sin wifi o sin `GEMINI_API_KEY`, y no cuenta como fallo de la
corrida. Para apagar el automático y dejar solo el botón del panel:

```json
"calidad_ia_automatica": false
```

**El límite, otra vez:** que a Gemini le guste un video no predice que el feed
lo reparta. Lo que sí puede ver es lo que tú ya no ves de tanto mirarlo.

### Borrar de YouTube lo que no arrancó y volver a grabarlo

De los 48 shorts, 18 se quedaron por debajo de 300 vistas y la mitad de esos
no pasó de 12. No hay ninguno entre 60 y 300: o el feed reparte el video o no
lo reparte, y cuando no lo reparte el video no se recupera nunca solo.

Un video así no hace nada por el canal, pero la historia sigue sirviendo. En
**Ajustes → Mantenimiento** están estos, y la revisión quincenal que los corre
juntos (más abajo, en «Que corra solo»):

```bash
python relanzar.py                  # las vistas reales de cada video subido
python relanzar.py --duplicados     # copias repetidas que no tuvieron ni una vista
python relanzar.py --sin-vistas     # los que no vio nadie, para rehacerlos
```

Sin `--si` solo enseñan el listado. Con `--si`:

- `--duplicados` borra la copia repetida del canal y **no** la rehace: la
  historia ya está contada en la copia que sí funcionó (se queda la que más
  vistas tiene).
- `--sin-vistas` borra el video **y devuelve la historia a la cola**, así que
  el siguiente `generar_video_maestro.py` la graba de cero, con otro título y
  otra miniatura. Si el guion ya no estaba en `guion.txt`, lo recupera de las
  copias `guion.txt.bak-…` que deja `limpiar_cola.py`.

Un video borrado de YouTube no vuelve. Lo que se borró queda anotado en
`pipeline_state/relanzados.json` con el título y las vistas que tenía.

Dos guardas hacen que esto se pueda dejar corriendo solo sin vigilarlo:

- **No toca lo subido hace menos de 14 días.** Un video de ayer con 0 vistas
  no fracasó: es que todavía no le ha tocado. Con `--dias-minimos 0` se revisa
  todo, que es lo que quieres cuando lo corres a mano y mirando la lista.
- **Una historia se rehace dos veces como mucho.** Se cuentan en
  `relanzados.json`. Si a la tercera sigue a cero, el problema es la historia
  y no el reparto: se queda en el canal y el listado la marca como ya
  intentada. Con `--max-intentos 0` se quita el límite.

Un registro sin fecha de subida tampoco se borra: no hay forma de saber si es
de hace un año o de esta mañana, y en la duda no se toca. El listado los
cuenta aparte para que los números cuadren.

**Esto necesita un permiso que el token viejo no tiene.** El token se generó
solo con «subir» y «leer»; borrar se pide aparte. Para añadirlo:

```bash
rm youtube_token.json
python generar_youtube_token.py
```

Sale el link de autorización, lo abres en el navegador del teléfono y das
permiso, igual que la primera vez. Los permisos nuevos ya están puestos en el
código, así que no hay que tocar nada más. Si te lo saltas, `relanzar.py` lo
dice antes de tocar nada en vez de fallar a mitad. `rehacer_todo.py`, que
también borra del canal, necesitaba lo mismo y nunca había podido hacerlo.

### La pestaña Canal

Lo mismo de arriba, pero mirándolo en vez de leyendo la terminal. La pestaña
**Canal** del panel enseña cuántos videos hay subidos, las vistas que suman,
la mediana, y uno por uno con las vistas que tiene cada uno y cuántos días
lleva en el canal. Los que la revisión quincenal se llevaría van marcados:
«repetida», «nadie la vio» o «sin dato».

Las vistas no se piden ahí. Las deja `relanzar.py` en
`pipeline_state/vistas.json` y el panel las pinta: abrir una pestaña no puede
depender de que haya cobertura, y la API de YouTube tiene cuota diaria. Por eso
arriba dice cuándo se leyeron y hay un botón **↻ Releer vistas** que las vuelve
a pedir. La revisión quincenal las refresca sola cada vez que corre.

Desde ahí mismo están los dos botones de la revisión —**Ver qué haría** y
**Hacerlo ahora**, este último con confirmación— y el estado de los videos
largos, con lo que falta para el umbral de suscriptores.

Esas vistas salen también en **Publicados**, en la línea de debajo de cada
título. «vistas sin leer» no es lo mismo que 0: quiere decir que ese video no
estaba en la última lectura (borrado a mano, con las estadísticas ocultas, o
subido después). Nada que diga «sin leer» entra en los filtros que borran.

**Para encontrar los virales viejos de YouTube** hace falta una clave gratis
(si no, solo se ven los videos recién subidos, que todavía no tienen vistas):

1. Entra a https://console.cloud.google.com/apis/library/youtube.googleapis.com
   y pulsa "Habilitar". Si ya dice "API habilitada", sáltate este paso: pasa
   cuando se generaron las credenciales para **subir** videos.
2. Menú ☰ → "APIs y servicios" → "Credenciales" → "+ Crear credenciales" →
   "Clave de API". Ojo: `client_secret.json` NO sirve para esto — ese es de
   OAuth, para subir en tu nombre; buscar necesita una *clave de API*, que es
   otro tipo de credencial del mismo proyecto.
3. Pégala en el teléfono y compruébala:

```bash
python youtube_scout.py --guardar-clave AIzaSy...
```

Eso la escribe en `secretos.env` y la prueba de una vez. Si ya había una
guardada, la reemplaza (no deja duplicados), así que sirve también para
corregir un intento fallido. Sin comillas: el teclado de Android las
convierte en comillas tipográficas al pegar y rompen el comando.

La clave real empieza con `AIzaSy` y tiene unos 39 caracteres. Para
comprobar una que ya esté guardada:

```bash
python youtube_scout.py --probar-clave
```

`--probar-clave` hace una sola llamada y distingue los tres fallos que desde
fuera se parecen: no hay clave, la clave es inválida, o la API no está
habilitada en ese proyecto.

Con eso, `youtube_scout.py` busca por número de vistas sin importar la fecha,
y además busca por tema en todo YouTube (`youtube_busquedas`), no solo en los
canales de la lista.

El panel también está como acceso directo en la pantalla de inicio, y como
`abrir_panel.html` en Descargas: ese HTML arranca el panel aunque lo muevas
de sitio.

## 5. TikTok (opcional)

El pipeline puede dejar cada video también en TikTok. Va detrás de YouTube y
reutiliza el título y los hashtags que ya se aprobaron ahí, así que no repite
chequeos ni gasta cuota de Gemini otra vez.

Hay dos modos y conviene entender la diferencia antes de empezar:

| | Qué hace | Qué pide TikTok |
|---|---|---|
| **borrador** | Deja el video en el buzón de notificaciones de la app; lo publicas a mano | Permiso `video.upload`, sin trámite |
| **directo** | Publica solo, en el perfil | Permiso `video.publish` **y** que TikTok audite tu app |

Conviene saber esto antes de montarlo: **borrador ahorra menos de lo que
parece**. Sube el video a TikTok para que luego lo bajes desde la app y lo
publiques a mano, sobre un archivo que ya está en tu teléfono. El que de verdad
automatiza es directo.

Y directo sin auditar no vale con una cuenta pública: TikTok lo rechaza con
`unaudited_client_can_only_post_to_private_accounts`, porque exige que la cuenta
entera sea privada. O sea que el camino real es pasar la auditoría, y eso está
explicado paso a paso en `AUDITORIA_TIKTOK.md`.

Lo que tienes que hacer tú, una vez:

1. Crea una app en <https://developers.tiktok.com/>, añádele los productos
   **Login Kit** y **Content Posting API**, y pide los permisos
   `user.info.basic` y `video.publish`.
2. Registra una URL de redirección. Tiene que empezar por `https` y no llevar
   parámetros; no hace falta que sea una web tuya de verdad, porque el código
   llega en la barra de direcciones y lo copias de ahí. `https://example.com/callback`
   sirve.
3. Guarda las credenciales:

```bash
python -c "import secretos; secretos.guardar('TIKTOK_CLIENT_KEY', 'aw...')"
python -c "import secretos; secretos.guardar('TIKTOK_CLIENT_SECRET', '...')"
python generar_tiktok_token.py --redirect https://example.com/callback
```

4. Enciéndelo en `config.json`:

```json
"tiktok": { "activo": true, "modo": "directo", "max_por_corrida": 5 }
```

Para ver qué subiría sin subir nada, y qué lleva subido:

```bash
python tiktok_publisher.py --simular
python tiktok_publisher.py --estado
```

Si ya habías subido videos a TikTok a mano, el registro no lo sabe y los daría
por pendientes: los subiría por segunda vez. Márcalos antes de la primera
corrida, que te los lista numerados y solo tienes que decir cuáles:

```bash
python tiktok_publisher.py --marcar-subidos
```

Igual que la subida a YouTube, esta etapa no sube sin WiFi: los videos pesan
cientos de MB. Para saltarse la protección una vez, `--con-datos`.

A partir de ahí, `python pipeline.py` incluye la etapa `tiktok` al final. Si
está apagada, la etapa termina sola sin hacer nada.

### Tamaño de los videos

Se renderizan con `libx264 -preset veryfast -crf 23`. Si los archivos te salen
grandes o pequeños de más:

```json
"video": { "preset": "veryfast", "crf": 23 }
```

`crf` más bajo = más calidad y más peso (18 es casi indistinguible del original,
28 ya se nota). `preset` más lento = archivo más pequeño a igual calidad, pero
el teléfono tarda más en renderizar.

Los videos hechos antes de ese cambio siguen pesando lo que pesaban. Para
arreglarlos sin volver a renderizarlos enteros:

```bash
python recomprimir.py        # lista los que pesan de más
python recomprimir.py --si   # los recomprime
```

Si una tanda se corta a medias deja temporales `…recomprimiendo.mp4`, que
ocupan sitio y no sirven para nada. El script los detecta solo y los borra
con `python recomprimir.py --limpiar` (nunca con otra recompresión en marcha).

### Mover la carpeta de videos

No basta con moverla desde el gestor de archivos. El pipeline guarda **rutas
absolutas** en `resultado_lote.json`, `publicados.json`, `rechazados.json` y
`tiktok_subidos.json`. Si apuntan a donde ya no hay nada, los pendientes
desaparecen del panel y, peor, lo que ya estaba en TikTok deja de reconocerse
y se vuelve a subir.

Mueve la carpeta y luego repara el estado:

```bash
python mover_salida.py "/storage/emulated/0/Download/Reddicuentos/Videos creados"
python mover_salida.py "/storage/emulated/0/Download/Reddicuentos/Videos creados" --si
```

Sin `--si` solo enseña el recuento. Deja una copia `.bak` de cada fichero que
toca, actualiza `carpeta_salida` en `config.json`, y se puede correr dos veces
sin estropear nada. Después, reinicia el panel.

## 6. Que corra solo

```bash
bash instalar_cron.sh
```

Deja cuatro tareas: generar una tanda lunes y jueves a las 6:00, publicar un
video cada día a las 9:00, refrescar la música el día 1 de cada mes, y la
revisión del canal los días 1 y 15 a las 7:30. Correrlo dos veces no duplica
nada, y `bash instalar_cron.sh --quitar` las borra.

### La revisión quincenal

Es lo que mira qué funcionó y qué no, y rehace lo que no:

```bash
bash revision_quincenal.sh --ver    # solo lista, no borra nada
bash revision_quincenal.sh          # lo hace
```

También está en **Ajustes → Mantenimiento**, en las dos versiones. Hace, por
ese orden, `relanzar.py --duplicados --si` y `relanzar.py --sin-vistas --si`
con las guardas por omisión (14 días, 2 intentos). El orden importa: si se
hiciera al revés, una historia repetida entraría por `--sin-vistas` y se
volvería a grabar el refrito.

Cada pasada se apunta con la fecha en `revision.log`:

```bash
tail -40 revision.log
```

No renderiza nada: deja las historias en la cola y las graba el cron de lunes
y jueves. Si no quieres esperar, `python generar_video_maestro.py`.

Los días 1 y 15 son dos semanas justas de media, y caen siempre en la misma
fecha — un `*/14` en el día del mes se desfasa cada mes, porque los meses no
tienen 28 días.

No lo escribas a mano en el crontab. La ruta del proyecto es larga, y cuando
se equivoca no avisa: cron lanza la línea a su hora, el `cd` falla, el `&&`
corta la cadena, y no queda ni log ni error — parece que el cron no se
dispara cuando en realidad corre a diario y no llega a nada.

Dos cosas de Android que el script no puede arreglar solo, y que son la causa
habitual de que un cron bien puesto deje de saltar a los pocos días:

- **Termux:Boot** ([F-Droid](https://f-droid.org/packages/com.termux.boot/)),
  instalado y abierto una vez. Sin él, cron muere en cada reinicio. La app por
  sí sola no arranca nada: solo ejecuta lo que haya en `~/.termux/boot/`, y el
  script que hace falta ahí lo deja puesto `instalar_cron.sh`.
- **Batería sin restricciones** para Termux: Ajustes → Apps → Termux →
  Batería → Sin restricciones.

Para ver si está corriendo de verdad:

```bash
tail -f ~/video-scout-pipeline/pipeline.log
```

## 7. Actualizar

```bash
cd ~/video-scout-pipeline
git pull
bash instalar.sh     # opcional: solo si hay dependencias nuevas
```

Y cierra y reabre el panel para que cargue la versión nueva.
