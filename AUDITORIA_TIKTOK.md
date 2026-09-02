# Pasar la auditoría de TikTok

Sin auditar, TikTok solo deja publicar en directo a cuentas privadas, así que
los vídeos llegan al buzón de notificaciones y hay que abrirlos a mano. Con la
auditoría pasada, `modo: "directo"` publica en el perfil y el flujo queda como
el de YouTube: decides en el panel y el cron hace el resto.

Se revisa la app de **Production**, no el sandbox. La configuración es la misma
que ya tienes; lo que falta es el bloque **App review**.

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

Máximo 50 MB, mp4 o mov. Graba la pantalla del móvil y que se vea, en este
orden y sin cortes:

1. **Login Kit.** Lanza `generar_tiktok_token.py`, abre el enlace, y que se vea
   la pantalla de permisos de TikTok con los scopes y tu cuenta.
2. **El panel.** Abre la pestaña TikTok y pulsa **Preparar** en un vídeo. Tiene
   que verse la cuenta destino (`@reflexiadaily`), el desplegable de privacidad
   empezando por «elige una opción», las casillas de comentarios/dúo/stitch, y
   el interruptor de contenido comercial con sus dos casillas y los enlaces.
3. **La elección.** Elige una privacidad y guarda. Que se vea el ✓.
4. **La subida.** Pulsa Subir y que se vea el progreso.
5. **El resultado.** Abre TikTok y enseña el vídeo publicado en el perfil.

Lo que más rechazos causa es no enseñar un scope que pediste. Si en la app
tienes `video.upload` además de `video.publish`, enseña también una subida en
modo borrador; si no vas a usarlo, quítalo antes de enviar.

## 3. Enviar

Portal → app de Production → **App review** → rellena los dos campos → **Submit
for review**. Mientras la revisan, la app sigue funcionando en sandbox.

## 4. Cuando la aprueben

En el panel ya aparecerá `PUBLIC_TO_EVERYONE` entre las privacidades, porque
`/creator_info/query/` deja de restringirlas. Cambia el modo:

```bash
python -c "import json;c=json.load(open('config.json'));c['tiktok']['modo']='directo';json.dump(c,open('config.json','w'),indent=2,ensure_ascii=False);print(c['tiktok'])"
```

A partir de ahí, en el panel preparas cada vídeo y el cron lo publica.
