"""Imagens de terceiros nos arquivos do Compose fixadas por digest (cadeia de suprimentos).

A tag é mutável: sem o digest, o desenvolvimento, a CI e o deploy/compose.sh podem receber uma
imagem diferente da revisada. As imagens construídas pelo próprio projeto (serviço com `build:`)
ficam de fora, porque a tag delas nomeia o build local. A infraestrutura publicada pelo Coolify
(deploy/coolify/infra.json) usa as mesmas imagens, pelo mesmo digest.
"""

import json
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
COMPOSE_FILES = (
    "compose.yaml",
    "compose.override.yaml",
    "compose.prod.yaml",
    "compose.e2e-gvisor.yaml",
    "deploy/edge/compose.yaml",
)
COOLIFY_INFRA = ROOT / "deploy" / "coolify" / "infra.json"
PINNED = re.compile(r"^[^@\s]+:[^@\s]+@sha256:[0-9a-f]{64}$")


def _services(relative: str) -> dict[str, dict[str, Any]]:
    document: dict[str, Any] = yaml.safe_load((ROOT / relative).read_text(encoding="utf-8"))
    services: dict[str, dict[str, Any]] = document.get("services") or {}
    return services


def _third_party_images() -> list[tuple[str, str, str]]:
    return [
        (relative, name, service["image"])
        for relative in COMPOSE_FILES
        for name, service in _services(relative).items()
        if "image" in service and "build" not in service
    ]


def _compose_image(service: str) -> str:
    image: str = _services("compose.yaml")[service]["image"]
    return image


def test_ha_imagens_de_terceiros_para_conferir() -> None:
    images = {image.split(":", 1)[0] for _, _, image in _third_party_images()}

    assert {"postgres", "otel/opentelemetry-collector-contrib", "openobserve/openobserve"} <= images


@pytest.mark.parametrize(
    ("relative", "service", "image"),
    [pytest.param(*entry, id=f"{entry[0]}:{entry[1]}") for entry in _third_party_images()],
)
def test_imagem_de_terceiro_tem_tag_e_digest(relative: str, service: str, image: str) -> None:
    assert PINNED.fullmatch(image), f"{relative}, serviço {service}: {image} sem digest"


@pytest.mark.parametrize("service", ["postgres", "otel-collector", "openobserve"])
def test_compose_usa_o_mesmo_digest_da_infra_do_coolify(service: str) -> None:
    infra: dict[str, Any] = json.loads(COOLIFY_INFRA.read_text(encoding="utf-8"))

    assert _compose_image(service) == infra["services"][service]["image"]
