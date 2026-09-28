"""
Cliente mínimo de Gemini, hablando con su API REST por `requests`.

**Por qué existe.** Sustituye a `google-genai`, que pedía `pydantic` y con él
`pydantic-core`: una extensión compilada en Rust. Eso sobra en Termux y hace
imposible meter el pipeline dentro del APK de Android
(`docs/repos_revisados.md` §11.4), porque `pydantic-core` no tiene rueda de
Android. `requests` ya era dependencia de este proyecto.

**Qué NO es.** No es un puerto de `google-genai`: es exactamente la porción
que este repositorio usa, que resultó ser pequeña — generar contenido con
instrucción de sistema y respuesta JSON con esquema, y subir/borrar un
archivo. Si algún día hace falta algo más (streaming, conteo de tokens,
embeddings), se añade aquí, no se vuelve al SDK.

**Por qué imita los nombres del SDK.** `Client`, `types.GenerateContentConfig`,
`types.Part`, `errors.APIError` se llaman igual que allí a propósito: así los
ocho archivos que ya llamaban a Gemini solo cambian la línea del `import`, y
las llamadas de verdad —las que hay que leer para entender el pipeline— se
quedan como estaban.

**El detalle que sostiene todo lo demás.** Todo el manejo de errores de este
proyecto mira el *texto* de la excepción: `motivo_error_gemini` busca
`API_KEY_INVALID` o `PERMISSION_DENIED`, y `hyperframes_broll` busca
`RESOURCE_EXHAUSTED` y el `retryDelay` que sugiere la API. Por eso
`APIError.__str__` devuelve el código HTTP y el **cuerpo JSON crudo** que
mandó Google: esas cadenas vienen dentro y todo ese código sigue
funcionando sin tocarlo. Si alguien "limpia" ese mensaje, rompe el
reintento por cuota y la detección de clave inválida a la vez, y en
silencio.
"""
import base64
import json
import mimetypes
import os

import requests

BASE = "https://generativelanguage.googleapis.com/v1beta"
# La subida de archivos va por otra ruta del mismo servidor. Se deriva de
# BASE y no se escribe aparte para que cambiar una cambie las dos —y para
# poder apuntarlas a un servidor de prueba.
BASE_SUBIDA = BASE.replace("/v1beta", "/upload/v1beta")

# Analizar un video entero puede tardar; el de subida cubre archivos de
# decenas de MB por el móvil.
TIMEOUT_GENERAR = 300
TIMEOUT_SUBIR = 600


# =========================================================
# ERRORES
# =========================================================
class APIError(Exception):
    """Un error devuelto por la API.

    `str(exc)` incluye el cuerpo tal cual llegó: ver la nota de arriba sobre
    por qué eso no es descuido sino el contrato con el resto del pipeline.
    """

    def __init__(self, codigo, cuerpo):
        self.code = codigo          # mismo nombre que en el SDK
        self.cuerpo = cuerpo
        super().__init__(f"{codigo} {cuerpo}")


class _Errores:
    APIError = APIError


errors = _Errores()


# =========================================================
# TIPOS
# =========================================================
class Part:
    """Un trozo de contenido que no es texto (aquí, imágenes en memoria)."""

    def __init__(self, datos, mime_type):
        self.datos = datos
        self.mime_type = mime_type

    @classmethod
    def from_bytes(cls, data, mime_type):
        return cls(data, mime_type)

    def _a_rest(self):
        return {"inlineData": {"mimeType": self.mime_type,
                               "data": base64.b64encode(self.datos).decode("ascii")}}


class GenerateContentConfig:
    """Los tres ajustes que este proyecto usa. Nada más, a propósito."""

    def __init__(self, system_instruction=None, response_mime_type=None,
                 response_schema=None):
        self.system_instruction = system_instruction
        self.response_mime_type = response_mime_type
        self.response_schema = response_schema


class _Tipos:
    GenerateContentConfig = GenerateContentConfig
    Part = Part


types = _Tipos()


class _Archivo:
    """Un archivo subido. `state` es una cadena, y eso basta:
    quien lo mira hace getattr(state, "name", str(state))."""

    def __init__(self, datos):
        self._datos = datos or {}
        self.name = self._datos.get("name")
        self.uri = self._datos.get("uri")
        self.mime_type = self._datos.get("mimeType")
        self.state = self._datos.get("state")

    def _a_rest(self):
        return {"fileData": {"fileUri": self.uri, "mimeType": self.mime_type}}


class _Respuesta:
    def __init__(self, datos):
        self._datos = datos

    @property
    def text(self):
        """El texto de la primera respuesta, que es lo único que se lee aquí."""
        candidatos = self._datos.get("candidates") or []
        if not candidatos:
            # Pasa cuando el filtro de seguridad corta la respuesta entera.
            # Se levanta como APIError para que caiga donde ya se atrapa.
            raise APIError(200, json.dumps(self._datos)[:2000])
        partes = ((candidatos[0].get("content") or {}).get("parts")) or []
        return "".join(p.get("text", "") for p in partes)


