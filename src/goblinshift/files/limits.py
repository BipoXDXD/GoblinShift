"""Limites e prazos de arquivos (docs/GoblinShiftPlan.md, 1.7 e 3.4)."""

from datetime import timedelta

KIB = 1024
MIB = 1024 * KIB
GIB = 1024 * MIB

MAX_FILE_BYTES = 32 * MIB
# 1.7: "vídeo, quando disponível, até 256 MiB" (Media Mill, ADR 036). O tipo vem da extensão do
# nome, que é só indício: o conteúdo é conferido no executor, e só a versão de receita que declara
# `max_input_bytes` acima de 32 MiB aceita a entrada maior na execução.
MAX_VIDEO_FILE_BYTES = 256 * MIB
VIDEO_EXTENSIONS = (".mp4", ".mov", ".webm")
MAX_BATCH_FILES = 100
MAX_BATCH_BYTES = 512 * MIB
# Cada intenção reserva ao menos isto na cota até ficar pronta ou vencer. Sem o piso, um lote de
# intenções de 0 B não reservava nada e as linhas se acumulavam por 1 h cada, limitadas só pelo rate
# limit; com ele, a cota de 4 GiB comporta 65.536 intenções abertas. No `ready` o objeto fica com o
# tamanho real e a sobra volta para a cota, então o piso só pesa enquanto a intenção está aberta.
MIN_INTENT_RESERVED_BYTES = 64 * KIB
# Uploads vivos (`created`, `receiving` ou `ready`, nem excluídos nem vencidos) por conta. O piso
# de reserva não alcança o upload vazio que chega a `ready`: ele reserva 0 B e vive 24 h, então só
# a cota de bytes deixava as linhas crescerem pelo rate limit (60 lotes de 100 por minuto). 1000 é
# dez lotes cheios, folga para o uso real, e põe teto nas linhas que cada conta mantém vivas.
MAX_LIVE_UPLOADS_PER_ACCOUNT = 1000
# Uploads criados pela conta nas últimas 24 h, inclusive excluídos e falhos (decisão do dono,
# 22/11). O teto de vivos não limita linhas: criar, enviar vazio e excluir solta a vaga na hora, e
# a linha excluída fica 90 dias para o histórico. 10.000 por dia é dez vezes o teto de vivos e põe
# teto nas linhas que cada conta acumula na retenção. O incompleto que a retenção remove (1 h) sai
# da contagem junto com a linha, e não acumula.
MAX_UPLOADS_CREATED_PER_DAY = 10_000
UPLOAD_CREATION_WINDOW = timedelta(hours=24)
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
# Chave, hash do corpo e posição de um lote criado com Idempotency-Key (`upload_requests`): o
# replay só vale enquanto o lote vive (1 h), mas o Aviso de privacidade promete 24 h para os dados
# de idempotência, então a manutenção os anula depois disso.
UPLOAD_REQUEST_KEY_TTL = timedelta(hours=24)
# Temporário de gravação sem modificação há mais que isto foi abandonado por uma queda. Bem acima da
# transferência mais longa (10 min), para nunca atingir uma gravação viva.
STALE_TEMPORARY_TTL = INCOMPLETE_UPLOAD_TTL

# Transferências (1.7), contadas por direção (envio ou download).
MAX_TRANSFERS_PER_ACCOUNT = 2
MAX_TRANSFERS_PER_ENVIRONMENT = 4
TRANSFER_MAX_DURATION = timedelta(minutes=10)
TRANSFER_IDLE_TIMEOUT = timedelta(seconds=30)
# Vazão mínima do envio, cobrada pela média de relógio desde o início da leitura depois da
# carência; o tempo do servidor entre os pedaços entra, para pedaços minúsculos não esconderem um
# envio lento. Sem ela, 1 byte a cada 25 s nunca dispara o prazo sem progresso e segura a vaga os
# 10 min inteiros: duas contas ocupavam as 4 vagas do ambiente. 32 KiB/s (256 kbit/s) depois de
# 30 s é a decisão D3 do dono; a carência deixa a conexão arrancar sem ser cobrada.
TRANSFER_MIN_RATE_BYTES_PER_S = 32 * KIB
TRANSFER_MIN_RATE_GRACE = timedelta(seconds=30)
# A vaga vence sozinha um pouco depois da transferência mais longa possível: se a réplica cair
# sem liberá-la, a conta não fica travada; a folga cobre a transação final do envio.
TRANSFER_SLOT_LEASE = TRANSFER_MAX_DURATION + timedelta(minutes=1)

# Governor da manutenção (3.4): apagar é a direção perigosa, então cada ciclo tem teto.
PURGE_MAX_OBJECTS = 500
PURGE_MAX_BYTES = 2 * GIB
# Lote das expirações: transação curta mesmo com muitas linhas vencidas.
EXPIRY_BATCH_SIZE = 500
