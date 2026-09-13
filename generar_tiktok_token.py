"""
Genera tiktok_token.json con el flujo OAuth de TikTok. Una sola vez.

Antes de correrlo hace falta una app en https://developers.tiktok.com/:

  1. Crea la app y añádele los productos "Login Kit" y "Content Posting API".
  2. Pide los permisos (scopes) user.info.basic y video.publish. El primero es
     el que deja al panel enseñar a qué cuenta va el video; el segundo es el de
     publicar. video.publish necesita que TikTok audite la app: ver
     AUDITORIA_TIKTOK.md. Sin auditar solo funciona con la cuenta en privado.
     El modo borrador, que usa video.upload en vez de video.publish, no hace
     falta pedirlo: deja el video en el buzon de notificaciones para que lo
     publiques a mano, que es justo el trabajo que esto viene a quitar.
  3. Registra una URL de redirección. TikTok exige que empiece por https y que
     no lleve parámetros. No hace falta que sea una web tuya de verdad: solo
     tiene que coincidir con la que pongas aquí, porque el código de
     autorización llega en la barra de direcciones y lo copias de ahí. Si no
     tienes ningún dominio, https://example.com/callback sirve.
  4. Copia Client key y Client secret a secretos.env:

       TIKTOK_CLIENT_KEY=...
       TIKTOK_CLIENT_SECRET=...

Después:

  python generar_tiktok_token.py --redirect https://example.com/callback

El script imprime un enlace, lo abres en el navegador del teléfono, apruebas,
y pegas aquí la URL completa a la que te redirige (aunque la página dé error:
lo que importa es el ?code= que lleva dentro).
"""
import os
import sys
import json
import time
import base64
import hashlib
import secrets as random_secrets
import argparse
from urllib.parse import urlencode, urlparse, parse_qs

import requests

import secretos  # carga secretos.env

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RUTA_TOKEN = os.path.join(BASE_DIR, "tiktok_token.json")

URL_AUTORIZAR = "https://www.tiktok.com/v2/auth/authorize/"
URL_TOKEN = "https://open.tiktokapis.com/v2/oauth/token/"


def par_pkce():
    """(verifier, challenge) para PKCE.

    TikTok lo exige en apps de móvil y escritorio, y lo acepta en las de web,
    así que se manda siempre: sobra en un caso y es obligatorio en el otro.
    """
    verifier = random_secrets.token_urlsafe(64)[:128]
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return verifier, challenge


def main(argv):
    ap = argparse.ArgumentParser(description="Autoriza la app de TikTok y guarda el token.")
    ap.add_argument("--redirect", required=True,
                    help="La URL de redirección registrada en developers.tiktok.com.")
    ap.add_argument("--scope", default="user.info.basic,video.publish",
                    help="Separados por comas. Por defecto los del modo directo; "
                         "añade video.upload solo si vas a usar el modo borrador.")
    args = ap.parse_args(argv)

    clave = os.environ.get("TIKTOK_CLIENT_KEY", "").strip()
    secreto = os.environ.get("TIKTOK_CLIENT_SECRET", "").strip()
    if not clave or not secreto:
        raise SystemExit(
            "Faltan TIKTOK_CLIENT_KEY o TIKTOK_CLIENT_SECRET en secretos.env.\n"
            "Están en https://developers.tiktok.com/ → tu app → Credentials.\n"
            "Guárdalas con:\n"
            "  python -c \"import secretos; secretos.guardar('TIKTOK_CLIENT_KEY', 'aw...')\""
        )

    verifier, challenge = par_pkce()
    estado = random_secrets.token_urlsafe(16)
    enlace = URL_AUTORIZAR + "?" + urlencode({
        "client_key": clave,
        "scope": args.scope,
        "response_type": "code",
        "redirect_uri": args.redirect,
        "state": estado,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    })

    print("\n  1. Abre este enlace en el navegador del teléfono y aprueba el acceso:\n")
    print(enlace)
    print("\n  2. Te va a redirigir a tu URL. Es normal que la página dé error;")
    print("     lo que importa es la dirección. Cópiala entera y pégala aquí.\n")

    devuelta = input("  URL a la que te redirigió:\n  > ").strip()
    query = parse_qs(urlparse(devuelta).query)
    codigo = (query.get("code") or [""])[0]
    if not codigo:
        error = (query.get("error_description") or query.get("error") or ["no venía ningún ?code="])[0]
        raise SystemExit(f"\n ✗ No pude sacar el código de esa URL: {error}")
    if (query.get("state") or [""])[0] != estado:
        # El state es lo único que ata la respuesta a esta ejecución. Si no
        # coincide, esa URL es de otro intento (o de otra persona) y el código
        # que lleva no es el que pedimos.
        raise SystemExit("\n ✗ El 'state' no coincide. Repite el proceso desde el principio.")

    r = requests.post(
        URL_TOKEN,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "client_key": clave,
            "client_secret": secreto,
            "code": codigo,
            "grant_type": "authorization_code",
            "redirect_uri": args.redirect,
            "code_verifier": verifier,
        },
        timeout=30,
    )
    datos = r.json()
    if "access_token" not in datos:
        raise SystemExit(f"\n ✗ TikTok no dio el token: {datos}")

    token = {
        "access_token": datos["access_token"],
        "refresh_token": datos.get("refresh_token", ""),
        "expira_en": time.time() + int(datos.get("expires_in", 86400)),
        "scope": datos.get("scope", args.scope),
    }
    with open(RUTA_TOKEN, "w", encoding="utf-8") as f:
        json.dump(token, f, indent=2)
    try:
        os.chmod(RUTA_TOKEN, 0o600)
    except OSError:
        pass

    print(f"\n ✓ Guardado en {RUTA_TOKEN}")
    print(f"   Permisos concedidos: {token['scope']}")
    if not token["refresh_token"]:
        # Sin refresh_token el acceso muere en 24 horas y hay que repetir todo
        # esto a mano, que es justo lo que un pipeline automático no puede
        # hacer. Mejor saberlo ahora que dentro de un día.
        print("   ⚠️ TikTok no devolvió refresh_token: el acceso caducará en 24 h.")
        print("      Revisa que la app tenga activado el producto Login Kit.")
    print("\n   Pruébalo sin subir nada:  python tiktok_publisher.py --simular")


if __name__ == "__main__":
    main(sys.argv[1:])
