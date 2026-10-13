"""Sondas do teste de aceitação; rodam DENTRO do executor (docs/GoblinShiftPlan.md, 3.8).

Cada sonda imprime linhas JSON no stdout, e a última resume o que aconteceu. Quem julga é o
run.py, no host: a sonda só observa e relata.

Toda sonda tem um teto próprio abaixo do que causaria dano se o limite do container falhasse
(a VPS é compartilhada), então "chegou ao teto" é o sinal de que o limite não funcionou.
"""

import errno
import json
import os
import signal
import socket
import sys
import time
from collections.abc import Callable

WORK = "/work"
MIB = 1024**2
CHUNK_BYTES = 64 * MIB
PAGE_BYTES = 4096
# Abaixo do RLIMIT_FSIZE, para que na sonda de disco só o filesystem limite.
DISK_FILE_BYTES = 512 * MIB
# O dobro do RLIMIT_FSIZE do perfil (1 GiB): o arquivo único precisa esbarrar no limite.
FILE_SIZE_ATTEMPT_BYTES = 2 * 1024 * MIB
# 1,5 vez o limite de 2 GiB: passar disso é prova de que o limite não vale.
MEMORY_CEILING_BYTES = 3 * 1024 * MIB
# Bem acima do limite de 64 processos, mas longe de esgotar o host se o limite falhar.
FORK_CEILING = 256
CHILD_LIFETIME_S = 60
OUTBOUND_TARGET = ("1.1.1.1", 443)
DNS_TARGET = "gvisor.dev"
NETWORK_TIMEOUT_S = 3


def main() -> None:
    probes: dict[str, Callable[[], None]] = {
        "identity": probe_identity,
        "network": probe_network,
        "disk": probe_disk,
        "file_size": probe_file_size,
        "memory": probe_memory,
        "pids": probe_pids,
    }
    probes[sys.argv[1]]()


def probe_identity() -> None:
    # /var/tmp da imagem é 1777: só a raiz read-only impede o UID do executor de escrever ali.
    # Não serve /tmp: o gVisor pode montar um tmpfs próprio nele (relatado em tmp_filesystem).
    try:
        with open("/var/tmp/escrita-na-raiz", "w"):  # noqa: S108
            root_write_error = None
    except OSError as error:
        root_write_error = _errno_name(error)
    filesystems = _mounted_filesystems()
    emit(
        uid=os.getuid(),
        kernel=os.uname().release,
        root_write_error=root_write_error,
        root_filesystem=filesystems.get("/"),
        tmp_filesystem=filesystems.get("/tmp"),  # noqa: S108
        docker_sock_exists=os.path.exists("/var/run/docker.sock"),
    )


def _mounted_filesystems() -> dict[str, str]:
    """Ponto de montagem → "tipo opções", a partir de /proc/self/mounts."""
    filesystems = {}
    with open("/proc/self/mounts") as mounts:
        for line in mounts:
            _, mount_point, fs_type, options, *_ = line.split()
            filesystems[mount_point] = f"{fs_type} {options}"
    return filesystems


def probe_network() -> None:
    try:
        interfaces = sorted(name for _, name in socket.if_nameindex())
    except OSError as error:
        interfaces = [f"erro: {_errno_name(error)}"]
    try:
        socket.create_connection(OUTBOUND_TARGET, timeout=NETWORK_TIMEOUT_S).close()
        outbound_error = None
    except OSError as error:
        outbound_error = _errno_name(error)
    try:
        socket.getaddrinfo(DNS_TARGET, 443)
        dns_error = None
    except OSError as error:
        dns_error = type(error).__name__
    emit(interfaces=interfaces, outbound_error=outbound_error, dns_error=dns_error)


def probe_disk() -> None:
    """Escreve arquivos de 512 MiB até o filesystem recusar."""
    signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
    written = 0
    index = 0
    while True:
        try:
            written += _write_file(f"{WORK}/enche-{index:03d}.bin", DISK_FILE_BYTES)
        except _WriteStoppedError as stopped:
            written += stopped.written
            emit(error=stopped.errno_name, written_bytes=written)
            return
        index += 1


def probe_file_size() -> None:
    """Escreve um único arquivo até o RLIMIT_FSIZE recusar (ou a área acabar)."""
    signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
    try:
        written = _write_file(f"{WORK}/arquivo-grande.bin", FILE_SIZE_ATTEMPT_BYTES)
        emit(error=None, written_bytes=written)
    except _WriteStoppedError as stopped:
        emit(error=stopped.errno_name, written_bytes=stopped.written)


def probe_memory() -> None:
    """Aloca e toca blocos de 64 MiB; o esperado é o processo morrer antes de 2 GiB."""
    blocks: list[bytearray] = []
    allocated = 0
    while allocated < MEMORY_CEILING_BYTES:
        block = bytearray(CHUNK_BYTES)
        # Escreve em cada página: memória só reservada não conta para o limite.
        block[::PAGE_BYTES] = b"\x01" * (CHUNK_BYTES // PAGE_BYTES)
        blocks.append(block)
        allocated += CHUNK_BYTES
        emit(allocated_mib=allocated // MIB)
    emit(allocated_mib=allocated // MIB, reached_ceiling=True)


def probe_pids() -> None:
    """Cria processos que só dormem até o fork falhar; é um fork bomb com teto.

    Relata cada fork na hora: se o sandbox inteiro morrer, a última linha mostra até onde foi.
    """
    children: list[int] = []
    fork_error = None
    while len(children) < FORK_CEILING:
        try:
            pid = os.fork()
        except OSError as error:
            fork_error = _errno_name(error)
            break
        if pid == 0:
            time.sleep(CHILD_LIFETIME_S)
            os._exit(0)
        children.append(pid)
        emit(forked=len(children))
    for pid in children:
        os.kill(pid, signal.SIGKILL)
        os.waitpid(pid, 0)
    emit(forked=len(children), error=fork_error)


class _WriteStoppedError(Exception):
    def __init__(self, errno_name: str, written: int) -> None:
        super().__init__(errno_name)
        self.errno_name = errno_name
        self.written = written


def _write_file(path: str, size: int) -> int:
    """Escreve `size` bytes com fsync; o fsync força a escrita real, sem parar num cache."""
    chunk = memoryview(bytes(CHUNK_BYTES))
    written = 0
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        while written < size:
            written += os.write(fd, chunk[: min(CHUNK_BYTES, size - written)])
        os.fsync(fd)
    except OSError as error:
        raise _WriteStoppedError(_errno_name(error), written) from error
    finally:
        os.close(fd)
    return written


def _errno_name(error: OSError) -> str:
    if error.errno is None:
        return type(error).__name__
    return errno.errorcode.get(error.errno, str(error.errno))


def emit(**fields: object) -> None:
    print(json.dumps(fields), flush=True)


if __name__ == "__main__":
    main()
