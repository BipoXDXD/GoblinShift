"""Confere as citações de código do modelo de ameaças por símbolo e trecho, sem número de linha.

Formas aceitas:
- `` `arquivo.py` `Símbolo` `trecho` ``: o símbolo (função, classe, `Classe.método`, função
  aninhada ou nome atribuído no módulo) é localizado pelo `ast`, e o trecho literal precisa
  aparecer no código-fonte dele, decoradores incluídos, exatamente uma vez. Trecho igual ao nome
  do símbolo não prova nada e é recusado;
- num nome atribuído (constante, tabela, dict de cabeçalhos), o trecho não pode ser só um
  identificador com pontuação (`metadata,`). Se a instrução cabe numa linha, o trecho é a
  instrução inteira, para que `= 2` não continue valendo com `= 200`. Se ocupa várias, o trecho
  (sem a vírgula final) é exatamente um item do valor: elemento de tupla, lista ou set, par
  `chave: valor` de dict, ou argumento posicional ou keyword de chamada. A busca desce só por
  elementos de tupla, lista ou set e por argumentos posicionais de chamada
  (`ForeignKeyConstraint` dentro de `Table`); não entra em valor de keyword nem de dict
  (`info={...}`), nem em `*`, `and` ou outra expressão, que tiram o item do valor efetivo. Uma
  linha solta não basta: outro item pode anular o citado sem tocá-lo. Por isso nenhum irmão pode
  repetir a chave do item citado (primeiro elemento da tupla, chave do dict, nome do keyword),
  comparada sem caixa quando é texto, já que, conforme quem lê, vence a primeira ocorrência ou a
  última; e o valor do item citado não pode ter irmão `*` ou `**`, cuja chave não se conhece;
- `` `arquivo` `trecho` `` para arquivo que não é Python (Nginx, Dockerfile...): o trecho precisa
  aparecer exatamente uma vez no arquivo inteiro.

A unicidade garante que o trecho aponta um único lugar, e editar código acima dele não reprova
nada. A citação `arquivo:linha` é recusada para não voltar.

Em célula de tabela, `\\|` no trecho vale `|`, como o GFM o mostra. Arquivo Python com quebra de
linha que o `ast` não conta (form feed, U+2028...) é recusado, porque o recorte sairia torto.
"""

import ast
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

