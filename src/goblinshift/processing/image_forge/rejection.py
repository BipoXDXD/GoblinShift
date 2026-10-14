"""Recusa de um arquivo, com o código do catálogo de 1.6 e um motivo para o relatório."""

from enum import StrEnum


class RejectionCode(StrEnum):
    UNSUPPORTED_FORMAT = "UNSUPPORTED_FORMAT"
    UNSUPPORTED_BIT_DEPTH = "UNSUPPORTED_BIT_DEPTH"
    UNSUPPORTED_COLOR_PROFILE = "UNSUPPORTED_COLOR_PROFILE"
    IMAGE_TOO_LARGE = "IMAGE_TOO_LARGE"


class ImageRejected(Exception):  # noqa: N818 - é um desfecho do item, não um defeito
    """O arquivo é recusado; os demais do lote continuam (1.1).

    O motivo vai para o relatório do usuário: nunca traz caminho interno nem mensagem crua do
    decodificador.
    """

    def __init__(self, code: RejectionCode, reason: str) -> None:
        super().__init__(f"{code}: {reason}")
        self.code = code
        self.reason = reason
