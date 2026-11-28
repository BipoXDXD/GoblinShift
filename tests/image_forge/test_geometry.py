"""`fit_size`: o `contain` de 1.2, sem ampliar, truncando e com mínimo de um pixel."""

from fractions import Fraction

import pytest
from hypothesis import given
from hypothesis import strategies as st

from goblinshift.processing.image_forge.geometry import FitMode, Layout, fit_size, layout


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


# --- `layout`: os quatro modos de ajuste da v2, todos sem ampliar (D5) ---


def test_cover_corta_o_centro_e_preenche_a_caixa() -> None:
    assert layout(2400, 1200, fit=FitMode.COVER, box=(1080, 1080), scale_percent=100) == Layout(
        crop=(600, 0, 1200, 1200), content=(1080, 1080), canvas=(1080, 1080), offset=(0, 0)
    )


def test_cover_menor_que_a_caixa_so_corta_na_proporcao() -> None:
    result = layout(800, 400, fit=FitMode.COVER, box=(1080, 1080), scale_percent=100)
    assert (result.crop, result.canvas) == ((200, 0, 400, 400), (400, 400))


def test_cover_em_retrato_corta_em_cima_e_embaixo() -> None:
    result = layout(1000, 3001, fit=FitMode.COVER, box=(1000, 1000), scale_percent=100)
    assert result.crop == (0, 1000, 1000, 1000)


def test_contain_e_o_fit_size_da_v1() -> None:
    result = layout(2400, 1200, fit=FitMode.CONTAIN, box=(1600, 1600), scale_percent=100)
    assert result == Layout(
        crop=(0, 0, 2400, 1200), content=(1600, 800), canvas=(1600, 800), offset=(0, 0)
    )


def test_pad_centraliza_o_conteudo_na_caixa_exata() -> None:
    result = layout(2400, 1200, fit=FitMode.PAD, box=(1600, 1600), scale_percent=100)
    assert (result.content, result.canvas, result.offset) == ((1600, 800), (1600, 1600), (0, 400))


def test_pad_com_imagem_menor_nao_amplia() -> None:
    result = layout(401, 200, fit=FitMode.PAD, box=(1000, 1000), scale_percent=100)
    assert (result.content, result.canvas, result.offset) == ((401, 200), (1000, 1000), (299, 400))


def test_escala_trunca_e_nunca_zera() -> None:
    assert layout(3, 1001, fit=FitMode.SCALE, box=(1600, 1600), scale_percent=10).content == (
        1,
        100,
    )


_image_side = st.integers(min_value=1, max_value=8192)
_box_side = st.integers(min_value=16, max_value=4096)
_percent = st.integers(min_value=1, max_value=100)


@given(_image_side, _image_side, _box_side, _box_side, _percent, st.sampled_from(FitMode))
def test_layout_cabe_na_caixa_dentro_da_imagem_e_nunca_amplia(
    w: int, h: int, bw: int, bh: int, p: int, fit: FitMode
) -> None:
    result = layout(w, h, fit=fit, box=(bw, bh), scale_percent=p)
    x, y, cw, ch = result.crop
    assert min(x, y) >= 0
    assert min(cw, ch) >= 1
    assert x + cw <= w and y + ch <= h
    assert 1 <= result.content[0] <= cw and 1 <= result.content[1] <= ch
    assert result.offset[0] >= 0 and result.offset[1] >= 0
    assert result.content[0] + result.offset[0] <= result.canvas[0]
    assert result.content[1] + result.offset[1] <= result.canvas[1]
    if fit is not FitMode.SCALE:
        assert result.canvas[0] <= bw and result.canvas[1] <= bh
    if fit is FitMode.PAD:
        assert result.canvas == (bw, bh)
