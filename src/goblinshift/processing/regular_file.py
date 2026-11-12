"""Abre uma saída do sandbox para leitura: sem seguir symlink e sem bloquear em FIFO.

O sandbox escreve em out/ e pode trocar um arquivo, depois de quem lê o ter conferido com `lstat`
(TOCTOU). `O_NOFOLLOW` barra o symlink; um FIFO, não: `open(O_RDONLY)` nele bloqueia até aparecer
um escritor e prende a thread de quem chama. Por isso o `open` leva `O_NONBLOCK`, o tipo é
conferido no próprio descritor (`fstat`) e só o arquivo regular volta ao modo bloqueante de
sempre.

Fica em `processing` porque o empacotador roda na imagem do executor, que só leva este pacote; a
coleta e a verificação do pacote, no worker, usam a mesma função.
"""

import fcntl
import os
import stat
from pathlib import Path
from typing import BinaryIO


class NotRegularFileError(OSError):
    """O caminho abriu, mas não é um arquivo regular (FIFO, diretório, dispositivo...)."""


def open_regular(path: Path) -> BinaryIO:
    """Abre `path` para leitura binária; symlink é recusado pelo `open` e o resto, por tipo.

    Levanta `NotRegularFileError` (um `OSError`) se não for arquivo regular. O descritor nunca
    vaza: em qualquer erro depois do `open`, é fechado aqui.
    """
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        # Antes do `fdopen`, que recusa diretório sem fechar o descritor recebido.
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise NotRegularFileError(f"saída não é arquivo regular: {path.name!r}")
        # Só arquivo regular chega aqui: devolve o descritor ao modo bloqueante.
        fcntl.fcntl(fd, fcntl.F_SETFL, fcntl.fcntl(fd, fcntl.F_GETFL) & ~os.O_NONBLOCK)
        return os.fdopen(fd, "rb")
    except BaseException:
        os.close(fd)
        raise
