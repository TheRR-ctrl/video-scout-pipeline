# La app de Android

Un WebView apuntando a `http://127.0.0.1:8770`, que es donde vive el panel
cuando `servidor.py` corre en Termux. 19 KB de APK, sin dependencias.

## Para qué existe, si el panel ya se podía instalar desde Chrome

Por una sola cosa, y es la que no tiene vuelta: **una página web no puede
arrancar el servidor**. Ningún navegador deja que una página lance Python en
tu teléfono, y hace bien. Así que con la PWA instalada el camino seguía
siendo: abrir Termux, escribir `panel`, cambiarse a la app.

Una app instalada sí puede pedírselo a Termux, con el intent `RUN_COMMAND`.
Eso es todo lo que hace de más, y es todo lo que hacía falta: tocas el icono
y el servidor arranca solo.

Lo demás (icono propio, sin barra de navegador, pantalla decente cuando el
panel está apagado) ya lo da la PWA — ver `web/sw.js`. Si no quieres instalar
un APK, no lo instales: el panel funciona igual.

## Qué hace, en orden

1. ¿Contesta alguien en `127.0.0.1:8770`? Entra y ya está. Da igual quién lo
   arrancó: el widget, el autoarranque de `.bashrc`, o tú a mano.
2. Si no, le pide a Termux que corra `panel-servidor` (lo deja
   `instalar_panel.sh`) y espera hasta 30 segundos sondeando el puerto.
3. Si algo falla, enseña `assets/ayuda.html` diciendo **cuál** de las tres
   cosas falló: no está Termux, falta el permiso, o Termux no aceptó la
   orden. Nunca una pantalla en blanco.

Al volver a la app desde Termux (`onResume`) se vuelve a mirar el puerto sin
pedir arranque, así que si lo levantaste a mano entra sola.

## Lo que hay que hacer una vez en el teléfono

```bash
bash instalar_panel.sh --app
```

Eso deja el comando `panel-servidor` y pone `allow-external-apps=true` en
`~/.termux/termux.properties`. Sin esa línea Termux ignora la orden, y la app
te lo dirá con esas palabras.

**Esa línea no es solo para esta app.** Vale para cualquier app que tenga el
permiso `com.termux.permission.RUN_COMMAND`. Por eso el instalador no la pone
por su cuenta y hay que pedirla con `--app`; para deshacerlo, se borra del
archivo.

## Compilar

En el teléfono no se puede: el SDK de Android no existe para Termux. Lo hace
el runner, con el workflow **Compilar la app de Android**
(`.github/workflows/apk.yml`), que deja el `.apk` como artifact — o en una
release si le marcas la casilla, que desde el móvil es mucho más cómodo que
bajar un zip.

Sale firmado con la clave de depuración, que es la única posible sin guardar
un keystore en el repo. Para una app personal que se instala a mano, sobra.

## Decisiones que parecen raras y no lo son

**Sin AndroidX ni ninguna librería.** Es una `Activity` y un `WebView`. Lo
que no se importa no se rompe en la siguiente actualización, y el APK baja de
19 KB en vez de varios megas.

**La app no sabe dónde clonaste el repo.** Llama a `panel-servidor`, un
ejecutable con nombre fijo en el PATH de Termux que crea
`instalar_panel.sh` — que sí sabe la ruta, porque corre dentro del repo. Así
mover la carpeta no obliga a recompilar nada.

**La lógica está en Java, no en la página de ayuda.** `ayuda.html` solo pinta
lo que `MainActivity` le dice con `pintar('...')`, y sus botones son enlaces
`mesa://…` que la Activity intercepta. Podría haber sondeado el puerto desde
la propia página, pero repartir la misma decisión entre dos sitios es como se
acaba con dos comportamientos distintos.

**Solo `127.0.0.1` puede ir sin cifrar** (`res/xml/red.xml`). El panel no
puede tener HTTPS. El resto del tráfico sigue cerrado: si un día esta app
cargara una URL externa por error, no podría hacerlo en claro. Los enlaces
que no son del panel (la fuente en Reddit, el video en YouTube) se abren en
el navegador de verdad, fuera de la app.

## Lo que NO está probado

El APK compila —eso lo comprueba el workflow en cada push— pero su
comportamiento en un teléfono de verdad (que Termux acepte el intent, que el
servidor levante a tiempo, que el WebView reproduzca las previsualizaciones)
solo se puede ver instalándolo. Si algo no va, la pantalla de ayuda dice en
qué paso se quedó, que es justo para lo que está.
