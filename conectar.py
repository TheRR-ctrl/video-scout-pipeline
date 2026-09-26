"""
Conectar servicios desde el panel: dónde sacar cada clave y si la pegada sirve.

Antes, poner una clave era saberse el nombre exacto de la variable, ir a
buscar la página donde se saca, pegarla a ciegas y enterarse de que no
servía en el siguiente render. Aquí cada servicio lleva su enlace y sus
pasos, y la clave se prueba contra el propio servicio ANTES de guardarla:
una llamada mínima, que distingue "clave mala" de "API sin habilitar" de
"sin conexión ahora mismo", que se arreglan en sitios distintos.

La clave solo viaja al servicio al que pertenece. No se escribe en el log
ni en la línea de comandos (ver CLAUDE.md): la guarda secretos.guardar, en
secretos.env con permisos 600.
"""
import re

import secretos

try:
    import requests
except ImportError:          # el panel sigue funcionando; solo no se prueba
    requests = None

# Lo que el panel enseña de cada servicio. "obligatoria" es lo que el
# pipeline necesita para su vuelta diaria; el resto añade cosas.
SERVICIOS = {
    "GEMINI_API_KEY": {
        "nombre": "Gemini",
        "para": "Escribe los guiones, los títulos y revisa la calidad. Sin ella no sale nada nuevo.",
        "obligatoria": True,
        "url": "https://aistudio.google.com/apikey",
        "pasos": ["Entra con tu cuenta de Google.",
                  "Pulsa «Create API key» y cópiala entera."],
        "patron": r"^(AIza[0-9A-Za-z_\-]{35}|AQ\.[0-9A-Za-z_\-.]{20,})$",
    },
    "YOUTUBE_API_KEY": {
        "nombre": "YouTube (búsqueda)",
        "para": "Busca historias en canales de YouTube y canales por nombre. Opcional: sin ella se usa el RSS.",
        "obligatoria": False,
        "url": "https://console.cloud.google.com/apis/library/youtube.googleapis.com",
        "pasos": ["Pulsa «Habilitar» en la YouTube Data API v3.",
                  "Ve a Credenciales → Crear credenciales → Clave de API y cópiala.",
                  "Tiene que ser del mismo proyecto donde la habilitaste."],
        "patron": r"^AIza[0-9A-Za-z_\-]{35}$",
    },
    "PEXELS_API_KEY": {
        "nombre": "Pexels",
        "para": "Baja videos de fondo gratis. Opcional.",
        "obligatoria": False,
        "url": "https://www.pexels.com/api/new/",
        "pasos": ["Crea la cuenta (gratis, sin tarjeta) y rellena el formulario.",
                  "Copia la clave que aparece en «Your API Key»."],
        "patron": r"^[0-9A-Za-z]{40,64}$",
    },
    "PIXABAY_API_KEY": {
        "nombre": "Pixabay",
        "para": "Otra fuente de videos de fondo gratis. Opcional.",
        "obligatoria": False,
        "url": "https://pixabay.com/api/docs/",
        "pasos": ["Inicia sesión en Pixabay.",
                  "En esa página, la clave sale en el apartado «Parameters», junto a «key»."],
        "patron": r"^\d+-[0-9a-f]{20,40}$",
    },
    "JAMENDO_CLIENT_ID": {
        "nombre": "Jamendo",
        "para": "Baja música libre para los videos. Opcional.",
        "obligatoria": False,
        "url": "https://devportal.jamendo.com/admin/applications",
        "pasos": ["Crea una cuenta de desarrollador.",
                  "Crea una aplicación y copia su «Client ID»."],
        "patron": r"^[0-9a-f]{8}$",
    },
}

# Credenciales que no son una clave para pegar sino un permiso que hay que
# conceder en el navegador (OAuth). Eso no se puede hacer desde el panel:
# se lanza una vez en Termux y el panel solo dice si ya está.
AUTORIZACIONES = {
    "youtube_token.json": {
        "nombre": "Subir a YouTube",
        "para": "Permiso para subir videos a tu canal. Sin él no se publica nada.",
        "orden": "python generar_youtube_token.py",
    },
    "tiktok_token.json": {
        "nombre": "Subir a TikTok",
        "para": "Opcional: hoy la API de TikTok no aporta nada (ver AUDITORIA_TIKTOK.md).",
        "orden": "python generar_tiktok_token.py",
    },
}

TIEMPO = 12