_PATH = r"(?:src|deploy|docker|tests)/[^`\s:]+"
_CODE_REF = re.compile(rf"`(?P<path>{_PATH})` `(?P<first>[^`]+)`(?: `(?P<second>[^`]+)`)?")
_LINE_REF = re.compile(rf"`{_PATH}:\d+(?:-\d+)?`")
_DOTTED_NAME = re.compile(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*")
# Um identificador solto, com pontuação em volta (`metadata,`): não prova nada sobre o valor.
_BARE_IDENTIFIER = re.compile(r"\W*[A-Za-z_]\w*\W*")
# As únicas quebras de linha que o `ast` conta em `lineno`; `str.splitlines` aceita outras.
_AST_LINE_BREAK = re.compile(r"\r\n|\r|\n")

Definition = ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef
_Named = Definition | ast.Assign | ast.AnnAssign


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
    node = resolve_symbol(source, symbol)
    if isinstance(node, str):
        return node
    segment = _segment(source, node)
    if excerpt == _name(node):
        return "o trecho é só o nome do símbolo"
    if not isinstance(node, Definition):
        problem = _assignment_problem(source, node, excerpt)
        if problem is not None:
            return problem
    return _uniqueness_problem(segment.count(excerpt), "no símbolo")


def resolve_symbol(source: str, symbol: str) -> _Named | str:
    """O nó do símbolo pontuado, ou o motivo pelo qual ele não designa um único nó.

    Cada parte do nome é procurada só entre as definições e atribuições do escopo da anterior
    (`Classe.método`, `função.aninhada`), atravessando `if`/`try`/`with`. O símbolo precisa
    existir e ser definido uma vez só: com duas definições, a citação não diz qual vale.
    """
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
    return candidates[0]


def _ast_line_count(source: str) -> int:
    lines = _AST_LINE_BREAK.split(source)
    return len(lines) - 1 if lines[-1] == "" else len(lines)


def _assignment_problem(source: str, node: ast.Assign | ast.AnnAssign, excerpt: str) -> str | None:
    if _BARE_IDENTIFIER.fullmatch(excerpt):
        return "o trecho de uma atribuição é só um identificador"
    if node.end_lineno == node.lineno:
        if excerpt != ast.get_source_segment(source, node):
            return "numa atribuição de uma linha, o trecho é a instrução inteira"
        return None
    return _item_problem(source, node.value, excerpt.removesuffix(","))


def _item_problem(source: str, value: ast.expr | None, wanted: str) -> str | None:
    """O trecho é um item do valor; a chave dele não se repete entre os irmãos."""
    for group in _item_groups(source, value):
        for item in group:
            if item.text != wanted:
                continue
            if any(other.unpacks for other in group):
                return "o valor do item citado tem `*` ou `**`, que pode repetir a chave"
            sibling_keys = [other.key for other in group]
            if item.key is not None and sibling_keys.count(item.key) > 1:
                return "a chave do item citado se repete no mesmo valor"
            return None
    return "numa atribuição de várias linhas, o trecho é um item do valor atribuído"


@dataclass(frozen=True)
class _Item:
    text: str
    key: str | None
    child: ast.expr | None
    unpacks: bool = False


def _item_groups(source: str, value: ast.expr | None) -> Iterator[list[_Item]]:
    """Os itens de cada container ou chamada, um grupo por container ou chamada.

    Desce só por elementos de tupla, lista ou set e por argumentos posicionais de chamada. Não
    desce por valor de keyword nem de dict (`info={...}`), nem por `*`, `and`, `if` ou outra
    expressão: um item escondido neles não está no valor efetivo.
    """
    items = _direct_items(source, value)
    if items:
        yield items
    for item in items:
        yield from _item_groups(source, item.child)


def _direct_items(source: str, value: ast.expr | None) -> list[_Item]:
    match value:
        case ast.Tuple(elts=elts) | ast.List(elts=elts) | ast.Set(elts=elts):
            return [_positional(source, elt, _first_element(elt)) for elt in elts]
        case ast.Dict(keys=keys, values=values):
            return [
                _Item(_text(source, key or item, item), _key(key), None, unpacks=key is None)
                for key, item in zip(keys, values, strict=True)
            ]
        case ast.Call(args=args, keywords=keywords):
            positional = [_positional(source, arg, None) for arg in args]
            named = [
                _Item(_text(source, kw, kw), kw.arg, None, unpacks=kw.arg is None)
                for kw in keywords
            ]
            return positional + named
    return []


def _positional(source: str, node: ast.expr, key: str | None) -> _Item:
    if isinstance(node, ast.Starred):
        return _Item(_text(source, node, node), None, None, unpacks=True)
    return _Item(_text(source, node, node), key, node)


def _first_element(element: ast.expr) -> str | None:
    if isinstance(element, ast.Tuple | ast.List) and element.elts:
        return _key(element.elts[0])
    return None


def _key(node: ast.expr | None) -> str | None:
    """Chave de texto sem caixa: cabeçalho HTTP é o mesmo em `Cache-Control` e `cache-control`."""
    if node is None:
        return None
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return repr(node.value.casefold())
    return ast.unparse(node)


def _text(source: str, first: ast.expr | ast.keyword, last: ast.expr | ast.keyword) -> str:
    """O código-fonte do início de `first` ao fim de `last` (um par `chave: valor` de dict)."""
    span = ast.Constant(value=None)
    span.lineno, span.col_offset = first.lineno, first.col_offset
    span.end_lineno, span.end_col_offset = last.end_lineno, last.end_col_offset
    return ast.get_source_segment(source, span) or ""


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
        if not isinstance(child, Definition):
            found.extend(_named_children(child))
    return found


def _name(node: _Named) -> str | None:
    if isinstance(node, Definition):
        return node.name
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    names = [target.id for target in targets if isinstance(target, ast.Name)]
    return names[0] if len(names) == 1 else None


def _segment(source: str, node: _Named) -> str:
    """Linhas inteiras do nó, a partir do primeiro decorador."""
    decorators = node.decorator_list if isinstance(node, Definition) else []
    first = min([node.lineno, *(decorator.lineno for decorator in decorators)])
    return "\n".join(source.splitlines()[first - 1 : node.end_lineno])
