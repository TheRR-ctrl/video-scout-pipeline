# La auditoría de TikTok: por qué este proyecto no la pasa

Este documento explicaba paso a paso cómo enviar la app a revisión. Estaba
equivocado en lo más importante, así que ahora explica lo contrario: **tal como
está montado el proyecto, la auditoría no se puede pasar**, y enviarla solo
sirve para acumular un rechazo en el historial de la app.

## Lo que dice TikTok

En las [Content Sharing Guidelines][csg], lista de casos de uso **no
aceptables**, literalmente:

> - An app that copies arbitrary contents from other platforms to TikTok.
> - A utility tool to help upload contents to the account(s) you or your team
>   manages.

Y en las [App Review Guidelines][arg], como requisito de admisión:

> Apps must not be for private or personal use.

El segundo punto describe este proyecto con precisión incómoda: una herramienta
para subir a la cuenta que tú manejas. No es una interpretación severa de la
regla, es la regla.

Lo peor es que la versión anterior de este documento lo decía sola. El texto de
1000 caracteres que proponía enviar empezaba así:

    Archivo de Relatos Olvidados is a personal tool I built for my own TikTok
    account (@reflexiadaily).

Es la frase prohibida, redactada por nosotros, en la primera línea que lee el
revisor.

Del primer punto —copiar contenido de otras plataformas— sí se puede salir.
Aquí no se copia nada: Gemini reescribe la historia, el narrador pone una voz
nueva y el motor fabrica el fondo. El vídeo es una obra nueva. Pero eso hay que
explicarlo, y "turns public stories into narrated short videos" suena justo a
lo que prohíben.

## Lo que no vale hacer

Presentar el proyecto como una herramienta abierta a creadores en general
cuando es para tu canal. Además de ser mentirle al revisor, no cuela: el vídeo
demo que piden enseña el login, y en el login se ve una sola cuenta,
@reflexiadaily, autorizándose a sí misma. Y una app aprobada sobre una
descripción falsa se revoca cuando lo miran otra vez, normalmente cuando ya
dependes de ella.

## Lo que sí vale

### 1. Publicar a mano (lo que hay hoy)

El pipeline deja el MP4 en el teléfono y tú lo subes desde la galería. Suena a
derrota, pero es que las dos alternativas de la API son peores:

- **Directo** necesita la auditoría de arriba, que no se pasa.
- **Borrador** parece la salida y no lo es. Sube el vídeo al buzón, y al tocar
  la notificación **la app lo vuelve a descargar** al móvil para abrir el
  editor. Como aquí el render se hace en el propio teléfono, eso es subirlo y
  bajarlo para acabar donde ya estabas. Está comprobado en el teléfono; la
  documentación de TikTok no lo cuenta.

Para que subirlo a mano cueste lo mínimo, el pie —título y hashtags, los
mismos que se aprobaron para YouTube— se deja copiado en el portapapeles, que
es lo único de esa etapa que se podía automatizar:

```bash
python tiktok_publisher.py --pie
```

Te lista los pendientes, eliges uno por número y lo copia. En el panel está el
mismo botón, **Copiar pie**, en cada pendiente de la pestaña TikTok. Necesita
`termux-api` (`instalar_panel.sh` ya lo instala); si no está, el pie se imprime
igual y lo copias manteniendo pulsado.

El panel sigue llevando el registro: cuando subas uno a mano, dale a **Ya
subido** para que deje de contar como pendiente.

### 2. Publicar a través de un servicio ya auditado

Postiz, bundle.social y parecidos tienen la auditoría pasada. Tú eres un
usuario suyo, que es un caso de uso que TikTok sí acepta. Es la única forma de
tener publicación automática de verdad sin mentir ni montar un producto.
A cambio: cuota mensual, un servicio de por medio en un proyecto que se
construyó justamente para no depender de ninguno, y tus vídeos pasando por
sus servidores.

### 3. Sacar el render del teléfono

Esta no desbloquea la auditoría, pero es la única que hace útil el modo
borrador. Si el vídeo se fabricara en el runner de GitHub —como ya se fabrican
los fondos— en vez de en el móvil, el buzón dejaría de ser un viaje redondo y
pasaría a ser el transporte: el vídeo llegaría al teléfono una sola vez, que es
la que hace falta de todas formas. Y con `source=PULL_FROM_URL` el móvil ni
siquiera sube: se le da a TikTok una URL y él se lo descarga. El prefijo de
dominio verificado ya lo tienes de las páginas de términos y privacidad.

Lo que cuesta: mover el render completo, voz incluida, fuera de Termux; y que
el MP4 esté en una URL pública los minutos que TikTok tarde en recogerlo.

### 4. Que la descripción sea verdad

Abrir el proyecto a otros creadores: login multiusuario, callback alojado de
verdad, y custodia de tokens que no son tuyos. Entonces la auditoría es
honesta y se puede pasar, y con ella el modo directo. Es un proyecto distinto
del que tienes, y desde un teléfono es mucho trabajo, pero es el único camino
legítimo hasta `video.publish`.

Los permisos, si algún día se usa la API: `user.info.basic` y `video.upload`.
`video.publish` no se pide mientras no haya auditoría — y ojo, porque la
versión anterior de este documento mandaba justo lo contrario.

## Si algún día vas por la opción 4

Todo lo que hace falta para grabar el demo sigue en el repo y sigue
funcionando: `demo_tiktok.py` lleva el guion de las cinco escenas, comprueba
antes de grabar lo que te obligaría a repetir la toma, y recomprime la
grabación a los 50 MB que admite el portal sin bajar la resolución.

```bash
python demo_tiktok.py --comprobar
python demo_tiktok.py --empezar
python demo_tiktok.py --estado
python demo_tiktok.py --revisar grabacion.mp4
python demo_tiktok.py --recomprimir grabacion.mp4
```

Las cinco escenas, en orden: el login con la pantalla de permisos de TikTok; el
panel enseñando la cuenta destino y las opciones que devuelve
`/creator_info/query/`; la elección de privacidad guardada; la subida
completándose; y el vídeo ya en el perfil. Entre la 3 y la 5 no se corta ni se
acelera, porque lo que comprueban es que nada se publica sin que alguien lo
haya elegido antes.

Y el detalle que no está en ninguna documentación: sin auditar, publicar en
directo a una cuenta pública falla con
`unaudited_client_can_only_post_to_private_accounts`. Para grabar la subida
funcionando hay que poner la cuenta en privado, grabar, y volver a ponerla
pública. Es el camino previsto por TikTok, no un rodeo.

Lo que **no** se puede reaprovechar es el texto de 1000 caracteres de la
versión anterior. Ese hay que escribirlo entero de nuevo, describiendo la
herramienta abierta que para entonces existiría.

[csg]: https://developers.tiktok.com/docs/en/content-sharing-guidelines
[arg]: https://developers.tiktok.com/docs/en/app-review-guidelines
