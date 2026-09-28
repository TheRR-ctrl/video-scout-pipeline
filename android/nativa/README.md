# La app sin Termux

El módulo `:app` le pide a Termux que arranque `servidor.py`. Este no le pide
nada a nadie: **lleva CPython dentro del APK**. El panel, los buscadores, los
guiones y el render corren dentro de la app.

Está **aparte** de `:app` y no lo sustituye. Que compila lo dice el workflow;
que funciona en un teléfono solo se ve instalándolo, y hasta entonces la app
instalable tiene que seguir siendo la que ya se probó.

## Cómo encaja

```
  APK
   ├── CPython 3.12 + pip (Chaquopy)      ← el intérprete
   ├── assets/pipeline.zip                 ← los .py del repo, web/, fuentes/
   ├── lib/arm64-v8a/libffmpeg.so          ← ffmpeg, disfrazado de librería
   └── Java: Activity + servicio en primer plano
```

Al arrancar:

1. `Arranque.preparar()` descomprime `pipeline.zip` en `filesDir/pipeline`
   (solo si cambió la versión de la app) y enlaza los binarios.
2. `ServicioPanel` inicia Chaquopy y llama a `arranque_panel.iniciar()`.
3. Ese módulo pone ffmpeg en el `PATH`, hace `chdir` a la carpeta del
   pipeline, e importa `servidor` — **sin tocarlo**.
4. La Activity sondea el puerto 8770 y carga el panel de siempre.

## Las tres decisiones que no son obvias

**El pipeline se descomprime en vez de correr desde el APK.** Veinte módulos
calculan su `BASE_DIR` con `os.path.dirname(__file__)` y cuelgan de ahí
`pipeline_state/`, `config.json`, `guion.txt` y los videos. Desde dentro del
APK ese directorio sería de solo lectura y habría que tocar los veinte.
Descomprimido en `filesDir`, `BASE_DIR` es escribible y el pipeline funciona
igual que en Termux. Al actualizar la app se borra **el código y solo el
código**: `pipeline_state/` sobrevive, porque perderlo sería volver a subir
el canal entero.

**ffmpeg viaja como `libffmpeg.so`.** Desde Android 10 una app no puede
ejecutar un archivo de su propia carpeta de datos (W^X); el directorio de
librerías del APK es el único sitio del que sí puede. Como el pipeline llama
a `ffmpeg` y usa `shutil.which`, `Arranque` crea un enlace
`filesDir/bin/ffmpeg → …/lib/arm64-v8a/libffmpeg.so` y lo pone en el `PATH`.
Así las 31 llamadas del pipeline siguen siendo `subprocess.run(["ffmpeg",…])`
en los tres sitios donde corre: Termux, el runner y la app.

**Un binario y no una librería tipo ffmpeg-kit.** Una librería obligaría a
reescribir esas 31 llamadas con un camino distinto solo para Android, y a
mantenerlo separado del que usan Termux y el runner. Ver
`docs/repos_revisados.md` §8.

## Lo que se pierde: el chip de video

**`usar_chip_android` no funciona aquí.** El soporte de `h264_mediacodec` en
ffmpeg pasa por JNI y necesita una JVM viva (`av_jni_set_java_vm`); un
ejecutable suelto no tiene ninguna. Es la razón de fondo del issue #73 de
mobile-ffmpeg, y no tiene vuelta mientras ffmpeg sea un binario.

No hace falta tocar nada: `usar_chip_android` ya viene en `false`, y si
alguien lo enciende desde Ajustes el pipeline **cae solo a libx264** (ver
`CLAUDE.md`, "El chip de video de Android"). El coste es real de todas
formas: renderizar por CPU es más lento y gasta más batería.

Recuperarlo exigiría llamar a ffmpeg como librería desde el proceso de la
app, que es donde sí hay JVM. Chaquopy permite llamar a Java desde Python,
así que es posible — pero es el refactor de las 31 llamadas, y no se ha
hecho.

## Lo que hay que mirar primero cuando se pruebe en un teléfono

1. **Que arranque.** Si a los 60 s nadie contesta en el 8770, el fallo está
   en logcat con la etiqueta `ServicioPanel` — casi siempre una dependencia
   de Python que no entró en el APK.
2. **El límite de `dataSync`.** El servicio se declara
   `foregroundServiceType="dataSync"`, y Android 15 pone tope de tiempo a ese
   tipo. Un render largo puede chocar con él. No está medido.
3. **Dónde quedan los videos.** `carpeta_salida` apunta a una ruta relativa
   que ahora cae dentro de `filesDir`, invisible para la galería. Para verlos
   desde el teléfono habrá que exportarlos con MediaStore.
4. **Las credenciales.** `secretos.env` y `tiktok_token.json` tienen que
   nacer en `filesDir/pipeline`, y ahí ya son privados de la app — el
   `chmod 600` de Termux deja de hacer falta, pero hay que ponerlos.

## Compilar

```bash
# ffmpeg primero (tarda, y solo hace falta cuando cambia su versión)
ANDROID_NDK_HOME=/ruta/al/ndk bash ffmpeg/construir.sh

# luego el APK
gradle :nativa:assembleDebug
```

El workflow **Compilar la app de Android** hace las dos cosas.
