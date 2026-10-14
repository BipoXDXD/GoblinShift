"""Perfis de cor por lista fechada (1.2): reconhecidos pelo conteúdo, nunca pela descrição."""

import pytest
from PIL import ImageCms

from tests.image_forge import icc, media
from tests.image_forge.conftest import Workspace, only_ok
from tests.image_forge.test_inspection import code_for

P3_ORANGE = (200, 100, 50)
# Calculado à mão: linearização do sRGB, matriz RGB→XYZ do Display P3 e XYZ→RGB do sRGB (os dois
# com branco D65, sem adaptação), recodificação sRGB: (214,90; 92,61; 30,95).
SRGB_FROM_P3_ORANGE = (215, 93, 31)
TOLERANCE = 2


def littlecms_srgb() -> bytes:
    return ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()


def orange_png(**options: object) -> bytes:
    return media.png(media.solid("RGB", (4, 4), P3_ORANGE), **options)


def pixel(work: Workspace, name: str) -> tuple[int, ...]:
    value = work.open_output(name).getpixel((1, 1))
    assert isinstance(value, tuple)
    return value


def test_sem_perfil_assume_srgb_e_registra(work: Workspace) -> None:
    work.add("a.png", orange_png())
    assert only_ok(work.run()).details["color_profile"] == "assumed-srgb"
    assert pixel(work, "images/a.png") == P3_ORANGE


@pytest.mark.parametrize(
    "profile",
    [littlecms_srgb(), icc.rgb_profile(icc.SRGB, "sRGB IEC61966-2.1")],
    ids=["littlecms", "montado-a-mao"],
)
def test_srgb_e_aceito_como_esta(work: Workspace, profile: bytes) -> None:
    work.add("a.png", orange_png(icc_profile=profile))
    assert only_ok(work.run()).details["color_profile"] == "srgb"
    assert pixel(work, "images/a.png") == P3_ORANGE


def test_bloco_srgb_do_png_sem_icc_e_srgb_declarado(work: Workspace) -> None:
    srgb_chunk = media.chunk(b"sRGB", b"\x00")
    work.add("a.png", media.insert_after_ihdr(orange_png(), srgb_chunk))
    assert only_ok(work.run()).details["color_profile"] == "srgb"


def test_exif_srgb_sem_icc_e_srgb_declarado(work: Workspace) -> None:
    work.add("a.png", orange_png(exif=media.exif(color_space=1)))
    assert only_ok(work.run()).details["color_profile"] == "srgb"


def test_display_p3_e_convertido_para_srgb(work: Workspace) -> None:
    work.add("a.png", orange_png(icc_profile=icc.rgb_profile(icc.DISPLAY_P3, "Display P3")))

    item = only_ok(work.run())

    assert item.details["color_profile"] == "display-p3-converted"
    converted = pixel(work, "images/a.png")
    assert all(
        abs(got - want) <= TOLERANCE
        for got, want in zip(converted, SRGB_FROM_P3_ORANGE, strict=True)
    )


def test_display_p3_com_descricao_srgb_continua_reconhecido_como_p3(work: Workspace) -> None:
    disguised = icc.rgb_profile(icc.DISPLAY_P3, "sRGB IEC61966-2.1")
    work.add("a.png", orange_png(icc_profile=disguised))
    assert only_ok(work.run()).details["color_profile"] == "display-p3-converted"


def test_display_p3_em_rgba_preserva_o_alfa(work: Workspace) -> None:
    image = media.solid("RGBA", (4, 4), (*P3_ORANGE, 77))
    work.add("a.png", media.png(image, icc_profile=icc.rgb_profile(icc.DISPLAY_P3, "P3")))

    only_ok(work.run())

    *rgb, alpha = pixel(work, "images/a.png")
    assert alpha == 77
    assert all(
        abs(got - want) <= TOLERANCE for got, want in zip(rgb, SRGB_FROM_P3_ORANGE, strict=True)
    )


def test_display_p3_em_jpeg(work: Workspace) -> None:
    image = media.solid("RGB", (16, 16), P3_ORANGE)
    work.add("p3.jpg", media.jpeg(image, icc_profile=icc.rgb_profile(icc.DISPLAY_P3, "P3")))
    assert only_ok(work.run()).details["color_profile"] == "display-p3-converted"


@pytest.mark.parametrize(
    "profile",
    [
        pytest.param(icc.rgb_profile(icc.ADOBE_RGB, "Adobe RGB (1998)"), id="adobe-rgb"),
        pytest.param(icc.rgb_profile(icc.ADOBE_RGB, "Display P3"), id="adobe-rgb-disfarcado"),
        pytest.param(b"lixo" * 40, id="icc-lixo"),
        pytest.param(
            ImageCms.ImageCmsProfile(ImageCms.createProfile("LAB")).tobytes(), id="perfil-lab"
        ),
    ],
)
def test_perfil_fora_da_lista_e_recusado(work: Workspace, profile: bytes) -> None:
    assert code_for(work, orange_png(icc_profile=profile)) == "UNSUPPORTED_COLOR_PROFILE"


def test_exif_srgb_com_perfil_p3_e_conflito(work: Workspace) -> None:
    data = orange_png(
        icc_profile=icc.rgb_profile(icc.DISPLAY_P3, "P3"), exif=media.exif(color_space=1)
    )
    assert code_for(work, data) == "UNSUPPORTED_COLOR_PROFILE"


def test_bloco_srgb_com_perfil_p3_e_conflito(work: Workspace) -> None:
    p3 = orange_png(icc_profile=icc.rgb_profile(icc.DISPLAY_P3, "P3"))
    data = media.insert_after_ihdr(p3, media.chunk(b"sRGB", b"\x00"))
    assert code_for(work, data) == "UNSUPPORTED_COLOR_PROFILE"


def test_exif_nao_calibrado_com_p3_e_o_caso_do_iphone(work: Workspace) -> None:
    data = orange_png(
        icc_profile=icc.rgb_profile(icc.DISPLAY_P3, "P3"), exif=media.exif(color_space=0xFFFF)
    )
    work.add("a.png", data)
    assert only_ok(work.run()).details["color_profile"] == "display-p3-converted"


def test_exif_com_espaco_de_cor_fora_da_lista_e_recusado(work: Workspace) -> None:
    assert code_for(work, orange_png(exif=media.exif(color_space=2))) == "UNSUPPORTED_COLOR_PROFILE"


def test_perfil_rgb_em_imagem_cinza_e_recusado(work: Workspace) -> None:
    gray = media.png(media.solid("L", (4, 4), 128), icc_profile=littlecms_srgb())
    assert code_for(work, gray) == "UNSUPPORTED_COLOR_PROFILE"
