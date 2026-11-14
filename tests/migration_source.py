"""Leitura estática do `upgrade()` das migrations, para os checks de contract e de travas."""

import ast
import re


def upgrade_function(source: str) -> ast.FunctionDef | None:
    """O `upgrade()` do módulo, ou None se a migration não tem um."""
    return next(
        (
            node
            for node in ast.parse(source).body
            if isinstance(node, ast.FunctionDef) and node.name == "upgrade"
        ),
        None,
    )


def is_marked(lines: list[str], line: int, mark: re.Pattern[str]) -> bool:
    """A marca está na linha `line` (1-based) ou numa linha só de comentário logo acima.

    Na linha acima vale só uma linha que é apenas o comentário: a marca de outra operação não
    cobre esta.
    """
    previous = lines[line - 2].strip() if line >= 2 else ""
    return bool(
        mark.search(lines[line - 1]) or (previous.startswith("#") and mark.search(previous))
    )


def string_constants(node: ast.AST) -> list[str]:
    """Os literais de texto dentro de `node`, inclusive dentro de `sa.text(...)`."""
    return [
        child.value
        for child in ast.walk(node)
        if isinstance(child, ast.Constant) and isinstance(child.value, str)
    ]
