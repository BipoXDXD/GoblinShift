"""O gitleaks do ci.yml também lê o diff dos merge commits (issue #406).

O gitleaks varre a saída de `git log -p` com as opções de `--log-opts`. Sem `--diff-merges`, o git
não mostra diff de merge, e um segredo que só existe na resolução de um merge (evil merge) passa.
O ensaio monta esse repositório e roda o `git log` com as opções do workflow, do mesmo jeito que o
gitleaks: se a linha plantada aparece, o gitleaks a recebe.
"""

import re
import shlex
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
GITLEAKS_STEP = "gitleaks no histórico de HEAD"
LOG_OPTS = re.compile(r'"--log-opts=([^"]*)"')
# Prefixo fixo que o gitleaks 8 põe antes das opções de `--log-opts`.
GITLEAKS_LOG_PREFIX = ("log", "-p", "-U0", "--no-ext-diff")
PLANTED_LINE = "CANARY-evil-merge-406"
GIT_TIMEOUT_S = 30


def test_gitleaks_ci_usa_diff_merges() -> None:
    assert "--diff-merges=first-parent" in workflow_log_opts()


def test_segredo_so_na_resolucao_do_merge_chega_ao_gitleaks(tmp_path: Path) -> None:
    build_evil_merge(tmp_path)

    log = git(tmp_path, *GITLEAKS_LOG_PREFIX, *workflow_log_opts())

    assert f"+{PLANTED_LINE}" in log


def test_sem_diff_merges_o_ensaio_nao_veria_o_segredo(tmp_path: Path) -> None:
    """Controle do ensaio: as opções antigas escondem a linha plantada."""
    build_evil_merge(tmp_path)

    log = git(tmp_path, *GITLEAKS_LOG_PREFIX, "--full-history", "HEAD")

    assert PLANTED_LINE not in log


def workflow_log_opts() -> list[str]:
    workflow = yaml.safe_load(CI_WORKFLOW.read_text())
    step = next(s for s in workflow["jobs"]["secrets"]["steps"] if s.get("name") == GITLEAKS_STEP)
    match = LOG_OPTS.search(step["run"])
    assert match is not None, "o passo do gitleaks perdeu o --log-opts"
    return shlex.split(match.group(1))


def build_evil_merge(repo: Path) -> None:
    """main e topic mexem em arquivos diferentes; a linha plantada nasce só no merge."""
    git(repo, "init", "-q", "-b", "main")
    commit_file(repo, "base.txt", "base\n", "base")
    git(repo, "switch", "-q", "-c", "topic")
    commit_file(repo, "topic.txt", "topic\n", "topic")
    git(repo, "switch", "-q", "main")
    commit_file(repo, "main.txt", "main\n", "main")
    git(repo, "merge", "-q", "--no-commit", "--no-ff", "topic")
    (repo / "base.txt").write_text(f"base\n{PLANTED_LINE}\n")
    git(repo, "add", "base.txt")
    git(repo, "commit", "-q", "-m", "merge topic")


def commit_file(repo: Path, name: str, content: str, message: str) -> None:
    (repo / name).write_text(content)
    git(repo, "add", name)
    git(repo, "commit", "-q", "-m", message)


def git(repo: Path, *args: str) -> str:
    identity = ("-c", "user.name=Ensaio", "-c", "user.email=ensaio@example.invalid")
    completed = subprocess.run(  # noqa: S603 - argv montado pelo teste, sem shell
        ["git", "-C", str(repo), *identity, "-c", "commit.gpgsign=false", *args],  # noqa: S607
        capture_output=True,
        text=True,
        timeout=GIT_TIMEOUT_S,
        check=True,
    )
    return completed.stdout
