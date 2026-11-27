"""`npm ci` da CI sem scripts de instalação (#442, item 31a).

Uma dependência comprometida com `postinstall` rodaria no runner, que tem `GITHUB_TOKEN` e o
cache. A imagem da web já instala com `--ignore-scripts` (`docker/web.Dockerfile`), o que prova
que o build não precisa de script nenhum. Um pacote liberado com `true` no `allowScripts` do
`web/package.json` só roda o script por um `npm rebuild <pacote>` explícito, no mesmo job.
"""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"


def _jobs() -> Iterator[tuple[str, str, list[dict[str, Any]]]]:
    for arquivo in sorted(WORKFLOWS.glob("*.yml")):
        workflow = yaml.safe_load(arquivo.read_text(encoding="utf-8"))
        for nome, job in (workflow.get("jobs") or {}).items():
            yield arquivo.name, nome, job.get("steps") or []


def _comandos(run: str) -> list[list[str]]:
    return [linha.split() for linha in run.splitlines() if linha.strip()]


def _npm_ci_runs() -> list[tuple[str, str, list[str]]]:
    return [
        (arquivo, job, comando)
        for arquivo, job, passos in _jobs()
        for passo in passos
        for comando in _comandos(passo.get("run", ""))
        if comando[:2] == ["npm", "ci"]
    ]


def _rebuilds(passos: list[dict[str, Any]]) -> set[str]:
    return {
        pacote
        for passo in passos
        for comando in _comandos(passo.get("run", ""))
        if comando[:2] == ["npm", "rebuild"]
        for pacote in comando[2:]
        if not pacote.startswith("-")
    }


def _package_json() -> dict[str, Any]:
    dados = json.loads((ROOT / "web" / "package.json").read_text(encoding="utf-8"))
    assert isinstance(dados, dict)
    return dados


def test_workflows_rodam_npm_ci() -> None:
    assert _npm_ci_runs(), "nenhum `npm ci` encontrado: o detector deixou de achar os passos"


def test_todo_npm_ci_dos_workflows_ignora_scripts() -> None:
    sem_flag = [
        (arquivo, job)
        for arquivo, job, comando in _npm_ci_runs()
        if "--ignore-scripts" not in comando
    ]

    assert sem_flag == []


def test_pacote_liberado_no_allowscripts_tem_rebuild_explicito() -> None:
    liberados = {nome for nome, ok in _package_json()["allowScripts"].items() if ok}
    jobs_com_npm_ci = {(arquivo, job) for arquivo, job, _ in _npm_ci_runs()}
    for arquivo, job, passos in _jobs():
        if (arquivo, job) in jobs_com_npm_ci:
            assert liberados <= _rebuilds(passos), (arquivo, job)
