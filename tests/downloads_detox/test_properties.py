"""Propriedades da receita downloads-detox sobre lotes gerados (fatia 5, 5.3).

Os conteúdos vêm de um conjunto pequeno, para forçar duplicatas e rejeições; os nomes são
Unicode arbitrário, com variantes de caixa e de normalização.
"""

import tempfile
import unicodedata
from pathlib import Path
from typing import Literal

from hypothesis import given, settings
from hypothesis import strategies as st

from goblinshift.processing.downloads_detox import Params
from goblinshift.processing.output_dir import MAX_COMPONENT_BYTES
from goblinshift.processing.recipe import ItemDuplicate, ItemOk, Report
from tests.downloads_detox import samples
from tests.downloads_detox.conftest import Workspace

CONTENTS = (
    samples.png(1),
    samples.png(2),
    samples.jpeg(),
    samples.text("a"),
    samples.text("b"),
    samples.text(""),
    samples.EMPTY_ZIP,
    samples.jar(),
    samples.PE_EXECUTABLE,
)
EXTENSIONS = (".png", ".PNG", ".jpg", ".JPEG", ".txt", ".csv", ".Csv", ".zip", ".exe", "")


# Sem code points não atribuídos (Cn): o APFS do macOS recusa criá-los (EILSEQ), embora o ext4
# do executor aceite. Surrogates (Cs) já ficam de fora por padrão no Hypothesis.
NAME_CHARACTERS = st.characters(exclude_categories=["Cs", "Cn"])


@st.composite
def names(draw: st.DrawFn) -> str:
    stem = draw(
        st.text(NAME_CHARACTERS, max_size=120)
        | st.sampled_from(["Foto", "foto", "FOTO", "Relatório"])
    )
    form: Literal["NFC", "NFD"] = draw(st.sampled_from(["NFC", "NFD"]))
    return unicodedata.normalize(form, stem) + draw(st.sampled_from(EXTENSIONS))


batches = st.lists(st.tuples(names(), st.sampled_from(CONTENTS)), min_size=1, max_size=12)


def run(batch: list[tuple[str, bytes]], params: Params) -> tuple[Workspace, Report]:
    work = Workspace(Path(tempfile.mkdtemp()))
    for name, data in batch:
        work.add(name, data)
    return work, work.run(params)


@settings(max_examples=150, deadline=None)
@given(batches, st.booleans())
def test_lote_gerado_respeita_as_invariantes(batch: list[tuple[str, bytes]], dedup: bool) -> None:
    work, report = run(batch, Params(deduplicate=dedup))

    # (1) Um item por entrada, na ordem.
    assert [item.position for item in report.items] == list(range(len(batch)))
    accepted = [item for item in report.items if isinstance(item, ItemOk)]
    outputs = [path for item in accepted for path in item.outputs]
    # (2) Nomes finais únicos por casefold e cada componente dentro do teto.
    assert len({path.casefold() for path in outputs}) == len(outputs)
    assert all(len(part.encode()) <= MAX_COMPONENT_BYTES for p in outputs for part in p.split("/"))
    assert sorted(outputs) == work.output_paths()
    # (3) Com deduplicação, uma saída por conteúdo distinto entre os aceitos.
    if dedup:
        assert len(outputs) == len({batch[item.position][1] for item in accepted})
    else:
        assert not any(isinstance(item, ItemDuplicate) for item in report.items)
    # (4) Toda duplicata aponta para um item aceito anterior com o mesmo conteúdo.
    for item in report.items:
        if isinstance(item, ItemDuplicate):
            original = report.items[item.duplicate_of]
            assert isinstance(original, ItemOk)
            assert batch[item.duplicate_of][1] == batch[item.position][1]
    # (5) Os bytes de cada saída são os da entrada.
    for item in accepted:
        assert work.read(item.outputs[0]) == batch[item.position][1]
