"""Cliente mínimo da API HTTP do OpenObserve, só com a biblioteca padrão.

Roda no notebook do operador (pelo túnel SSH) ou no Python do host da VPS, sem dependências. Só
as operações que o provisionamento e o relatório usam, com os caminhos da v1.0.4 (a versão
fixada no compose.yaml). Mensagens de erro trazem método, caminho, status e a mensagem do
OpenObserve, nunca a credencial.
"""

import base64
import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any

TIMEOUT_S = 15.0
HTTP_NOT_FOUND = 404
# Linhas por consulta: as do provisionamento e do relatório devolvem uma ou poucas linhas.
SEARCH_SIZE = 1000
ERROR_DETAIL_CHARS = 500


class OpenObserveError(Exception):
    """Falha numa chamada à API, com o que o OpenObserve respondeu."""


@dataclass(frozen=True)
class Credentials:
    email: str
    password: str = field(repr=False)

    def basic_auth(self) -> str:
        token = base64.b64encode(f"{self.email}:{self.password}".encode()).decode()
        return f"Basic {token}"


class OpenObserve:
    def __init__(self, base_url: str, credentials: Credentials, org: str = "default") -> None:
        self._base_url = base_url.rstrip("/")
        self._auth = credentials.basic_auth()
        self._org = urllib.parse.quote(org, safe="")

    @property
    def base_url(self) -> str:
        return self._base_url

    # --- Saúde, busca e ingestão --------------------------------------------------------------

    def healthy(self) -> bool:
        try:
            self._request("GET", "/healthz")
        except (OpenObserveError, OSError):
            return False
        return True

    def search(
        self, sql: str, stream_type: str, start_us: int, end_us: int
    ) -> list[dict[str, Any]]:
        """Linhas da consulta SQL no intervalo [start_us, end_us), em microssegundos."""
        body = {
            "query": {
                "sql": sql,
                "start_time": start_us,
                "end_time": end_us,
                "from": 0,
                "size": SEARCH_SIZE,
            }
        }
        result = self._request("POST", f"/api/{self._org}/_search", {"type": stream_type}, body)
        hits: list[dict[str, Any]] = result.get("hits", [])
        return hits

    def stream_fields(self, stream: str, stream_type: str) -> set[str]:
        """Campos do schema do stream; vazio se o stream ainda não existe."""
        path = f"/api/{self._org}/streams/{_segment(stream)}/schema"
        result = self._optional("GET", path, {"type": stream_type})
        if result is None:
            return set()
        return {str(item["name"]) for item in result.get("schema", [])}

    def ingest_traces(self, stream: str, resource_spans: list[dict[str, Any]]) -> None:
        """Spans em OTLP/JSON, pelo mesmo endpoint que o Collector usa."""
        self._request(
            "POST",
            f"/api/{self._org}/v1/traces",
            body={"resourceSpans": resource_spans},
            headers={"stream-name": stream},
        )

    # --- Templates e destinos (identificados pelo nome) ---------------------------------------

    def template(self, name: str) -> dict[str, Any] | None:
        return self._optional("GET", f"/api/{self._org}/alerts/templates/{_segment(name)}")

    def save_template(self, name: str, body: str) -> bool:
        """Cria ou substitui; devolve True se criou."""
        payload = {"name": name, "type": "http", "body": body}
        return self._upsert(f"/api/{self._org}/alerts/templates", name, payload)

    def destination(self, name: str) -> dict[str, Any] | None:
        return self._optional("GET", f"/api/{self._org}/alerts/destinations/{_segment(name)}")

    def save_destination(self, name: str, url: str, template: str) -> bool:
        """Webhook HTTP POST com o template dado; cria ou substitui, True se criou."""
        payload = {
            "name": name,
            "type": "http",
            "url": url,
            "method": "post",
            "template": template,
            "skip_tls_verify": False,
        }
        return self._upsert(f"/api/{self._org}/alerts/destinations", name, payload)

    # --- Alertas (a API v2 os identifica por id; o provisionamento, pelo nome) ----------------

    def alerts_named(self, prefix: str) -> dict[str, str]:
        """Nome → id dos alertas cujo nome começa com o prefixo."""
        query = {"alert_name_substring": prefix, "page_size": str(SEARCH_SIZE)}
        result = self._request("GET", f"/api/v2/{self._org}/alerts", query)
        return {
            str(item["name"]): str(item["alert_id"])
            for item in result.get("list", [])
            if str(item["name"]).startswith(prefix)
        }

    def alert(self, alert_id: str) -> dict[str, Any]:
        return self._request("GET", f"/api/v2/{self._org}/alerts/{_segment(alert_id)}")

    def create_alert(self, payload: dict[str, Any]) -> None:
        self._request("POST", f"/api/v2/{self._org}/alerts", body=payload)

    def update_alert(self, alert_id: str, payload: dict[str, Any]) -> None:
        self._request("PUT", f"/api/v2/{self._org}/alerts/{_segment(alert_id)}", body=payload)

    def delete_alert(self, alert_id: str) -> None:
        self._request("DELETE", f"/api/v2/{self._org}/alerts/{_segment(alert_id)}")

    # --- HTTP ----------------------------------------------------------------------------------

    def _upsert(self, collection: str, name: str, payload: dict[str, Any]) -> bool:
        item = f"{collection}/{_segment(name)}"
        if self._optional("GET", item) is None:
            self._request("POST", collection, body=payload)
            return True
        self._request("PUT", item, body=payload)
        return False

    def _optional(
        self, method: str, path: str, query: dict[str, str] | None = None
    ) -> dict[str, Any] | None:
        try:
            return self._request(method, path, query)
        except _NotFoundError:
            return None

    def _request(
        self,
        method: str,
        path: str,
        query: dict[str, str] | None = None,
        body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        where = f"{method} {self._base_url}{path}"
        url = self._base_url + path
        if query:
            url += "?" + urllib.parse.urlencode(query)
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(  # noqa: S310 (URL do operador, http ou https)
            url,
            data=data,
            method=method,
            headers={
                "Authorization": self._auth,
                "Content-Type": "application/json",
                **(headers or {}),
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:  # noqa: S310
                raw = response.read()
        except urllib.error.HTTPError as error:
            with error:
                detail = error.read().decode("utf-8", "replace")[:ERROR_DETAIL_CHARS]
            if error.code == HTTP_NOT_FOUND:
                raise _NotFoundError(f"{where}: HTTP 404: {detail}") from None
            raise OpenObserveError(f"{where}: HTTP {error.code}: {detail}") from None
        except urllib.error.URLError as error:
            raise OpenObserveError(f"{where}: {error.reason}") from None
        if not raw:
            return {}
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {"list": parsed}


class _NotFoundError(OpenObserveError):
    pass


def _segment(value: str) -> str:
    return urllib.parse.quote(value, safe="")
