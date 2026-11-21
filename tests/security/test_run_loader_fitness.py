"""Nenhuma rota lê a Run sem o dono (1.6, isolamento por conta).

`load_run_any_owner` existe para a manutenção e o worker, que agem sobre qualquer conta. Em
`goblinshift.entrypoints`, uma Run só se lê por `load_owned_run`: uma rota nova que esquecesse o
dono carregaria a Run de outra conta sem erro. O import-linter trabalha por módulo e não separa
as duas funções de `runs.store`, então a regra é verificada pela árvore sintática.
"""

import ast
from pathlib import Path

ENTRYPOINTS = Path(__file__).resolve().parents[2] / "src" / "goblinshift" / "entrypoints"
INTERNAL_LOADER = "load_run_any_owner"


def references_to(name: str, source: str) -> list[int]:
    """Linhas que importam, chamam ou acessam `name`, inclusive como atributo de módulo."""
    return sorted(
        node.lineno
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.alias | ast.Name | ast.Attribute) and _mentions(node, name)
    )


def _mentions(node: ast.alias | ast.Name | ast.Attribute, name: str) -> bool:
    match node:
        case ast.alias():
            return name in {node.name, node.asname}
        case ast.Name():
            return node.id == name
        case ast.Attribute():
            return node.attr == name


def test_verificador_acha_import_chamada_e_atributo() -> None:
    source = (
        f"from goblinshift.runs.store import {INTERNAL_LOADER} as loader\n"
        "from goblinshift.runs import store\n"
        f"store.{INTERNAL_LOADER}(session, run_id)\n"
        f"{INTERNAL_LOADER}(session, run_id)\n"
    )

    assert references_to(INTERNAL_LOADER, source) == [1, 3, 4]


def test_entrypoints_nao_usam_a_leitura_sem_dono() -> None:
    modules = sorted(ENTRYPOINTS.rglob("*.py"))
    assert modules, f"nenhum módulo em {ENTRYPOINTS}"

    offenders = {
        str(module.relative_to(ENTRYPOINTS)): lines
        for module in modules
        if (lines := references_to(INTERNAL_LOADER, module.read_text(encoding="utf-8")))
    }

    assert offenders == {}, f"use `load_owned_run` com o dono da credencial: {offenders}"
