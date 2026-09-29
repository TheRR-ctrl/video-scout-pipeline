"""
El diccionario de errores: qué significa lo que sale en rojo y qué hacer.

Cada tarea del panel escribe su salida tal cual la dan las librerías: un 429
de Google, un `invalid_grant`, un `No space left on device`. Quien lo lee
desde el teléfono no tiene por qué saber qué es cada cosa ni en qué pantalla
se arregla. Aquí se empareja esa salida con una explicación en castellano
llano, y el panel la enseña debajo del trabajo (y en «📖 Errores» de la Cola,
para consultarla sin haber fallado nada).

Añadir una entrada: un "id" nuevo, los "patrones" (expresiones regulares,
sin distinguir mayúsculas) que lo delatan en la salida, y las tres frases.
Van de lo más concreto a lo más general: si un 429 de Gemini y un "429"
suelto casan los dos, gana el primero, y el genérico no se repite detrás.
"ir" es la pestaña del panel donde se arregla, si la hay.

    python errores.py            # lista el diccionario entero
    python errores.py log.txt    # dice qué entradas casan con ese log
"""
import re
import sys

CATALOGO = [
    # ---- Gemini (guiones, títulos, revisión) ------------------------------
    {"id": "gemini_cuota", "tema": "Gemini",
     "patrones": [r"RESOURCE_EXHAUSTED", r"exceeded your current quota",
                  r"agotó la cuota de Gemini", r"generativelanguage.*429", r"429.*generativelanguage"],
     "titulo": "Gemini: se acabó la cuota del día",
     "que_pasa": "La clave funciona, pero el plan gratis ya gastó lo que permite hoy.",
     "que_hacer": "Esperar a que se renueve (una vez al día) y volver a pulsar. "
                  "Crear otra clave en el mismo proyecto no sirve: la cuota es por proyecto. "
                  "Con facturación activada en ese proyecto desaparece el tope. "
                  "Los guiones ya pasan solos por los otros modelos gratis de Gemini, cada "
                  "uno con su propia cuota: si ves esto al escribir guiones, se agotaron todos."},
    {"id": "gemini_saturado", "tema": "Gemini",
     "patrones": [r"\b503\b.*(gemini|generativelanguage)", r"UNAVAILABLE", r"model is overloaded",
                  r"high demand"],
     "titulo": "Gemini está saturado",
     "que_pasa": "Los servidores de Google tienen demasiada gente ahora mismo. No es tu clave.",
     "que_hacer": "Nada que arreglar: el pipeline ya reintenta solo. Si falla igual, "
                  "vuelve a pulsar en unos minutos."},
    {"id": "gemini_clave_mala", "tema": "Gemini",
     "patrones": [r"API_KEY_INVALID", r"API key not valid", r"GEMINI_API_KEY no es válida"],
     "titulo": "Gemini: la clave no vale",
     "que_pasa": "Google no reconoce esa clave: está mal copiada, borrada o es de otro servicio.",
     "que_hacer": "Saca una nueva en aistudio.google.com/apikey y pégala en "
                  "Ajustes → Conectar servicios → Gemini (allí se prueba antes de guardarla).",
     "ir": "ajustes"},
    {"id": "gemini_sin_permiso", "tema": "Gemini",
     "patrones": [r"SERVICE_DISABLED", r"PERMISSION_DENIED", r"API_KEY_\w+_BLOCKED",
                  r"has not been used in project"],
     "titulo": "Gemini: la clave no tiene permiso para esta API",
     "que_pasa": "La clave existe, pero la API de Gemini no está habilitada en su proyecto "
                 "o la clave está restringida a otras APIs.",
     "que_hacer": "Lo más rápido: crear la clave en aistudio.google.com/apikey, que ya la deja "
                  "habilitada, y pegarla en Ajustes → Conectar servicios.",
     "ir": "ajustes"},
    {"id": "gemini_falta", "tema": "Gemini",
     "patrones": [r"Falta GEMINI_API_KEY", r"GEMINI_API_KEY.*(no está|vac[ií]a|falta)", r"UNAUTHENTICATED"],
     "titulo": "Falta la clave de Gemini",
     "que_pasa": "Sin ella no se pueden escribir guiones ni títulos.",
     "que_hacer": "Ajustes → Conectar servicios → Gemini. Es gratis y lleva un minuto.",
     "ir": "ajustes"},
    {"id": "gemini_bloqueo", "tema": "Gemini",
     "patrones": [r"finish_?reason.*SAFETY", r"blocked.*safety", r"PROHIBITED_CONTENT", r"blockReason"],
     "titulo": "Gemini se negó a escribir esa historia",
     "que_pasa": "Su filtro de contenido consideró la historia delicada (violencia, menores, etc.).",
     "que_hacer": "Nada: esa historia se salta y siguen las demás. Si pasa con muchas, "
                  "quita de las fuentes el subreddit o canal que las trae."},

    # ---- Búsqueda: Reddit y YouTube ---------------------------------------
    {"id": "reddit_bloqueo", "tema": "Búsqueda",
     "patrones": [r"429 de reddit", r"Reddit no respondió", r"reddit\.com.*(429|403)",
                  r"(429|403).*reddit\.com"],
     "titulo": "Reddit está frenando las búsquedas",
     "que_pasa": "Reddit limita cuántas veces se le pregunta seguido (429/403). "
                 "Es temporal y no es un bloqueo de tu cuenta: no se usa ninguna.",
     "que_hacer": "Espera un rato (media hora suele bastar) y vuelve a buscar. "
                  "Buscar en YouTube mientras tanto no choca con esto."},
    {"id": "reddit_todo_usado", "tema": "Búsqueda",
     "patrones": [r"Casi todo el /top/ del día ya se usó"],
     "titulo": "Lo mejor de hoy en Reddit ya lo usaste",
     "que_pasa": "Los posts que Reddit enseña hoy ya se convirtieron en guion antes.",
     "que_hacer": "Añade subreddits en Ajustes → Más opciones → Fuentes, o espera a mañana.",
     "ir": "ajustes"},
    {"id": "busqueda_nada_nuevo", "tema": "Búsqueda",
     "patrones": [r"No hay nada nuevo que cumpla los filtros", r"NUEVOS\s*:\s*0\b"],
     "titulo": "La búsqueda no trajo nada nuevo",
     "que_pasa": "Se leyeron posts o videos, pero todos estaban ya usados, en la cola, "
                 "o eran demasiado cortos o largos. El detalle dice cuántos de cada.",
     "que_hacer": "Si hay candidatos esperando, pulsa «Escribir guiones». Si no, añade fuentes "
                  "en Ajustes → Más opciones → Fuentes o prueba mañana."},
    {"id": "youtube_api_cuota", "tema": "Búsqueda",
     "patrones": [r"quotaExceeded", r"dailyLimitExceeded"],
     "titulo": "YouTube: se acabó la cuota de la API del día",
     "que_pasa": "La clave de YouTube gastó sus unidades diarias (la búsqueda gasta muchas).",
     "que_hacer": "Espera a mañana. Mientras, la búsqueda en YouTube cae sola al RSS, "
                  "que ve menos videos pero funciona."},
    {"id": "youtube_transcripcion", "tema": "Búsqueda",
     "patrones": [r"RequestBlocked", r"IpBlocked", r"TranscriptsDisabled", r"NoTranscriptFound",
                  r"bloqueados por YouTube\s*:\s*[1-9]"],
     "titulo": "YouTube no dejó leer los subtítulos",
     "que_pasa": "Algunos videos no tienen subtítulos, o YouTube bloquea por un rato las "
                 "lecturas desde tu conexión.",
     "que_hacer": "Nada urgente: esos videos se saltan. Si son todos, espera unas horas "
                  "o prueba con otra red (WiFi en vez de datos o al revés)."},

    # ---- Subir a YouTube --------------------------------------------------
    {"id": "youtube_limite_subidas", "tema": "Subida",
     "patrones": [r"uploadLimitExceeded", r"límite diario de subidas"],
     "titulo": "YouTube: límite de subidas del día",
     "que_pasa": "YouTube no deja subir más videos hoy desde este canal. No se pierde nada.",
     "que_hacer": "Nada: lo que falta se sube solo en la siguiente vuelta de «Publicar»."},
    {"id": "youtube_token", "tema": "Subida",
     "patrones": [r"invalid_grant", r"Token has been expired or revoked", r"RefreshError",
                  r"youtube_token\.json.*(no existe|falta|No such file)"],
     "titulo": "YouTube: hay que volver a dar permiso",
     "que_pasa": "El permiso para subir a tu canal caducó o se retiró (pasa si la app de Google "
                 "Cloud sigue en modo «Prueba», o si cambiaste la contraseña).",
     "que_hacer": "En Termux: python generar_youtube_token.py y sigue el enlace. "
                  "Para que no caduque cada semana, pon la app en «En producción» "
                  "(ver README, «Generating the YOUTUBE_TOKEN secret»).",
     "ir": "ajustes"},
    {"id": "youtube_forbidden", "tema": "Subida",
     "patrones": [r"forbidden.*youtube", r"insufficientPermissions", r"youtubeSignupRequired"],
     "titulo": "YouTube rechazó la subida por permisos",
     "que_pasa": "La cuenta con la que diste permiso no tiene canal, o el permiso no incluye subir.",
     "que_hacer": "Vuelve a sacar el permiso con python generar_youtube_token.py, "
                  "entrando con la cuenta que tiene el canal."},

    # ---- Grabar (render) --------------------------------------------------
    {"id": "historia_larga", "tema": "Grabar",
     "patrones": [r"aplazad[oa]", r"los largos están bloqueados"],
     "titulo": "Historia aplazada por larga",
     "que_pasa": "No cabe en un short, y los videos largos siguen cerrados hasta tener "
                 "suscriptores suficientes. No es un fallo.",
     "que_hacer": "En la Cola: «✂ Partir» para sacarla en varios shorts, o «📦 Archivar» "
                  "para guardarla hasta que se abran los largos.",
     "ir": "cola"},
    {"id": "voz_fallo", "tema": "Grabar",
     "patrones": [r"NoAudioReceived", r"edge_tts", r"No audio was received", r"WSServerHandshakeError"],
     "titulo": "No se pudo generar la voz",
     "que_pasa": "El servicio de voz de Microsoft no contestó. Necesita internet.",
     "que_hacer": "Comprueba la conexión y vuelve a grabar. Si persiste unas horas, "
                  "suele ser una caída del servicio; se arregla solo."},
    {"id": "ffmpeg_falta", "tema": "Grabar",
     "patrones": [r"ffmpeg: (command )?not found", r"No such file or directory: 'ffmpeg'",
                  r"ffprobe: (command )?not found"],
     "titulo": "Falta ffmpeg",
     "que_pasa": "Es el programa que monta el video, y no está instalado en Termux.",
     "que_hacer": "En Termux: pkg install ffmpeg"},
    {"id": "sin_musica", "tema": "Grabar",
     "patrones": [r"No hay pistas", r"sin fondo musical", r"sin música"],
     "titulo": "No hay música descargada",
     "que_pasa": "Los videos saldrán solo con la voz.",
     "que_hacer": "Ajustes → Música y fondos → «Rellenar». Necesita la clave de Jamendo "
                  "(Ajustes → Conectar servicios).",
     "ir": "ajustes"},
    {"id": "sin_fondos", "tema": "Grabar",
     "patrones": [r"No hay (videos de )?fondos?", r"sin fondos?\b", r"Plantilla: falta"],
     "titulo": "No hay videos de fondo",
     "que_pasa": "El render necesita al menos un clip de fondo en la carpeta.",
     "que_hacer": "Ajustes → Música y fondos → «Re-enlazar material», o bajar fondos de Pexels "
                  "(necesita su clave en Conectar servicios).",
     "ir": "ajustes"},

    # ---- El teléfono ------------------------------------------------------
    {"id": "sin_espacio", "tema": "Teléfono",
     "patrones": [r"No space left on device", r"ENOSPC", r"Errno 28"],
     "titulo": "El teléfono se quedó sin espacio",
     "que_pasa": "No cabe el archivo que se estaba escribiendo; lo que salió a medias no sirve.",
     "que_hacer": "Libera espacio: en Revisar, borra videos ya subidos; en la Cola, «Limpiar»; "
                  "y en Ajustes → Más opciones, «Recomprimir» los que pesan de más. "
                  "Luego vuelve a lanzar lo que falló."},
    {"id": "sin_wifi", "tema": "Teléfono",
     "patrones": [r"sin WiFi(?!, pero)", r"solo con WiFi"],
     "titulo": "Esperando WiFi",
     "que_pasa": "Está puesto para no gastar datos móviles, y ahora no hay WiFi. No es un fallo.",
     "que_hacer": "Conéctate a una WiFi, o apaga «Solo subir con WiFi» en Ajustes → Subida "
                  "si no te importa gastar datos.",
     "ir": "ajustes"},
    {"id": "sin_red", "tema": "Teléfono",
     "patrones": [r"NameResolutionError", r"Temporary failure in name resolution",
                  r"Failed to resolve", r"Max retries exceeded", r"ConnectionError",
                  r"Network is unreachable", r"Read timed out", r"ConnectTimeout"],
     "titulo": "Sin conexión a internet (o muy lenta)",
     "que_pasa": "No se pudo llegar al servicio. Suele ser la red, no el pipeline.",
     "que_hacer": "Comprueba que hay internet y vuelve a pulsar. Si usas datos y el ahorro "
                  "de datos de Android está puesto, puede estar frenando a Termux."},
    {"id": "proceso_matado", "tema": "Teléfono",
     "patrones": [r"\bKilled\b", r"señal 9", r"SIGKILL", r"MemoryError"],
     "titulo": "Android cortó el proceso",
     "que_pasa": "El sistema lo mató por falta de memoria o por ahorro de batería.",
     "que_hacer": "Quita a Termux la optimización de batería (Ajustes de Android → Apps → Termux "
                  "→ Batería → Sin restricciones), cierra otras apps pesadas y vuelve a lanzarlo."},
    {"id": "falta_modulo", "tema": "Teléfono",
     "patrones": [r"ModuleNotFoundError: No module named '([\w.]+)'", r"ImportError"],
     "titulo": "Falta una librería de Python",
     "que_pasa": "Una actualización trajo una dependencia que aún no está instalada.",
     "que_hacer": "En Termux: cd ~/video-scout-pipeline && pip install -r requirements.txt"},

    # ---- Otros servicios --------------------------------------------------
    {"id": "jamendo", "tema": "Servicios",
     "patrones": [r"Jamendo no reconoce", r"JAMENDO_CLIENT_ID.*(falta|no)"],
     "titulo": "Jamendo: falta la clave o no vale",
     "que_pasa": "Sin ella no se descarga música nueva.",
     "que_hacer": "Sácala en devportal.jamendo.com/admin/applications y pégala en "
                  "Ajustes → Conectar servicios → Jamendo.",
     "ir": "ajustes"},
    {"id": "pexels", "tema": "Servicios",
     "patrones": [r"api\.pexels\.com.*\b(401|403)\b", r"PEXELS_API_KEY.*(falta|no)"],
     "titulo": "Pexels: falta la clave o no vale",
     "que_pasa": "Sin ella no se bajan fondos nuevos de Pexels.",
     "que_hacer": "Ajustes → Conectar servicios → Pexels, que trae el enlace y la prueba.",
     "ir": "ajustes"},

    # ---- Lo genérico va al final ------------------------------------------
    {"id": "limite_generico", "tema": "General",
     "patrones": [r"\b429\b", r"Too Many Requests", r"rate.?limit"],
     "titulo": "Un servicio pidió que se le pregunte más despacio",
     "que_pasa": "Demasiadas peticiones seguidas (429). Es temporal.",
     "que_hacer": "Espera un rato y vuelve a intentarlo."},
]

