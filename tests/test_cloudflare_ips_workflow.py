"""Detecção diária de mudança nas faixas da Cloudflare (ADR 041, "Atualizar a lista").

O workflow só compara e avisa: não grava no repositório, e a permissão de escrever issue fica num
job que não executa código do repositório.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "cloudflare-ips.yml"
PINNED_ACTION = re.compile(r"^[\w.-]+/[\w./-]+@[0-9a-f]{40}$")
DAILY_CRON = re.compile(r"^\d{1,2} \d{1,2} \* \* \*$")
REPORT = "Relatório de teste\n\n- `104.16.0.0/13`\n"
OLD_REPORT = "Relatório anterior\n\n- `103.21.244.0/22`\n"
BOT = "github-actions[bot]"
STRANGER = "alguem-de-fora"
ISSUE_NUMBER = 7
STEP_TIMEOUT_SECONDS = 10
# Como o runner chama o bash: sem `shell:` não há pipefail, e falha no `gh` antes do `jq` passaria
# por "nenhuma issue aberta".
RUNNER_BASH = ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c"]
RUNNER_DEFAULT_SHELL = ["bash", "-e", "-c"]


def workflow() -> dict[Any, Any]:
    loaded: dict[Any, Any] = yaml.safe_load(WORKFLOW.read_text())
    return loaded


def triggers() -> dict[str, Any]:
    # O PyYAML lê a chave `on` como o booleano True.
    on: dict[str, Any] = workflow()[True]
    return on


def jobs() -> dict[str, Any]:
    found: dict[str, Any] = workflow()["jobs"]
    return found


def issue_title() -> str:
    title: str = jobs()["report"]["steps"][0]["env"]["ISSUE_TITLE"]
    return title


def test_compara_todo_dia_e_sob_demanda() -> None:
    schedule = triggers()["schedule"]

    assert len(schedule) == 1
    assert DAILY_CRON.match(schedule[0]["cron"])
    assert "workflow_dispatch" in triggers()


def test_pr_que_mexe_na_comparacao_ou_na_lista_roda_a_comparacao() -> None:
    assert set(triggers()["pull_request"]["paths"]) == {
        ".github/workflows/cloudflare-ips.yml",
        "docker/gateway/sites/cloudflare-ips.conf",
        "scripts/cloudflare_ips.py",
    }


def test_permissoes_sao_as_minimas_por_job() -> None:
    assert workflow()["permissions"] == {}
    assert jobs()["compare"]["permissions"] == {"contents": "read"}
    assert jobs()["report"]["permissions"] == {"issues": "write"}


def test_quem_escreve_issue_nao_faz_checkout_nem_roda_em_pr() -> None:
    report = jobs()["report"]

    assert not any("uses" in step for step in report["steps"])
    assert "github.event_name != 'pull_request'" in report["if"]
    assert "needs.compare.outputs.report != ''" in report["if"]


def test_actions_sao_fixadas_por_sha() -> None:
    uses = [step["uses"] for job in jobs().values() for step in job["steps"] if "uses" in step]

    assert uses
    assert all(PINNED_ACTION.match(action) for action in uses), uses


def test_checkout_nao_guarda_credencial() -> None:
    checkout = next(
        step
        for step in jobs()["compare"]["steps"]
        if step.get("uses", "").startswith("actions/checkout@")
    )

    assert checkout["with"]["persist-credentials"] is False


def test_unico_segredo_e_o_token_padrao() -> None:
    text = WORKFLOW.read_text()

    assert "secrets." not in text
    assert text.count("github.token") == 1


def test_comparacao_usa_o_script_que_valida_a_fonte() -> None:
    commands = "\n".join(step.get("run", "") for step in jobs()["compare"]["steps"])

    assert "python -m scripts.cloudflare_ips --drift-report" in commands


def test_corpo_da_issue_nao_e_interpolado_no_shell() -> None:
    step = jobs()["report"]["steps"][0]

    assert "${{" not in step["run"]
    assert step["env"]["REPORT"] == "${{ needs.compare.outputs.report }}"


def _issue(
    title: str, author: str = BOT, body: str = OLD_REPORT, *, is_pull_request: bool = False
) -> dict[str, Any]:
    issue: dict[str, Any] = {
        "number": ISSUE_NUMBER,
        "title": title,
        "body": body,
        "user": {"login": author},
    }
    if is_pull_request:
        issue["pull_request"] = {"url": "https://api.github.invalid/pulls/7"}
    return issue


def _comment(body: str, author: str = BOT) -> dict[str, Any]:
    return {"body": body, "user": {"login": author}}


def _run_report_step(
    tmp_path: Path,
    issues: list[dict[str, Any]],
    comment_pages: tuple[list[dict[str, Any]], ...] = ([],),
    failing_read: str = "nenhuma-leitura",
) -> str:
    """Executa o passo real com um `gh` falso e devolve o que ele registrou.

    Cada lista vira uma página, concatenada como no `gh api --paginate`. O passo roda no shell que
    o workflow declara, e a leitura cujo caminho casar com `failing_read` falha.
    """
    calls = tmp_path / "gh-chamadas"
    calls.touch()
    (tmp_path / "issues.json").write_text(json.dumps(issues))
    (tmp_path / "comments.json").write_text("".join(json.dumps(page) for page in comment_pages))
    fake = tmp_path / "gh"
    fake.write_text(
        "#!/bin/bash\n"
        f'printf "%s\\n" "$*" >> "{calls}"\n'
        f'if [[ "$1" == api && "$*" == *{failing_read}* ]]; then exit 1; fi\n'
        'case "$1 $*" in\n'
        f'  issue*) cat >> "{calls}" ;;\n'
        f'  *"/comments"*) cat "{tmp_path}/comments.json" ;;\n'
        f'  *) cat "{tmp_path}/issues.json" ;;\n'
        "esac\n"
    )
    fake.chmod(0o755)
    step = jobs()["report"]["steps"][0]
    jq = shutil.which("jq")
    assert jq is not None
    shell = RUNNER_BASH if step.get("shell") == "bash" else RUNNER_DEFAULT_SHELL
    subprocess.run(  # noqa: S603 - executa o passo do próprio workflow, sem entrada externa
        [*shell, step["run"]],
        check=False,
        timeout=STEP_TIMEOUT_SECONDS,
        env={
            "PATH": f"{tmp_path}:{Path(jq).parent}:/usr/bin:/bin",
            "ISSUE_TITLE": issue_title(),
            "BOT_LOGIN": step["env"]["BOT_LOGIN"],
            "REPORT": REPORT,
            "GITHUB_REPOSITORY": "dono/repo",
        },
    )
    return calls.read_text()


def test_passo_da_issue_declara_bash_para_ter_pipefail() -> None:
    assert jobs()["report"]["steps"][0]["shell"] == "bash"


@pytest.mark.parametrize(
    "failing_read", ["dono/repo/issues", "/comments"], ids=["lista-de-issues", "comentarios"]
)
def test_falha_ao_ler_o_github_nao_cria_nem_comenta(tmp_path: Path, failing_read: str) -> None:
    calls = _run_report_step(tmp_path, [_issue(issue_title())], failing_read=failing_read)

    assert "issue create" not in calls
    assert "issue comment" not in calls


def test_autor_filtrado_e_o_do_proprio_workflow() -> None:
    assert jobs()["report"]["steps"][0]["env"]["BOT_LOGIN"] == BOT


def test_divergencia_abre_issue_com_o_relatorio(tmp_path: Path) -> None:
    calls = _run_report_step(tmp_path, [_issue("Outra issue")])

    assert "issue create" in calls
    assert REPORT in calls


def test_issue_do_workflow_com_o_mesmo_relatorio_nao_gera_aviso(tmp_path: Path) -> None:
    calls = _run_report_step(tmp_path, [_issue(issue_title(), body=REPORT)])

    assert "issue create" not in calls
    assert "issue comment" not in calls


def test_mesmo_titulo_de_outro_autor_nao_cala_o_aviso(tmp_path: Path) -> None:
    calls = _run_report_step(tmp_path, [_issue(issue_title(), author=STRANGER, body=REPORT)])

    assert "issue create" in calls
    assert "issue comment" not in calls


def test_pull_request_do_mesmo_autor_nao_conta_como_issue(tmp_path: Path) -> None:
    calls = _run_report_step(tmp_path, [_issue(issue_title(), body=REPORT, is_pull_request=True)])

    assert "issue create" in calls


def test_titulo_parecido_nao_impede_o_aviso(tmp_path: Path) -> None:
    calls = _run_report_step(tmp_path, [_issue(f"Re: {issue_title()} (antiga)", body=REPORT)])

    assert "issue create" in calls


def test_divergencia_nova_comenta_na_issue_do_workflow(tmp_path: Path) -> None:
    calls = _run_report_step(tmp_path, [_issue(issue_title())])

    assert f"issue comment {ISSUE_NUMBER} " in calls
    assert REPORT in calls
    assert "issue create" not in calls


def test_relatorio_igual_ao_ultimo_comentario_nao_repete(tmp_path: Path) -> None:
    calls = _run_report_step(tmp_path, [_issue(issue_title())], ([_comment(REPORT)],))

    assert "issue comment" not in calls


def test_ultimo_comentario_diferente_gera_novo_comentario(tmp_path: Path) -> None:
    calls = _run_report_step(
        tmp_path,
        [_issue(issue_title(), body=REPORT)],
        ([_comment(REPORT), _comment(OLD_REPORT)],),
    )

    assert f"issue comment {ISSUE_NUMBER} " in calls


def test_comentario_de_outro_autor_nao_conta_como_relatorio(tmp_path: Path) -> None:
    calls = _run_report_step(
        tmp_path,
        [_issue(issue_title())],
        ([_comment(OLD_REPORT), _comment(REPORT, author=STRANGER)],),
    )

    assert f"issue comment {ISSUE_NUMBER} " in calls


def test_ultimo_relatorio_vem_da_ultima_pagina_de_comentarios(tmp_path: Path) -> None:
    calls = _run_report_step(
        tmp_path,
        [_issue(issue_title())],
        ([_comment(OLD_REPORT)], [_comment(REPORT)]),
    )

    assert "issue comment" not in calls
