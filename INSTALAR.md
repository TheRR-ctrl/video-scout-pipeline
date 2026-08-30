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
2. "Habilitar" → menú "Credenciales" → "Crear credenciales" → "Clave de API"
3. Pégala en el teléfono:

```bash
echo 'YOUTUBE_API_KEY=AIza...' >> secretos.env
```

Con eso, `youtube_scout.py` busca por número de vistas sin importar la fecha,
y además busca por tema en todo YouTube (`youtube_busquedas`), no solo en los
canales de la lista.

El panel también está como acceso directo en la pantalla de inicio, y como
`abrir_panel.html` en Descargas: ese HTML arranca el panel aunque lo muevas
de sitio.

## 5. Actualizar

```bash
cd ~/video-scout-pipeline
git pull
bash instalar.sh     # opcional: solo si hay dependencias nuevas
```

Y cierra y reabre el panel para que cargue la versión nueva.
