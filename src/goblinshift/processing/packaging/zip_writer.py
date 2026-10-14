"""Escrita do ZIP determinístico (3.6, 3.9). A escrita em si é da frente A da fatia 5."""

import zipfile
from pathlib import PurePosixPath

# Formatos que já chegam comprimidos: DEFLATE só gastaria CPU.
_STORED_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".zip"})


def compression_for(arcname: str) -> int:
    """`ZIP_STORED` para .jpg, .jpeg, .png e .zip (qualquer caixa); `ZIP_DEFLATED` no resto."""
    suffix = PurePosixPath(arcname).suffix.casefold()
    return zipfile.ZIP_STORED if suffix in _STORED_SUFFIXES else zipfile.ZIP_DEFLATED
