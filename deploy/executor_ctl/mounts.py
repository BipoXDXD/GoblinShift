"""Opções de montagem lidas de `/proc/self/mountinfo` (item 36b, D12).

Formato (proc(5)): `id pai maior:menor raiz ponto opções [campos opcionais] - tipo origem super`.
`nosuid`, `nodev` e `noexec` são opções por montagem e saem na sexta coluna.
"""

import re
from pathlib import Path

MOUNTINFO_PATH = Path("/proc/self/mountinfo")
_MOUNT_POINT_FIELD = 4
_OPTIONS_FIELD = 5
_OCTAL_ESCAPE = re.compile(r"\\([0-7]{3})")


def mount_options(mountinfo_text: str, path: Path) -> frozenset[str]:
    """Opções da montagem cujo ponto é exatamente `path`; vazio se `path` não for um ponto.

    Com montagens empilhadas no mesmo ponto vale a última da lista, a visível.
    """
    options: frozenset[str] = frozenset()
    for line in mountinfo_text.splitlines():
        fields = line.split()
        if len(fields) <= _OPTIONS_FIELD:
            continue
        if Path(_unescape(fields[_MOUNT_POINT_FIELD])) == path:
            options = frozenset(fields[_OPTIONS_FIELD].split(","))
    return options


def read_self_mountinfo() -> str:
    return MOUNTINFO_PATH.read_text(encoding="utf-8", errors="surrogateescape")


def _unescape(field: str) -> str:
    # O kernel escreve espaço, tab, quebra de linha e barra invertida como `\ooo`.
    return _OCTAL_ESCAPE.sub(lambda match: chr(int(match.group(1), 8)), field)
