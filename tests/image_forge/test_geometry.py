"""`fit_size`: o `contain` de 1.2, sem ampliar, truncando e com mínimo de um pixel."""

from fractions import Fraction

import pytest
from hypothesis import given
from hypothesis import strategies as st

from goblinshift.processing.image_forge.geometry import fit_size


@pytest.mark.parametrize(
    ("size", "box", "expected"),
    [
        ((2400, 1200), (1600, 1600), (1600, 800)),  # exemplo de 1.2
        ((200, 100), (1600, 1600), (200, 100)),  # não amplia
        ((1, 1), (16, 16), (1, 1)),
        ((16, 16), (16, 16), (16, 16)),  # exatamente no limite
        ((17, 16), (16, 16), (16, 15)),  # um pixel acima: trunca, não arredonda
        ((4097, 1), (4096, 4096), (4096, 1)),
        ((10000, 1), (16, 16), (16, 1)),  # a outra dimensão nunca chega a zero
        ((1, 10000), (16, 16), (1, 16)),
        ((8192, 1), (4096, 4096), (4096, 1)),
        ((3000, 4000), (1600, 1600), (1200, 1600)),  # retrato
        ((1000, 999), (100, 100), (100, 99)),  # 99,9 truncado para 99
    ],
)
def test_fit_size_cabe_na_caixa_sem_ampliar(
    size: tuple[int, int], box: tuple[int, int], expected: tuple[int, int]
) -> None:
    assert fit_size(*size, *box) == expected


@pytest.mark.parametrize("size", [(0, 10), (10, 0), (-1, 10)])
def test_fit_size_recusa_dimensao_nao_positiva(size: tuple[int, int]) -> None:
    with pytest.raises(ValueError, match="positiv"):
        fit_size(*size, 16, 16)


def test_fit_size_recusa_caixa_nao_positiva() -> None:
    with pytest.raises(ValueError, match="positiv"):
        fit_size(10, 10, 0, 16)


_side = st.integers(min_value=1, max_value=20_000)
_box = st.integers(min_value=1, max_value=5000)


@given(_side, _side, _box, _box)
def test_resultado_cabe_na_caixa_e_no_original(w: int, h: int, bw: int, bh: int) -> None:
    fw, fh = fit_size(w, h, bw, bh)
    assert 1 <= fw <= min(w, bw)
    assert 1 <= fh <= min(h, bh)


@given(_side, _side, _box, _box)
def test_fit_size_e_idempotente(w: int, h: int, bw: int, bh: int) -> None:
    once = fit_size(w, h, bw, bh)
    assert fit_size(*once, bw, bh) == once


@given(_side, _side, _box, _box)
def test_ao_reduzir_uma_dimensao_encosta_no_limite(w: int, h: int, bw: int, bh: int) -> None:
    fw, fh = fit_size(w, h, bw, bh)
    if (fw, fh) != (w, h):
        assert fw == bw or fh == bh


@given(_side, _side, _box, _box)
def test_cada_dimensao_perde_menos_de_um_pixel_da_escala_exata(
    w: int, h: int, bw: int, bh: int
) -> None:
    scale = min(Fraction(bw, w), Fraction(bh, h), Fraction(1))
    fw, fh = fit_size(w, h, bw, bh)
    assert 0 <= w * scale - fw < 1 or fw == 1
    assert 0 <= h * scale - fh < 1 or fh == 1
