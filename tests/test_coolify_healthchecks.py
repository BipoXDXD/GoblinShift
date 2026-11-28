"""A saúde dos contêineres da API e do Nginx é liveness; a prontidão fica no gate (issue #366, d).

Se o healthcheck consultasse `/readyz`, que lê o banco, uma oscilação do Postgres marcaria a API e
o Nginx `unhealthy` juntos, e a regra 22 do pós-release reprovaria uma publicação sã. A prontidão
continua conferida onde decide: na regra 23 do `pos_release` (`/readyz` direto na API).
"""

import json
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
COOLIFY_APP = ROOT / "deploy" / "coolify" / "app.json"
COMPOSE = ROOT / "compose.yaml"
RELEASE = ROOT / "deploy" / "coolify" / "release"
PROBE = re.compile(r"http://127\.0\.0\.1(?::\d+)?(/[A-Za-z]+)")


def _probed_path(service: dict[str, Any]) -> str:
    """Caminho HTTP que o healthcheck sonda (o `python -c` da API ou o `curl` do Nginx)."""
    for item in service["healthcheck"]["test"]:
        match = PROBE.search(item)
        if match is not None:
            return match.group(1)
    raise AssertionError(f"healthcheck sem sonda HTTP local: {service['healthcheck']['test']}")


def _app_service(name: str) -> dict[str, Any]:
    service: dict[str, Any] = json.loads(COOLIFY_APP.read_text(encoding="utf-8"))["services"][name]
    return service


@pytest.mark.parametrize("name", ["api", "nginx"])
def test_saude_da_api_e_do_nginx_e_so_o_livez(name: str) -> None:
    service = _app_service(name)

    assert _probed_path(service) == "/livez"
    assert not any("readyz" in item for item in service["healthcheck"]["test"])


def test_saude_da_api_e_a_mesma_no_compose_e_no_coolify() -> None:
    compose_api = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]["api"]

    assert _probed_path(compose_api) == _probed_path(_app_service("api"))


def test_prontidao_continua_no_gate_da_publicacao() -> None:
    pos_release = (RELEASE / "pos_release.py").read_text(encoding="utf-8")
    rule = re.search(r"^def rule_23\(.*?(?=^def )", pos_release, flags=re.MULTILINE | re.DOTALL)
    rules = re.search(r"^RULES: .*?^\)", pos_release, flags=re.MULTILINE | re.DOTALL)
    remote = (RELEASE / "pos_release_remote.sh").read_text(encoding="utf-8")

    assert rule is not None, "pos_release.py sem rule_23"
    assert '"READYZ"' in rule.group(0)
    assert rules is not None
    assert re.search(r"\brule_23\b", rules.group(0)), "rule_23 fora da lista RULES"
    assert re.search(r"emit READYZ ", remote)
    assert re.search(r"http://\$ip:8000/readyz", remote)