def revisar_formato(clave, valor):
    """(severidad, mensaje) o None. Lo mismo que pinta el panel al escribir.

    Un formato raro es solo un aviso: los proveedores cambian el formato de
    sus claves (Google ya lo hizo) y esto no puede ser lo que lo impida. Lo
    que decide es la prueba contra el servicio.
    """
    valor = (valor or "").strip().strip('"').strip("'")
    if not valor:
        return ("error", "Está vacía.")
    if "..." in valor or "…" in valor or " " in valor:
        return ("error", "Tiene espacios o puntos suspensivos: parece el texto de ejemplo o una copia a medias.")
    if clave in ("GEMINI_API_KEY", "YOUTUBE_API_KEY"):
        problema = secretos.revisar_clave_api(valor)
        if problema and problema[0] == "error":
            return problema
    patron = (SERVICIOS.get(clave) or {}).get("patron")
    if patron and not re.match(patron, valor):
        return ("aviso", "No tiene la forma habitual; la pruebo igual.")
    return None


def _explicar(clave, codigo, texto):
    t = (texto or "").lower()
    if "quota" in t:
        return "El servicio dice que la cuota está agotada. La clave puede estar bien: prueba más tarde."
    if "has not been used" in t or "service_disabled" in t or "accessnotconfigured" in t or "disabled" in t:
        return "La clave vale, pero la API no está habilitada en ese proyecto. Pulsa «Habilitar» en el enlace."
    if codigo in (400, 401, 403):
        return "El servicio no acepta esa clave. Cópiala otra vez, entera."
    return f"El servicio contestó {codigo}."


def probar(clave, valor):
    """{"ok": bool, "mensaje": str, "comprobada": bool}.

    comprobada=False cuando no se pudo preguntar (sin red, sin requests, un
    servicio sin prueba): ahí la clave se guarda igual, avisando.
    """
    valor = (valor or "").strip().strip('"').strip("'")
    if requests is None:
        return {"ok": True, "comprobada": False, "mensaje": "No pude probarla (falta requests); guardada igual."}

    if clave == "GEMINI_API_KEY":
        pedir = lambda: requests.get("https://generativelanguage.googleapis.com/v1beta/models",
                                     params={"key": valor, "pageSize": 1}, timeout=TIEMPO)
    elif clave == "YOUTUBE_API_KEY":
        pedir = lambda: requests.get("https://www.googleapis.com/youtube/v3/videos",
                                     params={"part": "id", "chart": "mostPopular", "maxResults": 1,
                                             "regionCode": "MX", "key": valor}, timeout=TIEMPO)
    elif clave == "PEXELS_API_KEY":
        pedir = lambda: requests.get("https://api.pexels.com/videos/search",
                                     params={"query": "rain", "per_page": 1},
                                     headers={"Authorization": valor}, timeout=TIEMPO)
    elif clave == "PIXABAY_API_KEY":
        pedir = lambda: requests.get("https://pixabay.com/api/videos/",
                                     params={"key": valor, "q": "rain", "per_page": 3}, timeout=TIEMPO)
    elif clave == "JAMENDO_CLIENT_ID":
        pedir = lambda: requests.get("https://api.jamendo.com/v3.0/tracks/",
                                     params={"client_id": valor, "limit": 1, "format": "json"}, timeout=TIEMPO)
    else:
        return {"ok": True, "comprobada": False,
                "mensaje": "Guardada. Este servicio no lo conozco, así que no la pude probar."}

    try:
        r = pedir()
    except requests.RequestException:
        return {"ok": True, "comprobada": False,
                "mensaje": "Sin conexión con el servicio ahora mismo; guardada igual, se probará al usarla."}

    # Jamendo contesta 200 también con una clave mala: el error va dentro.
    if clave == "JAMENDO_CLIENT_ID" and r.status_code == 200:
        try:
            cab = r.json().get("headers", {})
        except ValueError:
            cab = {}
        if cab.get("status") != "success":
            return {"ok": False, "comprobada": True,
                    "mensaje": "Jamendo no reconoce ese Client ID. Cópialo otra vez."}

    if r.status_code == 200:
        return {"ok": True, "comprobada": True, "mensaje": "Funciona. Guardada."}
    if r.status_code == 429:
        # Cuota agotada: la clave casi seguro vale, solo que no ahora.
        return {"ok": True, "comprobada": False,
                "mensaje": "El servicio dice que la cuota está agotada por ahora; guardada igual."}
    return {"ok": False, "comprobada": True, "mensaje": _explicar(clave, r.status_code, r.text[:2000])}
