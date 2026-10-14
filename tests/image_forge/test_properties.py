"""Propriedades de ponta a ponta sobre imagens pequenas geradas pelo Hypothesis (1.2)."""

import tempfile
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st
from PIL import Image

from goblinshift.processing.image_forge import OutputFormat, Params
from goblinshift.processing.image_forge.geometry import fit_size
from goblinshift.processing.recipe import ItemOk
from tests.image_forge import media
from tests.image_forge.conftest import Workspace

ALPHA_MODES = {"RGBA", "LA"}


@st.composite
def images(draw: st.DrawFn) -> tuple[Image.Image, str]:
    image_format = draw(st.sampled_from(["PNG", "JPEG"]))
    modes = ["RGB", "RGBA", "L", "LA", "P"] if image_format == "PNG" else ["RGB", "L"]
    mode = draw(st.sampled_from(modes))
    width = draw(st.integers(1, 64))
    height = draw(st.integers(1, 64))
    bands = len(Image.new(mode, (1, 1)).getbands())
    size = width * height * bands
    image = Image.frombytes(mode, (width, height), draw(st.binary(min_size=size, max_size=size)))
    return image, image_format


@settings(max_examples=100, deadline=None)
@given(
    generated=images(),
    max_width=st.integers(16, 64),
    max_height=st.integers(16, 64),
    output_format=st.sampled_from(list(OutputFormat)),
)
def test_saida_tem_o_tamanho_do_contain_e_nenhum_metadado(
    generated: tuple[Image.Image, str],
    max_width: int,
    max_height: int,
    output_format: OutputFormat,
) -> None:
    image, image_format = generated
    with tempfile.TemporaryDirectory() as root:
        work = Workspace(Path(root))
        work.add("gerada", media.encoded(image, image_format, exif=media.exif(with_gps=True)))

        report = work.run(
            Params(max_width=max_width, max_height=max_height, output_format=output_format)
        )

        (item,) = report.items
        assert isinstance(item, ItemOk)
        (name,) = item.outputs
        output = work.open_output(name)
        assert output.size == fit_size(image.width, image.height, max_width, max_height)
        assert not output.getexif()
        assert "icc_profile" not in output.info
        if output.format == "PNG" and image.mode in ALPHA_MODES:
            assert output.mode == image.mode
            if output.size == image.size:
                assert output.getchannel("A").tobytes() == image.getchannel("A").tobytes()
