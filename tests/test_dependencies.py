"""Dependências do executor fixadas em versão exata, iguais no projeto, no grupo e no lock.

A imagem do executor instala o grupo `executor` a partir do `uv.lock`; o ambiente de testes usa
`[project.dependencies]`. Se os dois divergirem, os testes passam com uma versão e a produção roda
outra. Pillow: ADR 018. pikepdf, com o qpdf que vem dentro da wheel: ADR 035.

Também confere o cooldown do Dependabot: todo ecossistema espera o mínimo de dias antes de propor
uma versão recém-publicada; o uv da CI igual ao da imagem; e toda action fixada por SHA com a
versão exata no comentário, que é o que o Dependabot compara.
"""

import re
import tomllib
from pathlib import Path
from typing import Any

import pikepdf
import PIL
import yaml

ROOT = Path(__file__).resolve().parent.parent
PILLOW_PIN = "pillow==13.0.0"
PIKEPDF_PIN = "pikepdf==10.16.0"
# A wheel do pikepdf traz o libqpdf compilado junto; a versão dele muda com a do pikepdf.
QPDF_VERSION = "12.4.2"
# D1 (#366): o Dependabot espera 7 dias antes de propor uma versão nova, em todo ecossistema. Um
# pacote sequestrado costuma ser retirado nesse prazo, antes de virar PR.
COOLDOWN_MIN_DAYS = 7
WORKFLOWS = ROOT / ".github" / "workflows"
UV_IMAGE = re.compile(r"^FROM ghcr\.io/astral-sh/uv:(\d+\.\d+\.\d+)@sha256:[0-9a-f]{64}\b", re.M)
PINNED_USES = re.compile(r"^\s*-?\s*uses:\s*[\w.-]+/[\w./-]+@[0-9a-f]{40}\b(?P<resto>.*)$")
EXACT_VERSION_COMMENT = re.compile(r"\s+# v\d+\.\d+\.\d+$")


def _pyproject() -> dict[str, object]:
    with (ROOT / "pyproject.toml").open("rb") as source:
        return tomllib.load(source)


def _locked_versions() -> dict[str, str]:
    with (ROOT / "uv.lock").open("rb") as source:
        lock = tomllib.load(source)
    return {package["name"]: package["version"] for package in lock["package"]}


def _project_dependencies() -> list[str]:
    project = _pyproject()["project"]
    assert isinstance(project, dict)
    dependencies = project["dependencies"]
    assert isinstance(dependencies, list)
    return dependencies


def _dependabot_updates() -> list[dict[str, Any]]:
    config = yaml.safe_load((ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8"))
    updates = config["updates"]
    assert isinstance(updates, list)
    return updates


def _dockerfile_uv_version() -> str:
    versoes = UV_IMAGE.findall((ROOT / "docker" / "app.Dockerfile").read_text(encoding="utf-8"))
    assert len(versoes) == 1, versoes
    return str(versoes[0])


def _setup_uv_steps() -> list[tuple[str, dict[str, Any]]]:
    passos = []
    for arquivo in sorted(WORKFLOWS.glob("*.yml")):
        workflow = yaml.safe_load(arquivo.read_text(encoding="utf-8"))
        for job in (workflow.get("jobs") or {}).values():
            for passo in job.get("steps") or []:
                if passo.get("uses", "").startswith("astral-sh/setup-uv@"):
                    passos.append((arquivo.name, passo))
    return passos


def test_pillow_tem_pin_exato_nas_dependencias_do_projeto() -> None:
    pillow = [dep for dep in _project_dependencies() if dep.startswith("pillow")]

    assert pillow == [PILLOW_PIN]


def test_pikepdf_tem_pin_exato_nas_dependencias_do_projeto() -> None:
    pikepdf_deps = [dep for dep in _project_dependencies() if dep.startswith("pikepdf")]

    assert pikepdf_deps == [PIKEPDF_PIN]


def test_grupo_do_executor_tem_so_os_mesmos_pins() -> None:
    groups = _pyproject()["dependency-groups"]
    assert isinstance(groups, dict)

    assert groups["executor"] == [PIKEPDF_PIN, PILLOW_PIN]


def test_lock_e_ambiente_tem_a_versao_fixada_do_pillow() -> None:
    assert _locked_versions()["pillow"] == "13.0.0"
    assert PIL.__version__ == "13.0.0"


def test_lock_e_ambiente_tem_a_versao_fixada_do_pikepdf_e_do_qpdf() -> None:
    assert _locked_versions()["pikepdf"] == "10.16.0"
    assert pikepdf.__version__ == "10.16.0"
    assert pikepdf.__libqpdf_version__ == QPDF_VERSION


def test_todo_ecossistema_do_dependabot_espera_o_cooldown_minimo() -> None:
    updates = _dependabot_updates()
    assert updates
    for item in updates:
        cooldown = item.get("cooldown", {})
        dias = {k: v for k, v in cooldown.items() if k.endswith("-days")}
        assert dias.get("default-days", 0) >= COOLDOWN_MIN_DAYS, item["package-ecosystem"]
        assert all(v >= COOLDOWN_MIN_DAYS for v in dias.values()), item["package-ecosystem"]


def test_todo_setup_uv_usa_a_versao_do_dockerfile() -> None:
    versao = _dockerfile_uv_version()
    passos = _setup_uv_steps()
    assert passos

    sem_a_versao = [
        arquivo for arquivo, passo in passos if (passo.get("with") or {}).get("version") != versao
    ]

    assert sem_a_versao == []


def test_action_fixada_por_sha_traz_a_versao_exata_no_comentario() -> None:
    fixadas = [
        (arquivo.name, numero, casamento["resto"])
        for arquivo in sorted(WORKFLOWS.glob("*.yml"))
        for numero, linha in enumerate(arquivo.read_text(encoding="utf-8").splitlines(), start=1)
        if (casamento := PINNED_USES.match(linha))
    ]
    assert fixadas

    sem_versao_exata = [
        (arquivo, numero)
        for arquivo, numero, resto in fixadas
        if not EXACT_VERSION_COMMENT.fullmatch(resto)
    ]

    assert sem_versao_exata == []
