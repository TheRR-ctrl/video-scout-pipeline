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

## 6. Que corra solo

```bash
bash instalar_cron.sh
```

Deja tres tareas: generar una tanda lunes y jueves a las 6:00, publicar un
video cada día a las 9:00, y refrescar la música el día 1 de cada mes.
Correrlo dos veces no duplica nada, y `bash instalar_cron.sh --quitar` las
borra.

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
