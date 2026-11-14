"""Confere que os pacotes de deploy/ que rodam no Python do host funcionam nele (3.12).

O ruff só pega sintaxe nova e o pytest do CI roda no Python da imagem: uma função da stdlib
que só existe a partir do 3.13 passaria pelos dois e quebraria no host com AttributeError.
Este script roda no próprio 3.12 (job "Scripts do host" do CI) e faz duas coisas:

1. o vermin aponta qualquer API ou sintaxe que exija versão acima do host;
2. cada módulo é importado, o que pega erro de tempo de importação que o vermin não vê
   (anotação sem aspas avaliada na definição, por exemplo).

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
    args = parser.parse_args(argv)

    if _running_python() != HOST_PYTHON:
        expected = _dotted(HOST_PYTHON)
        print(f"precisa rodar no Python {expected}, veio {sys.version}", file=sys.stderr)
        return WRONG_PYTHON_EXIT
    paths: list[Path] = args.paths or host_paths()
    report = version_floor_report(paths)
    failures = import_failures(paths)
    for problem in [report, *failures]:
        if problem:
            print(problem, file=sys.stderr)
    return 1 if report or failures else 0


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
    result = subprocess.run(  # noqa: S603 - vermin do mesmo ambiente, argumentos fixos
        [
            sys.executable,
            "-c",
            VERMIN,
            f"-t={_dotted(HOST_PYTHON)}-",
            "--no-tips",
            "--violations",
            "--eval-annotations",
            "--no-parse-comments",
            *(str(path) for path in paths),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        return ""
    return f"{result.stdout}\n{result.stderr}".strip()


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
        if file.name == "__main__.py":
            continue  # executa o CLI ao importar
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
