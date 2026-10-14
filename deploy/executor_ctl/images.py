"""Allowlist das imagens do executor, gravada pelo deploy e relida a cada start.

O arquivo pertence ao usuário do deploy (`goblinshift`), e quem o lê aqui é o root: ele é aberto
sem seguir symlink e com tamanho limitado, e o conteúdo nunca vai para log nem resposta.
"""

import json
import os
import re
from pathlib import Path

# Só referências fixadas por digest: uma tag pode passar a apontar para outra imagem.
IMAGE_REFERENCE = re.compile(r"[a-z0-9][a-z0-9._/:-]{0,254}@sha256:[0-9a-f]{64}")
MAX_ALLOWLIST_BYTES = 64 * 1024


class ImageNotAllowedError(Exception):
    """A imagem pedida não está na allowlist, ou a allowlist não pôde ser lida."""


def require_allowed(images_file: Path, image: str) -> None:
    if image not in _read_allowlist(images_file):
        raise ImageNotAllowedError("imagem fora da allowlist do executor")


def _read_allowlist(images_file: Path) -> frozenset[str]:
    # Ilegível ou malformada vale como vazia: na dúvida, nenhuma imagem roda.
    try:
        fd = os.open(images_file, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as file:
            raw = file.read(MAX_ALLOWLIST_BYTES + 1)
    except OSError as error:
        raise ImageNotAllowedError(f"allowlist ilegível ({type(error).__name__})") from error
    if len(raw) > MAX_ALLOWLIST_BYTES:
        raise ImageNotAllowedError("allowlist acima do tamanho máximo")
    try:
        content = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ImageNotAllowedError("allowlist não é JSON válido") from error
    allowed = content.get("allowed") if isinstance(content, dict) else None
    if not isinstance(allowed, list) or not all(
        isinstance(entry, str) and IMAGE_REFERENCE.fullmatch(entry) for entry in allowed
    ):
        raise ImageNotAllowedError('allowlist precisa ser {"allowed": ["<ref>@sha256:<hex>"]}')
    return frozenset(allowed)
