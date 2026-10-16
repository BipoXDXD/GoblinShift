"""Limites e prazos de arquivos (docs/GoblinShiftPlan.md, 1.7 e 3.4)."""

from datetime import timedelta

MIB = 1024 * 1024
GIB = 1024 * MIB

MAX_FILE_BYTES = 32 * MIB
# 1.7: "vídeo, quando disponível, até 256 MiB" (Media Mill, ADR 036). O tipo vem da extensão do
# nome, que é só indício: o conteúdo é conferido no executor, e só a versão de receita que declara
# `max_input_bytes` acima de 32 MiB aceita a entrada maior na execução.
MAX_VIDEO_FILE_BYTES = 256 * MIB
VIDEO_EXTENSIONS = (".mp4", ".mov", ".webm")
MAX_BATCH_FILES = 100
MAX_BATCH_BYTES = 512 * MIB
# Igual ao CHECK da coluna `upload.original_name`, contado em caracteres.
MAX_NAME_CHARS = 255


def max_file_bytes_for(name: str) -> int:
    """Teto de bytes de um arquivo pelo nome declarado no envio."""
    return MAX_VIDEO_FILE_BYTES if name.casefold().endswith(VIDEO_EXTENSIONS) else MAX_FILE_BYTES


# Retenção (1.7): incompleto expira após 1 h sem atividade; pronto, após 24 h sem uso; entrada
# usada por uma execução, 48 h após o término dela, como as saídas.
INCOMPLETE_UPLOAD_TTL = timedelta(hours=1)
READY_UPLOAD_TTL = timedelta(hours=24)
ARTIFACT_TTL = timedelta(hours=48)
USED_INPUT_TTL = timedelta(hours=48)

# Transferências (1.7), contadas por direção (envio ou download).
MAX_TRANSFERS_PER_ACCOUNT = 2
MAX_TRANSFERS_PER_ENVIRONMENT = 4
TRANSFER_MAX_DURATION = timedelta(minutes=10)
TRANSFER_IDLE_TIMEOUT = timedelta(seconds=30)
# A vaga vence sozinha um pouco depois da transferência mais longa possível: se a réplica cair
# sem liberá-la, a conta não fica travada; a folga cobre a transação final do envio.
TRANSFER_SLOT_LEASE = TRANSFER_MAX_DURATION + timedelta(minutes=1)

# Governor da manutenção (3.4): apagar é a direção perigosa, então cada ciclo tem teto.
PURGE_MAX_OBJECTS = 500
PURGE_MAX_BYTES = 2 * GIB
# Lote das expirações: transação curta mesmo com muitas linhas vencidas.
EXPIRY_BATCH_SIZE = 500
