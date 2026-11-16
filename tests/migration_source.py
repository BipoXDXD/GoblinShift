"""Leitura estática do `upgrade()` das migrations, para os checks de contract e de travas."""

import ast
import io
import re
import tokenize
from typing import NamedTuple


class Comment(NamedTuple):
    text: str
    alone: bool
    """O comentário é tudo o que há na linha."""


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


def comments(source: str) -> dict[int, Comment]:
    """Os comentários do código por linha (1-based); texto dentro de string não conta."""
    return {
        token.start[0]: Comment(token.string, not token.line[: token.start[1]].strip())
        for token in tokenize.generate_tokens(io.StringIO(source).readline)
        if token.type == tokenize.COMMENT
    }


def is_marked(found: dict[int, Comment], line: int, mark: re.Pattern[str]) -> bool:
    """A marca está num comentário da linha `line` ou numa linha só de comentário logo acima.

    Na linha acima vale só uma linha que é apenas o comentário: a marca de outra operação não
    cobre esta. Só comentário marca: o mesmo texto dentro do SQL não autoriza a si mesmo.
    """
    here, above = found.get(line), found.get(line - 1)
    return bool(
        (here and mark.search(here.text)) or (above and above.alone and mark.search(above.text))
    )


def string_constants(node: ast.AST) -> list[str]:
    """Os literais de texto dentro de `node`, inclusive dentro de `sa.text(...)`."""
    return [
        child.value
        for child in ast.walk(node)
        if isinstance(child, ast.Constant) and isinstance(child.value, str)
    ]
