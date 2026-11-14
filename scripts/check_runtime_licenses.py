"""Recusa pacote Python GPL ou AGPL entre os instalados de um ambiente de runtime.

Roda dentro das imagens publicadas (só stdlib, porque a imagem não tem as ferramentas de dev) e
nos testes, sobre as dependências de runtime do lock. Ferramentas GPL podem ficar no grupo `dev`,
que nunca entra em imagem; ali, uma delas viraria distribuição junto com o código Apache-2.0
(ADR 057, pglast). LGPL é aceita: só a GPL e a AGPL alcançariam o código do projeto.

Lê a licença de três campos dos metadados: `License-Expression` (SPDX), `License` e os
classifiers `License ::`. Pacote do `DEV_ONLY` é recusado pelo nome, qualquer que seja a licença.

Uso: python check_runtime_licenses.py [diretório de site-packages ...]
Sem argumento, confere o `sys.path` do interpretador. Sai com 1 se achar pacote recusado e com 2
se não achar nenhum pacote (caminho errado conferiria um ambiente vazio e passaria).
"""

import re
import sys
from collections.abc import Iterable, Sequence
from importlib.metadata import Distribution, distributions

# Ferramentas que só existem no grupo dev; o nome basta, mesmo que a licença mude.
DEV_ONLY = frozenset({"pglast"})
# SPDX (GPL-3.0-or-later, AGPL-3.0-only) e texto livre ("GPLv3"), mas não LGPL nem "GPL-compatible".
SPDX_COPYLEFT = re.compile(r"(?<![A-Za-z])A?GPL(?=v\d|[^A-Za-z]|$)(?!-compatible)", re.IGNORECASE)
# Classifiers: "GNU General Public License" e "GNU Affero General Public License", não a Lesser.
CLASSIFIER_COPYLEFT = re.compile(r"GNU (Affero )?General Public License")
EXIT_REFUSED = 1
EXIT_EMPTY = 2


def main(argv: Sequence[str]) -> int:
    paths = list(argv) or None
    installed = list(distributions(path=paths) if paths else distributions())
    if not installed:
        print("check_runtime_licenses: nenhum pacote encontrado", file=sys.stderr)
        return EXIT_EMPTY
    refused = refused_packages(installed)
    for line in refused:
        print(f"check_runtime_licenses: recusado: {line}", file=sys.stderr)
    print(f"check_runtime_licenses: {len(installed)} pacotes conferidos, {len(refused)} recusados")
    return EXIT_REFUSED if refused else 0


def refused_packages(installed: Iterable[Distribution]) -> list[str]:
    """`nome versão (motivo)` de cada pacote recusado, em ordem de nome."""
    refused = []
    for distribution in installed:
        name = normalized_name(distribution.metadata.get("Name") or "")
        reason = refusal_reason(name, distribution)
        if reason:
            refused.append(f"{name} {distribution.version} ({reason})")
    return sorted(refused)


def refusal_reason(name: str, distribution: Distribution) -> str:
    if name in DEV_ONLY:
        return "ferramenta só de dev"
    metadata = distribution.metadata
    expression = metadata.get("License-Expression") or ""
    # O campo License às vezes traz o texto inteiro da licença: a primeira linha basta.
    declared = (metadata.get("License") or "").strip().splitlines()[:1]
    for text in (expression, *declared):
        if SPDX_COPYLEFT.search(text):
            return text
    for classifier in map(str, metadata.get_all("Classifier") or ()):
        if classifier.startswith("License ::") and CLASSIFIER_COPYLEFT.search(classifier):
            return classifier
    return ""


def normalized_name(name: str) -> str:
    """Nome como o PEP 503 o compara: minúsculas e `-` no lugar de `_`, `.` e repetições."""
    return re.sub(r"[-_.]+", "-", name).lower()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
