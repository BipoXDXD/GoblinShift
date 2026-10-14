"""Perfil fixo do `docker run` do executor (docs/GoblinShiftPlan.md, 3.6 e 1.7; ADR 015).

Quem chama não escolhe opção, montagem nem comando: o nome e os caminhos derivam do
`attempt_id` já validado como UUID, e o comando é o ENTRYPOINT da imagem.
"""

import uuid
from pathlib import Path

# Limites do executor (docs/GoblinShiftPlan.md, 1.7).
CPUS = 1
MEMORY_BYTES = 2 * 1024**3
# Processos e threads da receita: RLIMIT_NPROC, aplicado pelo próprio gVisor (o 65º fork recebe
# EAGAIN). O --pids-limit do Docker conta também as tarefas do gVisor no host (31 ociosas, mais
# cerca de 2 por processo da receita) e por isso é só a rede de segurança do sandbox (ADR 015).
PROCESS_LIMIT = 64
SANDBOX_TASKS_LIMIT = 256
# RLIMIT_FSIZE por arquivo: o dobro do maior arquivo de saída (ZIP de 512 MiB), para caber
# temporários; o teto da área inteira continua sendo o filesystem de 6 GiB (ADR 005).
FILE_SIZE_LIMIT_BYTES = 1024**3
# Log do container (stdout e stderr do executor) com limite próprio, sem depender do daemon.json
# do host compartilhado. O executor só escreve ali o diagnóstico de falha (uso, integridade,
# traceback), alguns KiB; 1 MiB num arquivo só basta e, com uma tentativa por vez, é o teto do
# que uma receita consegue gravar na raiz do host pelo stdout. Com um arquivo só, o driver
# `local` exige compressão desligada (não há arquivo girado para comprimir).
LOG_DRIVER = "local"
LOG_OPTIONS = {"max-size": "1m", "max-file": "1", "compress": "false"}
# UID sem conta no host, para que nada do executor coincida com um usuário real da VPS.
EXECUTOR_UID = 61000

WORK_MOUNT = "/work"
INPUTS_DIR = "inputs"
OUTPUT_DIR = "out"
ATTEMPT_LABEL = "goblinshift.attempt"


def container_name(attempt_id: uuid.UUID) -> str:
    # O tipo UUID já é a validação: um nome nunca é montado a partir de texto livre.
    return f"gs-exec-{attempt_id}"


def run_arguments(
    attempt_id: uuid.UUID, image: str, *, work_dir: Path, runtime: str, executor_gid: int
) -> list[str]:
    """Argumentos completos do `docker run`; a imagem é o último, sem comando depois dela."""
    options = run_options(attempt_id, work_dir=work_dir, runtime=runtime, executor_gid=executor_gid)
    return ["run", "--detach", *options, image]


def run_options(
    attempt_id: uuid.UUID, *, work_dir: Path, runtime: str, executor_gid: int
) -> list[str]:
    """As opções do perfil, sem a imagem; o teste de aceitação as reaproveita com as sondas."""
    memory = str(MEMORY_BYTES)
    return [
        f"--name={container_name(attempt_id)}",
        f"--label={ATTEMPT_LABEL}={attempt_id}",
        f"--runtime={runtime}",
        "--network=none",
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        f"--user={EXECUTOR_UID}:{executor_gid}",
        f"--pids-limit={SANDBOX_TASKS_LIMIT}",
        f"--ulimit=nproc={PROCESS_LIMIT}",
        f"--memory={memory}",
        f"--memory-swap={memory}",
        f"--cpus={CPUS}",
        f"--ulimit=fsize={FILE_SIZE_LIMIT_BYTES}",
        "--pull=never",
        f"--log-driver={LOG_DRIVER}",
        *(f"--log-opt={key}={value}" for key, value in LOG_OPTIONS.items()),
        f"--mount=type=bind,src={work_dir},dst={WORK_MOUNT}",
        f"--mount=type=bind,src={work_dir / INPUTS_DIR},dst={WORK_MOUNT}/{INPUTS_DIR},readonly",
    ]
