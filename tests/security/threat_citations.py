"""Confere as citações de código do modelo de ameaças por símbolo e trecho, sem número de linha.

Formas aceitas:
- `` `arquivo.py` `Símbolo` `trecho` ``: o símbolo (função, classe, `Classe.método`, função
  aninhada ou nome atribuído no módulo) é localizado pelo `ast`, e o trecho literal precisa
  aparecer no código-fonte dele, decoradores incluídos, exatamente uma vez. Trecho igual ao nome
  do símbolo não prova nada e é recusado. Num nome atribuído (constante, tabela), o trecho é a
  instrução inteira ou uma linha inteira dela, para que `= 2` não continue valendo com `= 200`;
- `` `arquivo` `trecho` `` para arquivo que não é Python (Nginx, Dockerfile...): o trecho precisa
  aparecer exatamente uma vez no arquivo inteiro.

A unicidade garante que o trecho aponta um único lugar, e editar código acima dele não reprova
nada. A citação `arquivo:linha` é recusada para não voltar.

Em célula de tabela, `\\|` no trecho vale `|`, como o GFM o mostra. Arquivo Python com quebra de
linha que o `ast` não conta (form feed, U+2028...) é recusado, porque o recorte sairia torto.
"""

import ast
import re
from pathlib import Path

_PATH = r"(?:src|deploy|docker|tests)/[^`\s:]+"
_CODE_REF = re.compile(rf"`(?P<path>{_PATH})` `(?P<first>[^`]+)`(?: `(?P<second>[^`]+)`)?")
_LINE_REF = re.compile(rf"`{_PATH}:\d+(?:-\d+)?`")
_DOTTED_NAME = re.compile(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*")
# As únicas quebras de linha que o `ast` conta em `lineno`; `str.splitlines` aceita outras.
_AST_LINE_BREAK = re.compile(r"\r\n|\r|\n")

_Definition = ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef
_Named = _Definition | ast.Assign | ast.AnnAssign


def citation_problems(document: str, root: Path) -> list[str]:
    """Um texto por citação inválida, no formato "<citação>: <motivo>"; vazio se tudo confere."""
    problems = [
        f"{match[0]}: citação por número de linha; use `arquivo` `símbolo` `trecho`"
        for match in _LINE_REF.finditer(document)
    ]
    for match in _CODE_REF.finditer(document):
        second = None if match["second"] is None else _unescape(match["second"])
        reason = _problem_of(root, match["path"], _unescape(match["first"]), second)
        if reason is not None:
            problems.append(f"{match[0]}: {reason}")
    return problems


def _unescape(part: str) -> str:
    """Em célula de tabela GFM, o `|` do código é escrito `\\|`, mesmo dentro de code span."""
    return part.replace("\\|", "|")


def count_code_citations(document: str) -> int:
    return len(_CODE_REF.findall(document))


def _problem_of(root: Path, file: str, first: str, second: str | None) -> str | None:
    path = root / file
    is_python = file.endswith(".py")
    if is_python and second is None:
        return "citação de arquivo Python exige símbolo e trecho"
    if not is_python and second is not None:
        return "arquivo que não é Python leva só o trecho, sem símbolo"
    if is_python and not _DOTTED_NAME.fullmatch(first):
        return "o símbolo não é um nome pontuado válido"
    if not path.is_file():
        return "o arquivo não existe"
    source = path.read_text(encoding="utf-8")
    if second is None:
        return _uniqueness_problem(source.count(first), "no arquivo")
    return _python_problem(source, first, second)


def _python_problem(source: str, symbol: str, excerpt: str) -> str | None:
    if len(source.splitlines()) != _ast_line_count(source):
        return "o arquivo tem quebras de linha que o ast não conta"
    scopes: list[ast.AST] = [ast.parse(source)]
    candidates: list[_Named] = []
    for name in symbol.split("."):
        candidates = [
            found for scope in scopes for found in _named_children(scope) if _name(found) == name
        ]
        scopes = list(candidates)
    if not candidates:
        return "o símbolo não existe no arquivo"
    if len(candidates) > 1:
        return f"o símbolo está definido {len(candidates)} vezes no arquivo"
    node = candidates[0]
    segment = _segment(source, node)
    if excerpt == _name(node):
        return "o trecho é só o nome do símbolo"
    if not isinstance(node, _Definition) and not _is_whole_statement(source, node, excerpt):
        return "o trecho de uma constante é a instrução inteira ou uma linha inteira dela"
    return _uniqueness_problem(segment.count(excerpt), "no símbolo")


def _ast_line_count(source: str) -> int:
    lines = _AST_LINE_BREAK.split(source)
    return len(lines) - 1 if lines[-1] == "" else len(lines)


def _is_whole_statement(source: str, node: ast.Assign | ast.AnnAssign, excerpt: str) -> bool:
    whole_lines = {line.strip() for line in _segment(source, node).splitlines()}
    return excerpt == ast.get_source_segment(source, node) or excerpt in whole_lines


def _uniqueness_problem(count: int, where: str) -> str | None:
    if count == 0:
        return f"o trecho não aparece {where}"
    if count > 1:
        return f"o trecho aparece {count} vezes {where}"
    return None


def _named_children(scope: ast.AST) -> list[_Named]:
    """Definições e atribuições do escopo, atravessando `if`/`try`/`with`, sem entrar em outra."""
    found: list[_Named] = []
    for child in ast.iter_child_nodes(scope):
        if isinstance(child, _Named):
            found.append(child)
        if not isinstance(child, _Definition):
            found.extend(_named_children(child))
    return found


def _name(node: _Named) -> str | None:
    if isinstance(node, _Definition):
        return node.name
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    names = [target.id for target in targets if isinstance(target, ast.Name)]
    return names[0] if len(names) == 1 else None


def _segment(source: str, node: _Named) -> str:
    """Linhas inteiras do nó, a partir do primeiro decorador."""
    decorators = node.decorator_list if isinstance(node, _Definition) else []
    first = min([node.lineno, *(decorator.lineno for decorator in decorators)])
    return "\n".join(source.splitlines()[first - 1 : node.end_lineno])
