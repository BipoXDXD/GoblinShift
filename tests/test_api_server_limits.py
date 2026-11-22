"""Tetos de concorrência e parada graciosa do uvicorn da API (docker/app.Dockerfile).

O serviço `api` usa o CMD da imagem, sem `command` no Compose nem no `app.json`. Acima do teto do
`ConcurrencyLimitMiddleware`, a API responde 503 problem+json com Retry-After, em vez de enfileirar
pedidos que esperariam o pool do banco. O `--limit-concurrency` do uvicorn, que recusa com 503
`text/plain` antes do ASGI, fica acima do middleware com folga para a rajada do Nginx: só uma carga
fora do previsto chega a ele. O `--timeout-graceful-shutdown` precisa vencer antes do SIGKILL da
plataforma, senão a parada nunca é graciosa: o processo morre com pedidos e telemetria pela metade.
"""

import json
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from goblinshift.entrypoints.api.concurrency import MAX_CONCURRENT_REQUESTS
from goblinshift.platform.settings import Settings
from tests.test_container_logs import uvicorn_cmd
from tests.test_nginx_transfer_timeouts import location_directives

ROOT = Path(__file__).resolve().parent.parent
COMPOSE_BASE = ROOT / "compose.yaml"
COMPOSE_PROD = ROOT / "compose.prod.yaml"
COOLIFY_APP = ROOT / "deploy" / "coolify" / "app.json"
# Cada conexão do pool pode ter um pedido usando-a e outro esperando o `pool_timeout`; além disso,
# o middleware recusa, e o cliente tenta de novo depois do Retry-After.
CONCURRENCY_PER_POOL_CONNECTION = 2
# Sondas que o middleware não conta (/livez e /readyz) e que podem chegar juntas: o healthcheck do
# container, o do Nginx e o monitor externo.
EXEMPT_PROBE_CONNECTIONS = 3
# O uvicorn compara com `>=` contando a conexão atual: atende até `--limit-concurrency - 1`.
UVICORN_COUNTS_CURRENT_CONNECTION = 1
_API_BURST = re.compile(r"^limit_req zone=api burst=(\d+) nodelay;$")
# Prazo do Docker e do Compose entre o SIGTERM e o SIGKILL quando o serviço não declara
# `stop_grace_period`. A parada graciosa precisa caber nele, a menor carência considerada.
COMPOSE_DEFAULT_STOP_GRACE_SECONDS = 10
# Depois do prazo dos pedidos, o lifespan ainda fecha o pool do banco e exporta a telemetria.
LIFESPAN_SHUTDOWN_MARGIN_SECONDS = 2


def _option(name: str) -> int:
    cmd = uvicorn_cmd()
    assert name in cmd, f"{name} ausente do CMD da API"
    return int(cmd[cmd.index(name) + 1])


def _seconds(duration: str) -> int:
    match = re.fullmatch(r"(?:(\d+)m)?(?:(\d+)s)?", duration)
    assert match and duration, f"duração inesperada: {duration}"
    minutes, seconds = (int(part or 0) for part in match.groups())
    return minutes * 60 + seconds


def _compose_api(*files: Path) -> dict[str, Any]:
    api: dict[str, Any] = {}
    for file in files:
        api.update(yaml.safe_load(file.read_text(encoding="utf-8"))["services"]["api"])
    return api


def _coolify_api() -> dict[str, Any]:
    api: dict[str, Any] = json.loads(COOLIFY_APP.read_text(encoding="utf-8"))["services"]["api"]
    return api


def _api_burst() -> int:
    (burst,) = [int(m[1]) for line in location_directives("/api/") if (m := _API_BURST.match(line))]
    return burst


def test_teto_do_middleware_e_derivado_do_pool_do_banco() -> None:
    fields = Settings.model_fields
    pool_default = fields["db_pool_size"].default
    overflow_default = fields["db_pool_max_overflow"].default
    pool = pool_default + overflow_default

    assert CONCURRENCY_PER_POOL_CONNECTION * pool >= MAX_CONCURRENT_REQUESTS


def test_teto_do_uvicorn_cobre_o_middleware_a_rajada_do_nginx_e_as_sondas() -> None:
    # Uma rajada de um cliente só (`burst` do /api/ no Nginx, sem keepalive: uma conexão por pedido;
    # as zonas /auth/ e /docs também contam no middleware, mas não somam a rajada da /api/)
    # com a API no teto recebe o 503 problem+json do middleware, e não o `text/plain` do uvicorn.
    # Mudar o middleware, a rajada ou o CMD sem os outros derruba este teste.
    assert _option("--limit-concurrency") == (
        MAX_CONCURRENT_REQUESTS
        + _api_burst()
        + EXEMPT_PROBE_CONNECTIONS
        + UVICORN_COUNTS_CURRENT_CONNECTION
    )


@pytest.mark.parametrize(
    "api",
    [
        pytest.param(_compose_api(COMPOSE_BASE), id="compose-local"),
        pytest.param(_compose_api(COMPOSE_BASE, COMPOSE_PROD), id="compose-prod"),
        pytest.param(_coolify_api(), id="coolify-app-json"),
    ],
)
def test_parada_graciosa_termina_antes_do_sigkill_da_plataforma(api: dict[str, Any]) -> None:
    grace = _seconds(api.get("stop_grace_period", f"{COMPOSE_DEFAULT_STOP_GRACE_SECONDS}s"))

    timeout = _option("--timeout-graceful-shutdown")

    assert 0 < timeout <= grace - LIFESPAN_SHUTDOWN_MARGIN_SECONDS
