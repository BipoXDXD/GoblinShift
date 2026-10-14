"""Fixtures dos scripts de ponta a ponta da fatia 5 e o que cada script deve conferir.

Uso, da raiz do repositório (o `-m` põe a raiz no caminho de import, e é isso que deixa importar
os construtores de `tests/` sem mexer em `sys.path`):

    uv run python -m scripts.e2e_fixtures image-forge <dir>
    uv run python -m scripts.e2e_fixtures downloads-detox <dir>

Grava cada entrada em `<dir>/inputs/<posição>/<nome do upload>` (um diretório por entrada,
porque `Foto.png` e `foto.PNG` se sobrescreveriam num filesystem que ignora a caixa) e o
`<dir>/expected.json`, que `scripts/e2e_check_package.py` usa para conferir o ZIP baixado. Os
valores esperados são escritos à mão a partir de 1.2, 1.4 e 3.6, não recalculados pelas receitas.

Determinístico: nada é baixado, e os mesmos bytes saem a cada execução no mesmo ambiente (o
Pillow do `uv.lock` grava o JPEG, o MPO e o PNG RGBA).
"""

import hashlib
import json
import random
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageChops

from tests.downloads_detox import samples
from tests.image_forge import icc, media

RECIPE_VERSION = 1
SUCCEEDED_WITH_WARNINGS = "succeeded_with_warnings"
EXIF_ORIENTATION_ROTATE_90_CW = 6
# Acima dos 24 MP do Image Forge (1.7) sem passar dos 8192 px por eixo: cai no teto de pixels.
BOMB_SIDE = 8000


@dataclass(frozen=True)
class Fixture:
    """Uma entrada do lote e o desfecho esperado no manifesto (status e campos do status)."""

    name: str
    content: bytes
    outcome: Mapping[str, object]


@dataclass(frozen=True)
class Scenario:
    recipe_id: str
    parameters: Mapping[str, object]
    fixtures: Sequence[Fixture]
    status: str
    # Conteúdo na ordem dos itens e, dentro do item, das saídas; depois manifesto e relatório.
    zip_entries: Sequence[str]
    # Conferências que só o conferente do host faz, sem Pillow: ver e2e_check_package.py.
    extra: Mapping[str, object] = field(default_factory=dict)


def ok(*outputs: str) -> dict[str, object]:
    return {"status": "ok", "outputs": list(outputs)}


def rejected(code: str) -> dict[str, object]:
    return {"status": "rejected", "code": code}


def duplicate_of(position: int) -> dict[str, object]:
    return {"status": "duplicate", "duplicate_of": position}


def image_forge() -> Scenario:
    """Lote de 3.9: P3 com orientação e GPS, MPO, RGBA, PNG de 16 bits e bomba."""
    orange = media.solid("RGB", (64, 32), (255, 128, 0))
    p3 = media.jpeg(
        orange,
        icc_profile=icc.rgb_profile(icc.DISPLAY_P3, "Display P3"),
        exif=media.exif(orientation=EXIF_ORIENTATION_ROTATE_90_CW, with_gps=True),
    )
    phone = media.mpo(
        [media.solid("RGB", (48, 40), (0, 90, 200)), media.solid("RGB", (48, 40), (10, 10, 10))]
    )
    transparent = media.png(media.solid("RGBA", (40, 30), (0, 160, 80, 128)))
    fixtures = (
        Fixture("retrato-p3.jpg", p3, ok("images/retrato-p3.jpg", "thumbnails/retrato-p3.jpg")),
        # MPO com a extensão que o celular grava; preserve: sai JPEG (ambiguidade A3).
        Fixture("celular.jpg", phone, ok("images/celular.jpg", "thumbnails/celular.jpg")),
        Fixture(
            "transparente.png",
            transparent,
            ok("images/transparente.png", "thumbnails/transparente.png"),
        ),
        Fixture(
            "profundo-16bits.png", media.png_16_bit(4, 4, 2), rejected("UNSUPPORTED_BIT_DEPTH")
        ),
        Fixture("bomba.png", media.png_bomb(BOMB_SIDE, BOMB_SIDE), rejected("IMAGE_TOO_LARGE")),
    )
    return Scenario(
        recipe_id="image-forge",
        parameters={"make_thumbnails": True},
        fixtures=fixtures,
        status=SUCCEEDED_WITH_WARNINGS,
        zip_entries=(
            "images/retrato-p3.jpg",
            "thumbnails/retrato-p3.jpg",
            "images/celular.jpg",
            "thumbnails/celular.jpg",
            "images/transparente.png",
            "thumbnails/transparente.png",
            "manifest.json",
            "report.json",
        ),
        extra={
            # Orientação 6 aplicada: 64 x 32 vira 32 x 64; nada amplia (1.2).
            "jpeg_sizes": {
                "images/retrato-p3.jpg": [32, 64],
                "thumbnails/retrato-p3.jpg": [32, 64],
                "images/celular.jpg": [48, 40],
                "thumbnails/celular.jpg": [48, 40],
            },
            # EXIF (inclusive o GPS) sai depois de aplicada a orientação.
            "jpeg_without_exif": ["images/retrato-p3.jpg", "thumbnails/retrato-p3.jpg"],
        },
    )


