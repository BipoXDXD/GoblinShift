"""Troca os parâmetros do template de uma rota por valores concretos, falhando fechado.

Os testes que percorrem todas as rotas registradas (fitness, CSRF, cabeçalhos) montam a URL a
partir do template. Um parâmetro que a regex não reconhecesse ficaria literal na URL, a rota
responderia 404 e o teste passaria sem exercitar a regra: por isso sobra de `{` ou `}` é erro.
"""

import re
from collections.abc import Callable

# Nome de identificador (maiúsculas e dígitos incluídos), com o conversor opcional do Starlette
# (`{run_id:uuid}`, `{rest:path}`); o valor é escolhido pelo nome, sem o conversor.
_PATH_PARAM = re.compile(r"\{(?P<name>[A-Za-z_][A-Za-z0-9_]*)(?::[A-Za-z_]+)?\}")


def fill_path_params(template: str, value_for: Callable[[str], str]) -> str:
    concrete = _PATH_PARAM.sub(lambda match: value_for(match["name"]), template)
    if "{" in concrete or "}" in concrete:
        raise ValueError(f"parâmetro de caminho não reconhecido em {template!r}")
    return concrete
