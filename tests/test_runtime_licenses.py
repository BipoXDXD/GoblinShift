"""Nenhum pacote GPL/AGPL no runtime (scripts/check_runtime_licenses.py, ADR 057).

O pglast (GPL-3.0-or-later) é aceito só no grupo dev. Aqui se confere o script com metadados
falsos e as dependências de runtime do lock (aplicação e executor) com os metadados instalados.
Dentro das imagens, o mesmo script roda no CI (imagens publicadas) e no teste da imagem do
executor (tests/test_executor_image.py).
"""

import shutil
import subprocess
from importlib.metadata import Distribution, distribution, distributions
from pathlib import Path

import pytest
from packaging.requirements import Requirement

from scripts.check_runtime_licenses import (
    EXIT_EMPTY,
    EXIT_REFUSED,
    main,
    normalized_name,
    refused_packages,
)

ROOT = Path(__file__).resolve().parent.parent
UV_TIMEOUT_S = 60
# Os dois ambientes de runtime: a imagem da aplicação e a do executor.
RUNTIME_EXPORTS = (("--no-dev",), ("--only-group", "executor"))


def _fake_distribution(site: Path, name: str, headers: list[str]) -> Distribution:
    info = site / f"{name.replace('-', '_')}-1.0.dist-info"
    info.mkdir(parents=True)
    lines = ["Metadata-Version: 2.4", f"Name: {name}", "Version: 1.0", *headers]
    (info / "METADATA").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (found,) = distributions(path=[str(site)])
    return found


@pytest.mark.parametrize(
    "headers",
    [
        ["License-Expression: GPL-3.0-or-later"],
        ["License-Expression: AGPL-3.0-only"],
        ["License-Expression: MIT OR GPL-2.0-only"],
        ["License: GPLv3"],
        ["Classifier: License :: OSI Approved :: GNU General Public License v3 (GPLv3)"],
        ["Classifier: License :: OSI Approved :: GNU Affero General Public License v3"],
        ["License: GNU General Public License v3"],
        ["License: GNU GENERAL PUBLIC LICENSE\n        Version 3, 29 June 2007"],
        ["License: gnu affero general public license"],
    ],
    ids=[
        "spdx-gpl",
        "spdx-agpl",
        "dupla-com-gpl",
        "texto-livre",
        "classifier-gpl",
        "classifier-agpl",
        "extenso",
        "texto-oficial",
        "extenso-minusculas",
    ],
)
def test_gpl_e_agpl_sao_recusadas(tmp_path: Path, headers: list[str]) -> None:
    package = _fake_distribution(tmp_path, "copyleft", headers)
    first_line = headers[0].split(": ", 1)[1].splitlines()[0]

    assert refused_packages([package]) == [f"copyleft 1.0 ({first_line})"]


@pytest.mark.parametrize(
    "headers",
    [
        ["License-Expression: LGPL-3.0-or-later"],
        ["License: GNU LGPL v3"],
        ["Classifier: License :: OSI Approved :: GNU Lesser General Public License v3 (LGPLv3)"],
        ["License: GPL-compatible, see LICENSE"],
        ["License-Expression: Apache-2.0", "Classifier: Programming Language :: Python"],
        ["License: MIT\n        GNU General Public License text pasted below"],
        ["Classifier: License :: OSI Approved :: GNU Library or Lesser General Public License"],
    ],
    ids=[
        "spdx-lgpl",
        "texto-lgpl",
        "classifier-lgpl",
        "compativel",
        "apache",
        "texto-longo",
        "classifier-library",
    ],
)
def test_lgpl_e_permissivas_passam(tmp_path: Path, headers: list[str]) -> None:
    package = _fake_distribution(tmp_path, "permissive", headers)

    assert refused_packages([package]) == []


def test_pacote_sem_licenca_declarada_e_recusado(tmp_path: Path) -> None:
    # Sem nenhum dos três campos não há o que conferir; passar calado esconderia uma GPL.
    package = _fake_distribution(tmp_path, "unknown", ["Classifier: Programming Language :: C"])

    assert refused_packages([package]) == ["unknown 1.0 (sem licença declarada)"]


