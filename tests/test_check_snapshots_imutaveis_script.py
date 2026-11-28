"""scripts/check-snapshots-imutaveis.sh: snapshot de versão publicada não muda nem some (1.6).

Cada teste monta um repositório git temporário com um snapshot na base, faz um commit por cima e
roda o script contra a base.
"""

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check-snapshots-imutaveis.sh"
SCRIPT_TIMEOUT_S = 30
SNAPSHOTS = Path("tests") / "catalog" / "congeladas"
EXISTING = SNAPSHOTS / "image-forge-v1.json"
# Sem assinatura nem hooks globais de quem roda: o repositório do teste é descartável.
GIT_CONFIG = (
    *("-c", "user.name=teste", "-c", "user.email=teste@example.invalid"),
    *("-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null"),
)


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(  # noqa: S603 - argumentos fixos do próprio teste
        ["git", *GIT_CONFIG, *args],  # noqa: S607 - git do PATH, como nos outros testes
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        timeout=SCRIPT_TIMEOUT_S,
    )
    return result.stdout.strip()


def _repo_with_snapshot(tmp_path: Path) -> tuple[Path, str]:
    """Repositório com um snapshot na base; devolve o caminho e o SHA da base."""
    repo = tmp_path / "repo"
    (repo / SNAPSHOTS).mkdir(parents=True)
    (repo / EXISTING).write_text('{"defaults": {}}\n', encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "base")
    return repo, _git(repo, "rev-parse", "HEAD")


def _commit_all(repo: Path) -> None:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "pr")


def _check(repo: Path, base: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - script do repositório
        ["bash", str(SCRIPT), base],  # noqa: S607 - bash do PATH, como no terminal
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=SCRIPT_TIMEOUT_S,
        check=False,
    )


def test_snapshot_modificado_falha(tmp_path: Path) -> None:
    repo, base = _repo_with_snapshot(tmp_path)
    (repo / EXISTING).write_text('{"defaults": {"max_width": 1024}}\n', encoding="utf-8")
    _commit_all(repo)

    result = _check(repo, base)

    assert result.returncode == 1
    assert str(EXISTING) in result.stderr


def test_snapshot_apagado_falha(tmp_path: Path) -> None:
    repo, base = _repo_with_snapshot(tmp_path)
    (repo / EXISTING).unlink()
    (repo / "README").write_text("outro arquivo\n", encoding="utf-8")
    _commit_all(repo)

    result = _check(repo, base)

    assert result.returncode == 1
    assert str(EXISTING) in result.stderr


def test_snapshot_novo_passa(tmp_path: Path) -> None:
    repo, base = _repo_with_snapshot(tmp_path)
    (repo / SNAPSHOTS / "image-forge-v2.json").write_text('{"defaults": {}}\n', encoding="utf-8")
    _commit_all(repo)

    result = _check(repo, base)

    assert result.returncode == 0, result.stderr
