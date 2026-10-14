"""Limites e prazos de arquivos (docs/GoblinShiftPlan.md, 1.7 e 3.4)."""

from datetime import timedelta

MIB = 1024 * 1024
GIB = 1024 * MIB

MAX_FILE_BYTES = 32 * MIB
MAX_BATCH_FILES = 100
MAX_BATCH_BYTES = 512 * MIB
# Igual ao CHECK da coluna `upload.original_name`, contado em caracteres.
MAX_NAME_CHARS = 255

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