def test_ferramenta_de_dev_e_recusada_pelo_nome_mesmo_com_licenca_permissiva(
    tmp_path: Path,
) -> None:
    package = _fake_distribution(tmp_path, "pglast", ["License-Expression: MIT"])

    assert refused_packages([package]) == ["pglast 1.0 (ferramenta só de dev)"]


def test_script_sai_com_erro_quando_recusa(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _fake_distribution(tmp_path / "a", "copyleft", ["License-Expression: GPL-2.0-only"])

    assert main([str(tmp_path / "a")]) == EXIT_REFUSED
    assert "recusado: copyleft 1.0 (GPL-2.0-only)" in capsys.readouterr().err


def test_script_passa_com_pacotes_permitidos(tmp_path: Path) -> None:
    _fake_distribution(tmp_path, "permissive", ["License-Expression: MIT"])

    assert main([str(tmp_path)]) == 0


def test_script_sem_pacote_nenhum_falha(tmp_path: Path) -> None:
    # Caminho errado conferiria um ambiente vazio; passar aí esconderia a imagem inteira.
    assert main([str(tmp_path)]) == EXIT_EMPTY


@pytest.mark.parametrize("export", RUNTIME_EXPORTS, ids=["aplicacao", "executor"])
def test_dependencias_de_runtime_do_lock_nao_tem_gpl(export: tuple[str, ...]) -> None:
    names = _runtime_names(export)
    installed = [distribution(name) for name in names]

    assert "pglast" not in names
    assert refused_packages(installed) == []


def test_pglast_continua_no_grupo_dev() -> None:
    # Se ele sair do lock, o check das migrations (tests/migration_sql.py) também some.
    dev = [normalized_name(Requirement(line).name) for line in _export("--only-group", "dev")]

    assert "pglast" in dev


@pytest.mark.parametrize(
    ("dockerfile", "python"),
    [("app.Dockerfile", "/app/.venv/bin/python"), ("executor.Dockerfile", "python")],
)
def test_build_das_imagens_roda_o_check_depois_do_ultimo_copy(dockerfile: str, python: str) -> None:
    # No build, o check roda em cada plataforma e antes do push. Depois do último COPY do estágio
    # final ele vê todos os pacotes; a instrução exata barra `|| true` e troca de interpretador.
    instructions = _instructions(ROOT / "docker" / dockerfile)
    last_from = max(i for i, line in enumerate(instructions) if line.startswith("FROM "))
    final_stage = instructions[last_from:]
    expected = (
        "RUN --mount=type=bind,source=scripts/check_runtime_licenses.py,"
        f"target=/tmp/check_runtime_licenses.py {python} -I -B /tmp/check_runtime_licenses.py"
    )
    last_copy = max(i for i, line in enumerate(final_stage) if line.startswith("COPY "))

    assert expected in final_stage[last_copy:]


def _instructions(dockerfile: Path) -> list[str]:
    """Instruções do Dockerfile, com as continuações `\\` unidas e sem comentários."""
    instructions: list[str] = []
    pending = ""
    for raw in dockerfile.read_text("utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        pending = f"{pending} {line}".strip() if pending else line
        if pending.endswith("\\"):
            pending = pending[:-1].rstrip()
            continue
        instructions.append(pending)
        pending = ""
    return instructions


def _runtime_names(export: tuple[str, ...]) -> list[str]:
    """Pacotes do runtime que valem neste interpretador (os marcadores de plataforma decidem)."""
    names = []
    for requirement in _export(*export):
        parsed = Requirement(requirement)
        if parsed.marker is None or parsed.marker.evaluate():
            names.append(normalized_name(parsed.name))
    assert names, export
    return names


def _export(*selection: str) -> list[str]:
    """Exigências do lock para a seleção de grupos, como o Dockerfile as instala."""
    uv = shutil.which("uv")
    assert uv is not None, "o teste lê o lock pelo uv export"
    result = subprocess.run(  # noqa: S603
        [uv, "export", "--locked", "--no-emit-project", "--no-hashes", *selection],
        capture_output=True,
        text=True,
        cwd=ROOT,
        timeout=UV_TIMEOUT_S,
        check=True,
    )
    # Uma exigência por linha (`nome==versão ; marcador`); as linhas `# via` são comentário.
    return [
        line
        for line in result.stdout.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
