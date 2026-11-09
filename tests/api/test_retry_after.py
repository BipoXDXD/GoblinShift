"""`Retry-After` em segundos inteiros, nunca antes da hora nem zero (1.6, 1.7)."""

from datetime import timedelta

import pytest

from goblinshift.entrypoints.api.capacity_problems import retry_after


@pytest.mark.parametrize(
    ("wait", "expected"),
    [
        pytest.param(timedelta(seconds=-5), "1", id="ja-passou"),
        pytest.param(timedelta(0), "1", id="zero"),
        pytest.param(timedelta(milliseconds=1), "1", id="fracao-de-segundo"),
        pytest.param(timedelta(seconds=1), "1", id="um-segundo"),
        pytest.param(timedelta(seconds=1, milliseconds=1), "2", id="logo-acima-de-um"),
        pytest.param(timedelta(hours=24), "86400", id="janela-inteira"),
    ],
)
def test_retry_after_arredonda_para_cima_com_minimo_de_um_segundo(
    wait: timedelta, expected: str
) -> None:
    assert retry_after(wait) == {"Retry-After": expected}
