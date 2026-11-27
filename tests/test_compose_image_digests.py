"""Imagens de terceiros nos arquivos do Compose fixadas por digest (cadeia de suprimentos).

A tag é mutável: sem o digest, o desenvolvimento, a CI e o deploy/compose.sh podem receber uma
imagem diferente da revisada. As imagens construídas pelo próprio projeto (serviço com `build:`)
ficam de fora, porque a tag delas nomeia o build local. A infraestrutura publicada pelo Coolify
(deploy/coolify/infra.json) usa as mesmas imagens, pelo mesmo digest. Os workflows do GitHub
Actions seguem a mesma regra e, quando usam a imagem de um serviço do Compose, o mesmo digest.
Constantes de imagem nos testes e nos scripts (`*IMAGE = "..."`, `*IMAGE=...`) também (#442, 68b).
"""

import ast
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
THIRD_PARTY_SERVICES = ("postgres", "otel-collector", "openobserve")
WORKFLOWS = ROOT / ".github" / "workflows"
SHELL_IMAGE = re.compile(r"^\s*(?:readonly\s+)?(?P<nome>[A-Z0-9_]*IMAGE)=(?P<valor>\S+)", re.M)
# Constantes `*IMAGE` que não são imagem baixada de registro, cada uma com o motivo. Uma regra
# genérica (ex.: tudo que termina em `:test`) deixaria passar imagem de terceiro com essa tag.
NOT_PULLED = {
    "goblinshift-web:test": "construída pelo próprio teste (tests/test_nginx_*.py)",
    "ghcr.io/bipoxdxd/goblinshift:": "prefixo do repositório, não uma referência completa",
    "/etc/nginx/snippets/": "caminho dentro da imagem (tests/test_nginx_hsts.py)",
}


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


def _env_images(where: str, env: Any) -> list[tuple[str, str]]:
    if not isinstance(env, dict):
        return []
    return [
        (f"{where} env {name}", value)
        for name, value in env.items()
        if str(name).endswith("IMAGE") and isinstance(value, str)
    ]


def _job_images(name: str, job: dict[str, Any]) -> list[tuple[str, str]]:
    found = _env_images(name, job.get("env"))
    container = job.get("container")
    if isinstance(container, str):
        found.append((f"{name} container", container))
    elif isinstance(container, dict) and "image" in container:
        found.append((f"{name} container", container["image"]))
    for service, spec in (job.get("services") or {}).items():
        found.append((f"{name}/{service}", spec["image"]))
    for index, step in enumerate(job.get("steps") or []):
        where = f"{name} passo {step.get('name') or step.get('uses', index)}"
        if "image" in (step.get("with") or {}):
            found.append((f"{where} with.image", step["with"]["image"]))
        found += _env_images(where, step.get("env"))
    return found


