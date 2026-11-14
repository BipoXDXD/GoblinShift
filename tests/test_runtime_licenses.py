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
import yaml
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
    ],
    ids=[
        "spdx-gpl",
        "spdx-agpl",
        "dupla-com-gpl",
        "texto-livre",
        "classifier-gpl",
        "classifier-agpl",
    ],
)
def test_gpl_e_agpl_sao_recusadas(tmp_path: Path, headers: list[str]) -> None:
    package = _fake_distribution(tmp_path, "copyleft", headers)

    assert refused_packages([package]) == [f"copyleft 1.0 ({headers[0].split(': ', 1)[1]})"]


@pytest.mark.parametrize(
    "headers",
    [
        ["License-Expression: LGPL-3.0-or-later"],
        ["License: GNU LGPL v3"],
        ["Classifier: License :: OSI Approved :: GNU Lesser General Public License v3 (LGPLv3)"],
        ["License: GPL-compatible, see LICENSE"],
        ["License-Expression: Apache-2.0", "Classifier: Programming Language :: Python"],
        ["License: MIT\n        GNU General Public License text pasted below"],
        [],
    ],
    ids=[
        "spdx-lgpl",
        "texto-lgpl",
        "classifier-lgpl",
        "compativel",
        "apache",
        "texto-longo",
        "sem",
    ],
)
def test_lgpl_e_permissivas_passam(tmp_path: Path, headers: list[str]) -> None:
    package = _fake_distribution(tmp_path, "permissive", headers)

    assert refused_packages([package]) == []


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


@pytest.mark.parametrize("job", ["image", "executor-image"])
def test_imagens_publicadas_rodam_o_check_de_licenca(job: str) -> None:
    workflow = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text("utf-8"))
    commands = [step.get("run", "") for step in workflow["jobs"][job]["steps"]]

    assert any("< scripts/check_runtime_licenses.py" in command for command in commands)


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
