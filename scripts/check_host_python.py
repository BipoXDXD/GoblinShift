"""Confere que os pacotes de deploy/ que rodam no Python do host funcionam nele (3.12).

O Vermin verifica sintaxe e APIs da stdlib. O mypy usa os tipos da stdlib do 3.12 e
alcança também argumentos de métodos cuja versão do Vermin não conhece.

O modo `--imports-only` roda com `python -I -S -B`, antes de instalar dependências no CI.
Assim, cada módulo precisa importar com a stdlib do host e sem depender do site-packages.

Sem argumentos, confere os pacotes marcados como `py312` em
`[tool.ruff.per-file-target-version]` do pyproject.toml, que fica sendo a única lista deles.
Com caminhos, confere só esses (usado pelos testes).
"""

import argparse
import importlib
import subprocess
import sys
import tomllib
from collections.abc import Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "pyproject.toml"
HOST_TARGET = "py312"
HOST_PYTHON = (3, 12)
HOST_PACKAGES_DIR = "deploy"
# O pacote vermin não tem __main__: chama a mesma função do console script.
VERMIN = "import sys, vermin; sys.exit(vermin.main())"
WRONG_PYTHON_EXIT = 2


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Confere os pacotes do host no Python do host.")
    parser.add_argument("paths", nargs="*", type=Path, help="pastas (padrão: as do host)")
    parser.add_argument(
        "--imports-only", action="store_true", help="valida imports só com a stdlib"
    )
    args = parser.parse_args(argv)

    if _running_python() != HOST_PYTHON:
        expected = _dotted(HOST_PYTHON)
        print(f"precisa rodar no Python {expected}, veio {sys.version}", file=sys.stderr)
        return WRONG_PYTHON_EXIT
    paths: list[Path] = args.paths or host_paths()
    if args.imports_only:
        if not (sys.flags.isolated and sys.flags.no_site):
            parser.error("rode --imports-only com python -I -S -B")
        failures = import_failures(paths)
    else:
        failures = [
            report for report in (version_floor_report(paths), type_check_report(paths)) if report
        ]
    for problem in failures:
        print(problem, file=sys.stderr)
    return 1 if failures else 0


def host_paths() -> list[Path]:
    """As pastas de deploy/ que o pyproject declara como código do Python do host."""
    config = tomllib.loads(PYPROJECT.read_text())
    targets: dict[str, str] = config["tool"]["ruff"]["per-file-target-version"]
    paths = [
        ROOT / pattern.removesuffix("/**")
        for pattern, target in targets.items()
        if target == HOST_TARGET and pattern.startswith(f"{HOST_PACKAGES_DIR}/")
    ]
    if not paths:
        raise SystemExit(f"nenhuma pasta {HOST_TARGET} em {PYPROJECT}")
    return paths


def version_floor_report(paths: Sequence[Path]) -> str:
    """Relato do vermin quando algo exige Python acima do host; vazio quando tudo cabe."""
    return _failure_report(
        [
            sys.executable,
            "-c",
            VERMIN,
            f"-t={_dotted(HOST_PYTHON)}-",
            "--no-tips",
            "--violations",
            "--eval-annotations",
            "--no-parse-comments",
        ],
        paths,
    )


def type_check_report(paths: Sequence[Path]) -> str:
    """O mypy usa as assinaturas da stdlib da versão alvo, mesmo nos testes que rodam em 3.14."""
    return _failure_report(
        [sys.executable, "-m", "mypy", "--python-version", _dotted(HOST_PYTHON)],
        paths,
    )


def _failure_report(command: Sequence[str], paths: Sequence[Path]) -> str:
    result = subprocess.run(  # noqa: S603 - ferramentas no mesmo ambiente, argumentos fixos
        [*command, *(str(path) for path in paths)],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        return ""
    report = f"{result.stdout}\n{result.stderr}".strip()
    return report or f"checagem falhou (código {result.returncode})"


def import_failures(paths: Sequence[Path]) -> list[str]:
    """Uma linha por módulo que não importa neste interpretador."""
    failures = []
    for path in paths:
        sys.path.insert(0, str(path.parent))
        try:
            for module in _modules(path):
                try:
                    importlib.import_module(module)
                # Qualquer falha de importação é o que se procura: relata e segue para o próximo.
                except Exception as error:
                    failures.append(f"{module}: {type(error).__name__}: {error}")
        finally:
            sys.path.remove(str(path.parent))
    return failures


def _modules(package_dir: Path) -> list[str]:
    """Nome importável de cada .py da pasta, relativo à pasta que a contém."""
    base = package_dir.parent
    names = []
    for file in sorted(package_dir.rglob("*.py")):
        parts = file.relative_to(base).with_suffix("").parts
        if parts[-1] == "__init__":
            parts = parts[:-1]
        names.append(".".join(parts))
    return names


def _running_python() -> tuple[int, int]:
    return sys.version_info.major, sys.version_info.minor


def _dotted(version: tuple[int, int]) -> str:
    return ".".join(str(part) for part in version)


if __name__ == "__main__":
    sys.exit(main())
