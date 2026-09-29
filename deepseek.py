"""
DeepSeek como respaldo de Gemini, y solo con su crédito gratis.

Cuando Gemini contesta "cuota agotada" a media tanda, en vez de cortar la
escritura de guiones se sigue con DeepSeek. Pero **nunca con dinero**: antes
de cada llamada se pregunta el saldo (`GET /user/balance`, que no gasta
nada) y solo se usa si queda saldo *regalado* (`granted_balance`). Aunque
algún día se recargue la cuenta para otra cosa, este pipeline no toca ese
dinero: sin regalo, no hay llamada.

El regalo de DeepSeek es de una sola vez al crear la cuenta, no se renueva,
y puede caducar. Cuando se acaba, esto deja de usarse solo y el pipeline
vuelve a esperar a que se renueve la cuota de Gemini, como siempre.

**Por qué imita a gemini.py.** `ConRespaldo` se hace pasar por el cliente de
Gemini (`client.models.generate_content(...)` y `.text`), así que
`script_writer.py`, `partir_historias.py` y sus esquemas no cambian: el
cliente que reciben simplemente sabe seguir por otro lado.

**Por qué no el SDK de OpenAI** (DeepSeek habla su mismo protocolo): pide
`pydantic`, y con él `pydantic-core`, que es Rust sin rueda de Android — el
mismo muro que ya obligó a escribir gemini.py (docs/repos_revisados.md §11.4).
Con `requests` son cuarenta líneas.
"""
import json
import logging
import os

import requests

from gemini import APIError

logger = logging.getLogger(__name__)

BASE = "https://api.deepseek.com"
MODELO = "deepseek-flash"
TIMEOUT = 300
# Marca en el texto del error cuando el regalo se acabó. script_writer la
# busca para cortar la corrida con un motivo que diga de quién es la cuota.
SIN_CREDITO = "DEEPSEEK_SIN_CREDITO_GRATIS"


class _Respuesta:
    def __init__(self, texto):
        self.text = texto


class Cliente:
    def __init__(self, api_key=None):
        self.api_key = (api_key or os.environ.get("DEEPSEEK_API_KEY") or "").strip()
        if not self.api_key:
            raise ValueError("Falta DEEPSEEK_API_KEY (va en secretos.env).")
        self.models = self

    def _cabeceras(self):
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    def saldo(self):
        """{"regalado": float, "pagado": float, "moneda": str}. Lanza APIError si falla."""
        r = requests.get(BASE + "/user/balance", headers=self._cabeceras(), timeout=15)
        if r.status_code >= 400:
            raise APIError(r.status_code, f"DeepSeek: {r.text[:500]}")
        infos = (r.json() or {}).get("balance_infos") or []
        regalado = sum(float(i.get("granted_balance") or 0) for i in infos)
        pagado = sum(float(i.get("topped_up_balance") or 0) for i in infos)
        moneda = "/".join(sorted({i.get("currency", "") for i in infos})) or "?"
        return {"regalado": regalado, "pagado": pagado, "moneda": moneda}

    def generate_content(self, model=None, contents=None, config=None):
        # Cada llamada vuelve a mirar el regalo: la tanda puede agotarlo a
        # mitad, y a partir de ahí la siguiente tiraría del saldo pagado.
        if self.saldo()["regalado"] <= 0:
            raise APIError(402, f"{SIN_CREDITO}: se acabó el crédito gratis de DeepSeek.")

        if not isinstance(contents, str):
            raise TypeError("El respaldo de DeepSeek solo manda texto.")
        sistema = (getattr(config, "system_instruction", None) or "").strip()
        quiere_json = getattr(config, "response_mime_type", None) == "application/json"
        esquema = getattr(config, "response_schema", None)
        if quiere_json:
            # DeepSeek no acepta esquema: se le describe en el prompt. Y su
            # modo JSON exige que la palabra "json" aparezca en él.
            sistema += ("\n\nResponde SOLO con un objeto json válido, sin texto alrededor"
                        + (", que siga exactamente este esquema (mismos nombres de campo):\n"
                           + json.dumps(esquema, ensure_ascii=False) if esquema else "."))

        cuerpo = {
            "model": MODELO,
            "messages": ([{"role": "system", "content": sistema}] if sistema else [])
                        + [{"role": "user", "content": contents}],
            "max_tokens": 8192,
            "stream": False,
        }
        if quiere_json:
            cuerpo["response_format"] = {"type": "json_object"}

        # Su documentación avisa de que el modo JSON a veces devuelve vacío.
        for _ in range(2):
            r = requests.post(BASE + "/chat/completions", headers=self._cabeceras(),
                              json=cuerpo, timeout=TIMEOUT)
            if r.status_code == 402:
                raise APIError(402, f"{SIN_CREDITO}: {r.text[:500]}")
            if r.status_code >= 400:
                raise APIError(r.status_code, f"DeepSeek: {r.text[:1500]}")
            texto = (((r.json().get("choices") or [{}])[0].get("message") or {})
                     .get("content") or "").strip()
            if texto:
                return _Respuesta(texto)
        raise APIError(200, "DeepSeek devolvió una respuesta vacía dos veces.")


def cliente_gratis():
    """Un Cliente si hay clave y queda crédito regalado; si no, None (y por qué, al log)."""
    if not (os.environ.get("DEEPSEEK_API_KEY") or "").strip():
        return None
    try:
        c = Cliente()
        s = c.saldo()
    except Exception as exc:                      # noqa: BLE001 — el respaldo es opcional
        logger.warning(f"  DeepSeek no disponible como respaldo ({exc}).")
        return None
    if s["regalado"] <= 0:
        logger.info("  DeepSeek: el crédito gratis ya se gastó; no se usa (solo gratis).")
        return None
    return c


class ConRespaldo:
    """Se hace pasar por el cliente de Gemini y, si Gemini se queda sin
    cuota, sigue con DeepSeek mientras quede crédito gratis.

    `es_cuota(exc)` decide qué error de Gemini cuenta como cuota agotada; lo
    pone quien llama, que es quien ya sabe leer esos errores.
    """

    def __init__(self, principal, es_cuota):
        self._principal = principal
        self._es_cuota = es_cuota
        self._respaldo = None
        self.models = self
        self.files = getattr(principal, "files", None)

    @property
    def usando_respaldo(self):
        return self._respaldo is not None

    def generate_content(self, model=None, contents=None, config=None):
        if self._respaldo is None:
            try:
                return self._principal.models.generate_content(
                    model=model, contents=contents, config=config)
            except Exception as exc:
                if not self._es_cuota(exc):
                    raise
                respaldo = cliente_gratis()
                if respaldo is None:
                    raise
                logger.info("  Gemini sin cuota: sigo con DeepSeek (crédito gratis).")
                self._respaldo = respaldo
        return self._respaldo.generate_content(model=model, contents=contents, config=config)
