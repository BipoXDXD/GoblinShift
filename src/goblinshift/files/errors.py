"""Erros do módulo `files`, no vocabulário do serviço; a rota HTTP os traduz (1.6).

Recurso inexistente e recurso de outra conta produzem o mesmo erro, com a mesma mensagem, para
não permitir enumeração.
"""

from goblinshift.files.states import UploadState


class FilesError(Exception):
    """Base dos erros de serviço de `files`."""


class InvalidUploadRequestError(FilesError):
    """Pedido de intenções inválido (→ 422), com o campo e o motivo."""

    def __init__(self, field: str, reason: str) -> None:
        super().__init__(f"{field}: {reason}")
        self.field = field
        self.reason = reason


class UploadNotFoundError(FilesError):
    """Inexistente ou de outra conta (→ 404)."""

    def __init__(self) -> None:
        super().__init__("upload não encontrado")


class UploadGoneError(FilesError):
    """Do próprio dono, mas excluído ou expirado (→ 410)."""

    def __init__(self) -> None:
        super().__init__("upload excluído ou expirado")


class UploadStateConflictError(FilesError):
    """O upload existe, mas o estado não aceita a operação (→ 409)."""

    def __init__(self, state: UploadState) -> None:
        super().__init__(f"upload em estado {state.value} não aceita conteúdo")
        self.state = state


class UploadSizeMismatchError(FilesError):
    """Bytes recebidos diferentes do tamanho declarado; o upload terminou `failed` (→ 422)."""

    def __init__(self, expected_size: int) -> None:
        super().__init__(f"conteúdo recebido difere do tamanho declarado ({expected_size} bytes)")
        self.expected_size = expected_size


class InputNotAvailableError(FilesError):
    """Algum upload citado não está disponível (→ 422 `INPUT_NOT_AVAILABLE`).

    Único para todos os motivos (inexistente, alheio, expirado, excluído, incompleto), sem dizer
    qual ID falhou, para não permitir enumeração.
    """

    def __init__(self) -> None:
        super().__init__("entrada indisponível")


class ArtifactNotFoundError(FilesError):
    """Inexistente ou de outra conta (→ 404)."""

    def __init__(self) -> None:
        super().__init__("artefato não encontrado")


class ArtifactGoneError(FilesError):
    """Do próprio dono, mas excluído ou expirado (→ 410)."""

    def __init__(self) -> None:
        super().__init__("artefato excluído ou expirado")


class ObjectNotAvailableError(FilesError):
    """`hold` pedido para objeto que não está `committed` (excluído, pendente ou ausente)."""

    def __init__(self) -> None:
        super().__init__("objeto indisponível para novo uso")