def downloads_detox() -> Scenario:
    """As oito entradas de 1.4: uma cópia exata, nomes em colisão, JAR como ZIP e HTML em .txt.

    Oito entradas, uma duplicata e uma rejeição: seis arquivos. A rejeição deixa a execução em
    `succeeded_with_warnings`; a duplicata sozinha não (A14).
    """
    fixtures = (
        Fixture("ferias.jpg", samples.jpeg(1), ok("images/ferias.jpg")),
        Fixture("Foto.png", samples.png(1), ok("images/Foto.png")),
        # Colide com Foto.png por casefold(): sufixo com o índice original da entrada (3.6).
        Fixture("foto.PNG", samples.png(2), ok("images/foto_2.PNG")),
        Fixture("relatorio.csv", b"id,nome\r\n1,Ana\r\n", ok("tables/relatorio.csv")),
        Fixture("notas.txt", samples.text("comprar pão\n"), ok("text/notas.txt")),
        Fixture("ferias (1).jpg", samples.jpeg(1), duplicate_of(0)),
        # Limite declarado em 1.4: um JAR chamado .zip passa como ZIP opaco.
        Fixture("ferramenta.zip", samples.jar(), ok("archives/ferramenta.zip")),
        Fixture(
            "pagina.txt",
            samples.text("<!DOCTYPE html>\n<html><body>oi</body></html>\n"),
            rejected("UNSUPPORTED_FORMAT"),
        ),
    )
    return Scenario(
        recipe_id="downloads-detox",
        parameters={},
        fixtures=fixtures,
        status=SUCCEEDED_WITH_WARNINGS,
        zip_entries=(
            "images/ferias.jpg",
            "images/Foto.png",
            "images/foto_2.PNG",
            "tables/relatorio.csv",
            "text/notas.txt",
            "archives/ferramenta.zip",
            "manifest.json",
            "report.json",
        ),
        # O Detox preserva os bytes: o artefato tem o SHA-256 do upload (1.4).
        extra={"same_bytes_as_input": {"images/ferias.jpg": 0, "archives/ferramenta.zip": 6}},
    )


# Pior caso do Image Forge para a medição na VPS (plano da fatia 5, item 9): o teto de entradas e
# de pixels da v1, na conversão mais cara (Display P3, orientação 6 e miniatura).
LOAD_COUNT = 100
LOAD_SIZE = (6000, 4000)  # 24 000 000 px, exatamente o teto (aceito)
LOAD_RECIPE = "image-forge-carga"
# Ruído de um ladrilho fixo sobre um fractal: textura de foto, sem sorteio, com ~4,3 MiB por JPEG,
# e os 100 cabem no lote de 512 MiB.
_NOISE_TILE_SIDE = 256
_NOISE_SEED = 5
_NOISE_WEIGHT = 0.08
_LOAD_JPEG_QUALITY = 85


