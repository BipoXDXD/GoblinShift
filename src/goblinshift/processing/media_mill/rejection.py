"""Recusa de um vídeo, com o código do catálogo de 1.6 e um motivo para o relatório (ADR 036)."""

from enum import StrEnum


class RejectionCode(StrEnum):
    # Contêiner, codec ou perfil fora da allowlist, ou arquivo danificado ou truncado.
    UNSUPPORTED_FORMAT = "UNSUPPORTED_FORMAT"
    # Duração, resolução ou quadros por segundo acima dos limites de 1.5.
    MEDIA_TOO_LARGE = "MEDIA_TOO_LARGE"
    # Faixa de áudio acima de 48 kHz ou de 2 canais: nada de downmix nem resampling implícito.
    AUDIO_LIMIT_EXCEEDED = "AUDIO_LIMIT_EXCEEDED"
    # Extração de áudio pedida para um vídeo sem a faixa indicada (ou sem áudio nenhum).
    AUDIO_STREAM_NOT_FOUND = "AUDIO_STREAM_NOT_FOUND"


class MediaRejected(Exception):  # noqa: N818 - é um desfecho do item, não um defeito
    """O vídeo é recusado; os demais do lote continuam (1.1).

    O motivo vai para o relatório do usuário: nunca traz caminho interno nem saída crua do FFmpeg.
    """

    def __init__(self, code: RejectionCode, reason: str) -> None:
        super().__init__(f"{code}: {reason}")
        self.code = code
        self.reason = reason
