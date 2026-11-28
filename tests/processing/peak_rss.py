"""Pico de memória (RSS) de uma função rodada num processo novo (ADR 062)."""

import multiprocessing
import resource
import sys
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from multiprocessing.connection import Connection

# Prazo para o alvo terminar no filho; os `heavy` levam segundos, então só um travamento chega lá.
DEFAULT_TIMEOUT_S = 300.0


@dataclass(frozen=True)
class Measured[T]:
    result: T
    # Pico de RSS do processo inteiro, em bytes: o que o limite de memória do executor vê.
    peak_bytes: int
    # Pico antes de chamar o alvo (interpretador e imports); `peak_bytes - baseline_bytes` é o que
    # o alvo acrescentou.
    baseline_bytes: int

    @property
    def growth_bytes(self) -> int:
        return self.peak_bytes - self.baseline_bytes


def run_measuring_peak_rss[T](
    target: Callable[[], T], timeout_s: float = DEFAULT_TIMEOUT_S
) -> Measured[T]:
    """Roda `target` num processo `spawn` e devolve o resultado com o pico de RSS do filho.

    O filho mede o próprio pico (`RUSAGE_SELF`) e o devolve pelo pipe: `RUSAGE_CHILDREN` no pai
    traria o maior filho que já terminou, inclusive o de outro teste. O alvo vai serializado para
    o filho, então é função de módulo ou `functools.partial` dela, nunca lambda; o resultado
    também volta serializado.

    `RUSAGE_SELF` não conta os netos: um alvo que abre subprocesso (ffmpeg, por exemplo) tem a
    memória dele fora da medida. Se o alvo não termina em `timeout_s`, o filho é morto e a
    chamada falha.
    """
    context = multiprocessing.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(target=_measure, args=(target, sender))
    process.start()
    sender.close()
    try:
        # `poll` também volta verdadeiro se o filho morreu: aí o `recv` levanta `EOFError`.
        if not receiver.poll(timeout_s):
            process.kill()
            process.join()
            raise AssertionError(f"o alvo não terminou em {timeout_s} s; o filho foi morto")
        outcome = receiver.recv()
    except EOFError:
        process.join()
        raise AssertionError(f"o filho terminou sem medir (código {process.exitcode})") from None
    finally:
        receiver.close()
    process.join()
    if isinstance(outcome, str):
        raise AssertionError(f"o alvo falhou no filho:\n{outcome}")
    measured: Measured[T] = outcome
    return measured


def _measure[T](target: Callable[[], T], sender: Connection) -> None:
    baseline = _own_peak_bytes()
    try:
        result = target()
    except Exception:  # o traceback volta ao pai como texto, que sempre serializa
        sender.send(traceback.format_exc())
    else:
        sender.send(Measured(result, _own_peak_bytes(), baseline))
    finally:
        sender.close()


def _own_peak_bytes() -> int:
    """`ru_maxrss` vem em KiB no Linux e em bytes no macOS.

    No macOS é um piso: a página que o sistema comprime na memória sai da conta. A medida que vale
    é a da CI (Linux), onde nada comprime.
    """
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak if sys.platform == "darwin" else peak * 1024
