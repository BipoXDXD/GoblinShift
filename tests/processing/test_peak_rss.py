import functools
import time

import pytest

from tests.processing.peak_rss import run_measuring_peak_rss


def test_alvo_que_passa_do_prazo_e_morto_e_falha_com_mensagem() -> None:
    started = time.monotonic()

    with pytest.raises(AssertionError, match=r"não terminou em 0\.5 s"):
        run_measuring_peak_rss(functools.partial(time.sleep, 30), timeout_s=0.5)

    # O filho morto não segura o teste: sem o `kill`, o `join` esperaria os 30 s do alvo.
    assert time.monotonic() - started < 10