_COMPILADOS = [(e, [re.compile(p, re.IGNORECASE) for p in e["patrones"]]) for e in CATALOGO]
# Los temas a los que pisa lo genérico: si ya se explicó el 429 de Gemini,
# el "429" suelto sobra.
_GENERICOS = {"limite_generico"}


def _publica(e):
    return {k: v for k, v in e.items() if k != "patrones"}


def explicar(lineas, maximo=3):
    """Las entradas del diccionario que casan con esta salida, sin repetir.

    Recibe las líneas del trabajo (o un texto). Devuelve como mucho `maximo`,
    en el orden del catálogo: lo concreto antes que lo genérico.
    """
    texto = lineas if isinstance(lineas, str) else "\n".join(lineas or [])
    if not texto:
        return []
    halladas = []
    for e, pats in _COMPILADOS:
        if e["id"] in _GENERICOS and halladas:
            continue
        if any(p.search(texto) for p in pats):
            halladas.append(_publica(e))
            if len(halladas) >= maximo:
                break
    return halladas


def catalogo():
    """El diccionario entero, para consultarlo en el panel."""
    return [_publica(e) for e in CATALOGO]


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if argv:
        with open(argv[0], encoding="utf-8", errors="replace") as f:
            entradas = explicar(f.read(), maximo=len(CATALOGO))
        if not entradas:
            print("\n  Nada del diccionario casa con ese log.\n")
    else:
        entradas = catalogo()
    for e in entradas:
        print(f"\n  [{e['tema']}] {e['titulo']}\n    Qué pasa : {e['que_pasa']}\n    Qué hacer: {e['que_hacer']}")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