# =========================================================
# CONVERSIÓN A LO QUE ESPERA EL REST
# =========================================================
def _tipos_en_mayusculas(esquema):
    """El REST quiere los tipos del esquema en mayúsculas; el SDK los
    normalizaba por su cuenta.

    Es la única diferencia real entre los `SCHEMA_*` que ya hay escritos en
    el repo —en minúsculas, estilo JSON Schema— y lo que la API acepta. Sin
    esto Google responde 400 sin explicar cuál de los campos le molesta.
    """
    if isinstance(esquema, dict):
        salida = {}
        for clave, valor in esquema.items():
            if clave == "type" and isinstance(valor, str):
                salida[clave] = valor.upper()
            else:
                salida[clave] = _tipos_en_mayusculas(valor)
        return salida
    if isinstance(esquema, list):
        return [_tipos_en_mayusculas(v) for v in esquema]
    return esquema


def _parte(trozo):
    if isinstance(trozo, str):
        return {"text": trozo}
    if hasattr(trozo, "_a_rest"):
        return trozo._a_rest()
    raise TypeError(f"No sé mandar esto a Gemini: {type(trozo).__name__}")


def _contenidos(contents):
    trozos = contents if isinstance(contents, (list, tuple)) else [contents]
    return [{"role": "user", "parts": [_parte(t) for t in trozos]}]


# =========================================================
# CLIENTE
# =========================================================
class _Modelos:
    def __init__(self, cliente):
        self._c = cliente

    def generate_content(self, model=None, contents=None, config=None):
        cuerpo = {"contents": _contenidos(contents)}

        if config is not None:
            if config.system_instruction:
                cuerpo["systemInstruction"] = {
                    "parts": [{"text": config.system_instruction}]
                }
            generacion = {}
            if config.response_mime_type:
                generacion["responseMimeType"] = config.response_mime_type
            if config.response_schema:
                generacion["responseSchema"] = _tipos_en_mayusculas(config.response_schema)
            if generacion:
                cuerpo["generationConfig"] = generacion

        # El nombre del modelo puede venir con o sin "models/" delante.
        nombre = model if str(model).startswith("models/") else f"models/{model}"
        datos = self._c._pedir(
            "POST", f"/{nombre}:generateContent", json=cuerpo,
            timeout=TIMEOUT_GENERAR,
        )
        return _Respuesta(datos)


class _Archivos:
    def __init__(self, cliente):
        self._c = cliente

    def upload(self, file=None):
        """Sube un archivo con el protocolo reanudable, que es el único que
        la API de archivos acepta.

        Son dos peticiones: una que anuncia el tamaño y devuelve una URL en
        una cabecera, y otra que manda los bytes a esa URL.
        """
        ruta = file
        tam = os.path.getsize(ruta)
        mime = mimetypes.guess_type(ruta)[0] or "application/octet-stream"

        inicio = requests.post(
            BASE_SUBIDA + "/files",
            headers={
                **self._c._cabeceras(),
                "X-Goog-Upload-Protocol": "resumable",
                "X-Goog-Upload-Command": "start",
                "X-Goog-Upload-Header-Content-Length": str(tam),
                "X-Goog-Upload-Header-Content-Type": mime,
                "Content-Type": "application/json",
            },
            json={"file": {"display_name": os.path.basename(ruta)}},
            timeout=60,
        )
        if inicio.status_code >= 400:
            raise APIError(inicio.status_code, inicio.text)

        destino = inicio.headers.get("X-Goog-Upload-URL")
        if not destino:
            raise APIError(inicio.status_code,
                           "La API no devolvió X-Goog-Upload-URL al empezar la subida")

        with open(ruta, "rb") as f:
            fin = requests.post(
                destino,
                headers={
                    "Content-Length": str(tam),
                    "X-Goog-Upload-Offset": "0",
                    "X-Goog-Upload-Command": "upload, finalize",
                },
                data=f,
                timeout=TIMEOUT_SUBIR,
            )
        if fin.status_code >= 400:
            raise APIError(fin.status_code, fin.text)

        return _Archivo((fin.json() or {}).get("file"))

    def get(self, name=None):
        return _Archivo(self._c._pedir("GET", f"/{name}", timeout=60))

    def delete(self, name=None):
        self._c._pedir("DELETE", f"/{name}", timeout=60)


class Client:
    """Se construye sin argumentos, igual que el del SDK: la clave sale del
    entorno, que es donde `secretos.py` la deja."""

    def __init__(self, api_key=None):
        self.api_key = (api_key
                        or os.environ.get("GEMINI_API_KEY")
                        or os.environ.get("GOOGLE_API_KEY"))
        if not self.api_key:
            raise ValueError(
                "Falta GEMINI_API_KEY. Va en secretos.env, nunca en el código "
                "ni en la línea de comandos."
            )
        self.models = _Modelos(self)
        self.files = _Archivos(self)

    def _cabeceras(self):
        # En cabecera y no en ?key=: así la clave no acaba en los registros
        # de ningún intermediario ni en un mensaje de error con la URL.
        return {"x-goog-api-key": self.api_key}

    def _pedir(self, metodo, camino, json=None, timeout=60):
        resp = requests.request(
            metodo, BASE + camino,
            headers={**self._cabeceras(), "Content-Type": "application/json"},
            json=json, timeout=timeout,
        )
        if resp.status_code >= 400:
            # El cuerpo crudo: ahí vienen API_KEY_INVALID, RESOURCE_EXHAUSTED
            # y retryDelay, que es lo que lee el resto del pipeline.
            raise APIError(resp.status_code, resp.text)
        if not resp.content:
            return {}
        return resp.json()
