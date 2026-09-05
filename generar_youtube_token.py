"""
Genera youtube_token.json mediante el flujo OAuth de Google, una sola vez.
El archivo resultante es el que copias al secret YOUTUBE_TOKEN de GitHub
Actions — no se sube nunca al repo.

Uso:
  python generar_youtube_token.py

Requiere client_secret.json en esta misma carpeta (ver README.md, sección
"Setup").

Funciona igual en PC que en Termux (Android): en vez de intentar abrir un
navegador automáticamente (falla en Termux, que no tiene uno integrado),
imprime el link de autorización para que lo abras a mano en Chrome/el
navegador del teléfono. Como el servidor de callback escucha en
localhost y Chrome corre en el mismo dispositivo, sí lo alcanza aunque
esté en otra app.
"""
import os
import json

from google_auth_oauthlib.flow import InstalledAppFlow

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RUTA_CLIENT_SECRET = os.path.join(BASE_DIR, "client_secret.json")
RUTA_TOKEN = os.path.join(BASE_DIR, "youtube_token.json")
SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
]


def construir_client_secret_interactivo():
    """Arma client_secret.json preguntando Client ID y secreto.

    Google ya no deja ver ni volver a descargar el secreto de un cliente
    OAuth existente: si se perdió el archivo, hay que entrar a Cloud Console
    → Credentials → tu cliente → "+ Add secret", que genera uno nuevo y lo
    muestra UNA sola vez. A veces ofrece descargar el JSON y a veces solo
    enseña la cadena; este camino cubre el segundo caso, y evita tener que
    pegar un bloque JSON de varias líneas en la terminal del teléfono.

    El archivo queda en .gitignore, así que no se sube al repo.
    """
    print("\nNo encontré client_secret.json. Lo armamos aquí mismo.\n")
    print("En Google Cloud Console → APIs & Services → Credentials, abre tu")
    print("cliente OAuth (tipo 'Desktop app'). Ahí está el Client ID, y con")
    print("'+ Add secret' generas un secreto nuevo (el anterior ya no se puede ver).\n")

    client_id = input("Client ID (termina en .apps.googleusercontent.com):\n> ").strip()
    if not client_id.endswith(".apps.googleusercontent.com"):
        raise SystemExit(
            "Ese Client ID no se ve bien: debe terminar en '.apps.googleusercontent.com'."
        )

    client_secret = input("\nClient secret (suele empezar con GOCSPX-):\n> ").strip()
    if len(client_secret) < 10:
        raise SystemExit("Ese secreto se ve demasiado corto; revisa que lo hayas copiado completo.")

    datos = {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
            "redirect_uris": ["http://localhost"],
        }
    }
    with open(RUTA_CLIENT_SECRET, "w", encoding="utf-8") as f:
        json.dump(datos, f, indent=2)
    os.chmod(RUTA_CLIENT_SECRET, 0o600)
    print(f"\n✅ {os.path.basename(RUTA_CLIENT_SECRET)} creado.\n")


def main():
    if not os.path.exists(RUTA_CLIENT_SECRET):
        construir_client_secret_interactivo()

    flow = InstalledAppFlow.from_client_secrets_file(RUTA_CLIENT_SECRET, SCOPES)
    print("Abre este link en tu navegador (Chrome) y autoriza el acceso:\n")
    creds = flow.run_local_server(port=0, open_browser=False)

    with open(RUTA_TOKEN, "w", encoding="utf-8") as f:
        f.write(creds.to_json())

    print(f"✅ Token guardado en {RUTA_TOKEN}")
    print("   Copia el contenido de ese archivo al secret YOUTUBE_TOKEN en GitHub.")


if __name__ == "__main__":
    main()