def image_forge_load(count: int = LOAD_COUNT, size: tuple[int, int] = LOAD_SIZE) -> Scenario:
    """`count` cópias da mesma foto pesada; só o tempo importa, o conteúdo pode repetir."""
    photo = load_photo(size)
    names = [f"carga-{index:03}.jpg" for index in range(1, count + 1)]
    fixtures = tuple(
        Fixture(name, photo, ok(f"images/{name}", f"thumbnails/{name}")) for name in names
    )
    entries = [entry for name in names for entry in (f"images/{name}", f"thumbnails/{name}")]
    return Scenario(
        recipe_id="image-forge",
        parameters={"make_thumbnails": True},
        fixtures=fixtures,
        status="succeeded",
        zip_entries=(*entries, "manifest.json", "report.json"),
    )


def load_photo(size: tuple[int, int]) -> bytes:
    width, height = size
    fractal = Image.effect_mandelbrot(size, (-2.2, -1.2, 1.0, 1.2), 100)
    tile_bytes = random.Random(_NOISE_SEED).randbytes(_NOISE_TILE_SIDE**2)  # noqa: S311 - textura
    tile = Image.frombytes("L", (_NOISE_TILE_SIDE, _NOISE_TILE_SIDE), tile_bytes)
    noise = Image.new("L", size)
    for left in range(0, width, _NOISE_TILE_SIDE):
        for top in range(0, height, _NOISE_TILE_SIDE):
            noise.paste(tile, (left, top))
    gradient = Image.linear_gradient("L").resize(size)
    red = ImageChops.blend(fractal, noise, _NOISE_WEIGHT)
    green = ImageChops.blend(gradient, noise, _NOISE_WEIGHT)
    blue = red.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    return media.encoded(
        Image.merge("RGB", (red, green, blue)),
        "JPEG",
        quality=_LOAD_JPEG_QUALITY,
        icc_profile=icc.rgb_profile(icc.DISPLAY_P3, "Display P3"),
        exif=media.exif(orientation=EXIF_ORIENTATION_ROTATE_90_CW),
    )


_BUILDERS = {"image-forge": image_forge, "downloads-detox": downloads_detox}
RECIPES = tuple(_BUILDERS)


def build(recipe: str) -> Scenario:
    if recipe == LOAD_RECIPE:
        return image_forge_load()
    builder = _BUILDERS.get(recipe)
    if builder is None:
        raise ValueError(f"receita sem fixtures de ponta a ponta: {recipe}; use {list(RECIPES)}")
    return builder()


def write(scenario: Scenario, directory: Path) -> None:
    """Grava as entradas e o expected.json; `directory` pode não existir, mas não pode ter nada."""
    directory.mkdir(parents=True, exist_ok=True)
    if any(directory.iterdir()):
        raise ValueError(f"{directory} não está vazio")
    inputs = []
    for position, fixture in enumerate(scenario.fixtures):
        relative = Path("inputs") / str(position) / fixture.name
        (directory / relative).parent.mkdir(parents=True)
        (directory / relative).write_bytes(fixture.content)
        inputs.append({"path": relative.as_posix(), "manifest": _manifest_input(position, fixture)})
    expected = {
        "recipe_id": scenario.recipe_id,
        "recipe_version": RECIPE_VERSION,
        "parameters": dict(scenario.parameters),
        "status": scenario.status,
        "inputs": inputs,
        "zip_entries": list(scenario.zip_entries),
        **scenario.extra,
    }
    text = json.dumps(expected, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    (directory / "expected.json").write_text(text, encoding="utf-8")


def _manifest_input(position: int, fixture: Fixture) -> dict[str, object]:
    return {
        "position": position,
        "original_name": fixture.name,
        "size": len(fixture.content),
        "sha256": hashlib.sha256(fixture.content).hexdigest(),
        **fixture.outcome,
    }


def main(argv: Sequence[str]) -> int:
    if len(argv) != 2:
        choices = "|".join((*RECIPES, LOAD_RECIPE))
        print(f"uso: python -m scripts.e2e_fixtures {{{choices}}} <dir>", file=sys.stderr)
        return 2
    recipe, directory = argv
    try:
        write(build(recipe), Path(directory))
    except ValueError as error:
        print(f"e2e_fixtures: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
