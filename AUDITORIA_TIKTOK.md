# Pasar la auditoría de TikTok

Sin auditar, el único modo que funciona con una cuenta pública es **borrador**,
y borrador no te ahorra trabajo: sube el vídeo a TikTok para que luego lo bajes
desde la app y lo publiques a mano, sobre un archivo que ya tienes en el
teléfono. La auditoría es lo que desbloquea `modo: "directo"`, y con ella la
etapa de TikTok queda como la de YouTube: decides en el panel y el cron publica.

Se revisa la app de **Production**, no el sandbox.

## 0. Antes de enviar: quita `video.upload`

TikTok rechaza la revisión si pides un permiso que no enseñas funcionando en el
vídeo demo. Como el modo borrador ya no te sirve, `video.upload` solo añade una
escena más que grabar y un motivo más de rechazo.

En la app de Production → **Scopes** → quita `video.upload`, deja
`user.info.basic` y `video.publish` → **Apply changes**.

A partir de ahí el modo borrador deja de funcionar. Da igual: está apagado.

Repasa también que el resto siga completo, porque la revisión mira toda la
ficha, no solo el formulario:

- Category y Description rellenadas.
- Terms of Service y Privacy Policy apuntando a las páginas publicadas
  (`https://therr-ctrl.github.io/video-scout-pipeline/terminos.html` y
  `.../privacidad.html`), con el prefijo verificado en verde.
- Login Kit con su Redirect URI.
- Content Posting API con **Direct Post** activado.

## 1. La explicación (campo de 1000 caracteres)

Va en inglés, que es como la leen. Cabe en el límite:

```
Archivo de Relatos Olvidados is a personal tool I built for my own TikTok
account (@reflexiadaily). It turns public stories into narrated short videos.

Login Kit: I authorize my own account once. The app stores the token locally on
my phone and refreshes it; no other user ever logs in.

Content Posting API (video.publish): before anything is posted, I open the
app's review panel, which queries /creator_info/query/ and shows me the target
account, the privacy options that account allows, and the interaction settings
(comment, duet, stitch), greyed out when my account disables them. Privacy has
no default: nothing is posted until I pick one. The panel also has the
commercial content disclosure toggle with the "your brand" and "branded
content" checkboxes and the corresponding music and branded content policy
links. Only after I save those choices does the upload run, sending exactly
what I selected. Posts are uploaded one at a time from my phone.
```

## 2. El vídeo demo

Máximo 50 MB, mp4 o mov. Es una grabación de pantalla del móvil.

**El truco que hay que saber antes de grabar:** todavía no estás auditado, así
que una publicación directa a una cuenta pública falla con
`unaudited_client_can_only_post_to_private_accounts`. Para poder grabar la
subida funcionando:

1. Pon **@reflexiadaily en privado** (Ajustes → Privacidad → Cuenta privada).
2. Graba el demo entero.
3. Vuelve a ponerla pública.

Es el camino previsto por TikTok para demostrar Direct Post antes de la
auditoría, no un rodeo: el revisor espera ver exactamente eso.

Con los vídeos ya recomprimidos la subida dura poco, así que el demo entra en
una sola toma sin cortes. Que se vea, en este orden:

1. **Login Kit.** Lanza `generar_tiktok_token.py`, abre el enlace, y que se vea
   la pantalla de permisos de TikTok con los scopes y tu cuenta.
2. **El panel.** Abre la pestaña TikTok y pulsa **Preparar** en un vídeo. Tiene
   que verse la cuenta destino (`@reflexiadaily`), el desplegable de privacidad
   empezando por «elige una opción», las casillas de comentarios/dúo/stitch, y
   el interruptor de contenido comercial con sus dos casillas y los enlaces.
3. **La elección.** Elige una privacidad y guarda. Que se vea el ✓.
4. **La subida.** Lánzala y que se vea el progreso hasta el final.
5. **El resultado.** Abre TikTok y enseña el vídeo ya publicado en el perfil.

No cortes ni aceleres el vídeo entre el paso 3 y el 5: lo que están comprobando
es justamente que nada se publica sin que tú lo hayas elegido antes.

## 3. Enviar

Portal → app de Production → **App review** → rellena los dos campos → **Submit
for review**. Mientras la revisan, la app sigue funcionando en sandbox.

## 4. Cuando la aprueben

Las credenciales de Production son distintas a las del sandbox, así que hay que
guardarlas y volver a autorizar una vez:

```bash
python -c "import secretos; secretos.guardar('TIKTOK_CLIENT_KEY', 'aw...')"
python -c "import secretos; secretos.guardar('TIKTOK_CLIENT_SECRET', '...')"
python generar_tiktok_token.py --redirect https://therr-ctrl.github.io/video-scout-pipeline/callback.html
```

Y enciende el modo directo:

```bash
python -c "import json;c=json.load(open('config.json'));c['tiktok'].update(activo=True, modo='directo');json.dump(c,open('config.json','w'),indent=2,ensure_ascii=False);print(c['tiktok'])"
```

En el panel ya aparecerá `PUBLIC_TO_EVERYONE` entre las privacidades, porque
`/creator_info/query/` deja de restringirlas. A partir de ahí preparas cada
vídeo en el panel y el cron lo publica.