def _workflow_image_references() -> list[tuple[str, str, str]]:
    """(arquivo, onde, imagem) de toda referência a imagem nos workflows, com expressão ou não."""
    images: list[tuple[str, str, str]] = []
    for path in sorted(WORKFLOWS.glob("*.yml")):
        document: dict[Any, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
        found = _env_images("workflow", document.get("env"))
        for name, job in (document.get("jobs") or {}).items():
            found += _job_images(name, job)
        images += [(path.name, where, image) for where, image in found]
    return images


def _workflow_images() -> list[tuple[str, str, str]]:
    """Imagens de terceiros dos workflows: sem as do próprio run, que vêm de uma expressão."""
    return [ref for ref in _workflow_image_references() if "${{" not in ref[2]]


def _named_image_constants() -> list[tuple[str, str, str]]:
    """Atribuições de módulo `*IMAGE = "<literal>"` nos testes e `*IMAGE=` nos scripts.

    Só literal: valor montado (`"...@sha256:" + "a" * 64`, f-string) é digest falso de teste de
    unidade e fica de fora, como os literais soltos dos testes de recusa.
    """
    constantes = []
    for arquivo in sorted((ROOT / "tests").rglob("*.py")):
        for no in ast.parse(arquivo.read_text(encoding="utf-8")).body:
            alvo = no.target if isinstance(no, ast.AnnAssign) else None
            if isinstance(no, ast.Assign) and len(no.targets) == 1:
                alvo = no.targets[0]
            valor = no.value if isinstance(no, ast.Assign | ast.AnnAssign) else None
            if (
                isinstance(alvo, ast.Name)
                and alvo.id.endswith("IMAGE")
                and isinstance(valor, ast.Constant)
                and isinstance(valor.value, str)
            ):
                constantes.append((str(arquivo.relative_to(ROOT)), alvo.id, valor.value))
    for pasta in ("scripts", "deploy"):
        for arquivo in sorted((ROOT / pasta).rglob("*.sh")):
            for casamento in SHELL_IMAGE.finditer(arquivo.read_text(encoding="utf-8")):
                valor = casamento["valor"].strip("\"'")
                constantes.append((str(arquivo.relative_to(ROOT)), casamento["nome"], valor))
    return constantes


def test_ha_imagens_de_terceiros_para_conferir() -> None:
    images = {image.split(":", 1)[0] for _, _, image in _third_party_images()}

    assert {"postgres", "otel/opentelemetry-collector-contrib", "openobserve/openobserve"} <= images


@pytest.mark.parametrize(
    ("relative", "service", "image"),
    [pytest.param(*entry, id=f"{entry[0]}:{entry[1]}") for entry in _third_party_images()],
)
def test_imagem_de_terceiro_tem_tag_e_digest(relative: str, service: str, image: str) -> None:
    assert PINNED.fullmatch(image), f"{relative}, serviço {service}: {image} sem digest"


@pytest.mark.parametrize("service", THIRD_PARTY_SERVICES)
def test_compose_usa_o_mesmo_digest_da_infra_do_coolify(service: str) -> None:
    infra: dict[str, Any] = json.loads(COOLIFY_INFRA.read_text(encoding="utf-8"))

    assert _compose_image(service) == infra["services"][service]["image"]


def test_imagem_dos_workflows_tem_tag_e_digest() -> None:
    imagens = _workflow_images()

    assert imagens, "nenhuma imagem encontrada nos workflows"
    assert [imagem for imagem in imagens if not PINNED.fullmatch(imagem[2])] == []


def test_imagem_de_workflow_igual_a_do_compose_quando_a_tag_e_a_mesma() -> None:
    do_compose = {image.split("@")[0]: image for image in map(_compose_image, THIRD_PARTY_SERVICES)}

    divergentes = [
        (arquivo, onde, image)
        for arquivo, onde, image in _workflow_images()
        if do_compose.get(image.split("@")[0], image) != image
    ]

    assert divergentes == []


def test_imagem_do_proprio_run_vem_do_registro_local_e_fica_fora_da_conferencia() -> None:
    # Imagem construída no próprio run: o digest vem de `needs` e o registro é o descartável do
    # job, então não há tag de terceiro para fixar. Uma imagem de terceiro escrita com expressão
    # reprova aqui, porque não começa pelo registro local.
    do_run = [ref for ref in _workflow_image_references() if "${{" in ref[2]]

    assert do_run, "nenhuma imagem do próprio run encontrada: o filtro deixou de ser exercitado"
    assert [ref for ref in do_run if not ref[2].startswith("127.0.0.1:")] == []


def test_constante_de_imagem_em_testes_e_scripts_tem_digest() -> None:
    constantes = _named_image_constants()
    assert {nome for _, nome, _ in constantes} >= {"POSTGRES_IMAGE", "K6_IMAGE", "REGISTRY_IMAGE"}

    sem_digest = [
        (arquivo, nome, imagem)
        for arquivo, nome, imagem in constantes
        if imagem not in NOT_PULLED and not PINNED.fullmatch(imagem)
    ]

    assert sem_digest == []


def test_constante_de_imagem_igual_a_do_compose_quando_a_tag_e_a_mesma() -> None:
    do_compose = {image.split("@")[0]: image for image in map(_compose_image, THIRD_PARTY_SERVICES)}

    divergentes = [
        (arquivo, nome, imagem)
        for arquivo, nome, imagem in _named_image_constants()
        if do_compose.get(imagem.split("@")[0], imagem) != imagem
    ]

    assert divergentes == []
