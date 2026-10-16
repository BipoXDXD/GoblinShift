"""Inspeção de cada PDF antes de compor (docs/GoblinShiftPlan.md, 1.5; ADR 035).

Toda recusa falha a execução inteira (`RecipeFailure`), com o código do catálogo de 1.6. Os PDFs
maliciosos são gerados aqui mesmo (`samples`).
"""

import hashlib
import io
from pathlib import Path

import pikepdf
import pytest
from pikepdf import Dictionary, Name, String

from goblinshift.processing.pdf_press import Limits
from goblinshift.processing.pdf_press.inspection import InspectedDocument, inspect_document
from goblinshift.processing.recipe import FailureCode, InputFile, RecipeFailure
from tests.pdf_press import samples

JAVASCRIPT = Dictionary(S=Name.JavaScript, JS=String("app.alert(1)"))


def entry(tmp_path: Path, data: bytes) -> InputFile:
    path = tmp_path / "0000.bin"
    path.write_bytes(data)
    return InputFile(0, path, "a.pdf", len(data), hashlib.sha256(data).hexdigest())


def inspected(
    tmp_path: Path, data: bytes, *, page_budget: int = 200, limits: Limits | None = None
) -> InspectedDocument:
    return inspect_document(entry(tmp_path, data), page_budget, limits or Limits())


def refusal(
    tmp_path: Path, data: bytes, *, page_budget: int = 200, limits: Limits | None = None
) -> RecipeFailure:
    with pytest.raises(RecipeFailure) as caught:
        inspected(tmp_path, data, page_budget=page_budget, limits=limits).close()
    return caught.value


def test_pdf_comum_abre_com_paginas_e_versao(tmp_path: Path) -> None:
    document = inspected(tmp_path, samples.document(pages=3, version="1.6"))

    with document:
        assert (document.page_count, document.pdf_version) == (3, "1.6")


def test_destino_inicial_sem_acao_e_aceito(tmp_path: Path) -> None:
    # /OpenAction com uma página de destino é navegação, não conteúdo ativo.
    data = samples.document(mutate=samples.open_action_destination)

    with inspected(tmp_path, data) as document:
        assert document.page_count == 1


@pytest.mark.parametrize(
    "action",
    [
        pytest.param(Dictionary(S=Name.GoTo, D=[0, Name.Fit]), id="goto"),
        pytest.param(Dictionary(S=Name.URI, URI=String("https://example.org")), id="uri"),
    ],
)
def test_link_de_navegacao_e_aceito(tmp_path: Path, action: Dictionary) -> None:
    data = samples.document(mutate=samples.link_annotation(action))

    with inspected(tmp_path, data) as document:
        assert document.page_count == 1


def test_documento_no_teto_de_paginas_restantes_e_aceito(tmp_path: Path) -> None:
    with inspected(tmp_path, samples.document(pages=3), page_budget=3) as document:
        assert document.page_count == 3


def test_documento_no_teto_de_objetos_e_aceito(tmp_path: Path) -> None:
    data = samples.document()
    with pikepdf.open(io.BytesIO(data)) as pdf:
        size = int(pdf.trailer.Size)

    with inspected(tmp_path, data, limits=Limits(max_objects_per_document=size)) as document:
        assert document.page_count == 1


@pytest.mark.parametrize(
    ("data", "reason"),
    [
        pytest.param(b"", "%PDF-", id="vazio"),
        pytest.param(b"GIF89a" + b"\x00" * 64, "%PDF-", id="outro-formato"),
        pytest.param(b"junk" + samples.document(), "%PDF-", id="lixo-antes-do-cabecalho"),
        pytest.param(b"%PDF-x.y\n", "%PDF-", id="versao-invalida"),
        pytest.param(samples.truncated(samples.document()), "danificado", id="truncado"),
        pytest.param(samples.broken_xref(samples.document()), "danificado", id="xref-quebrado"),
        pytest.param(
            samples.wrong_stream_length(samples.document(compress=False)),
            "aviso estrutural",
            id="length-errado",
        ),
        pytest.param(samples.document(pages=0), "sem páginas", id="sem-paginas"),
    ],
)
def test_arquivo_danificado_falha_com_pdf_invalid(tmp_path: Path, data: bytes, reason: str) -> None:
    failure = refusal(tmp_path, data)

    assert failure.code is FailureCode.PDF_INVALID
    assert reason in failure.detail


@pytest.mark.parametrize(
    ("data", "reason"),
    [
        pytest.param(
            samples.document(encryption=pikepdf.Encryption(owner="o", user="u")),
            "senha",
            id="senha-de-usuario",
        ),
        pytest.param(
            samples.document(encryption=pikepdf.Encryption(owner="o", user="")),
            "criptografado",
            id="so-senha-de-dono",
        ),
        pytest.param(
            samples.document(mutate=samples.javascript_name_tree), "JavaScript", id="js-nomeado"
        ),
        pytest.param(
            samples.document(mutate=samples.javascript_name_tree, object_streams=True),
            "JavaScript",
            id="js-dentro-de-object-stream",
        ),
        pytest.param(
            samples.with_orphan_object(
                samples.document(compress=False), b"<< /S /JavaScript /JS (app.alert(1)) >>"
            ),
            "JavaScript",
            id="js-orfao",
        ),
        pytest.param(
            samples.document(mutate=samples.open_action(JAVASCRIPT)),
            "JavaScript",
            id="openaction-js",
        ),
        pytest.param(
            samples.document(
                mutate=samples.open_action(Dictionary(S=Name.Launch, F=String("calc.exe")))
            ),
            "Launch",
            id="openaction-launch",
        ),
        pytest.param(
            samples.document(mutate=samples.link_annotation(JAVASCRIPT)),
            "JavaScript",
            id="link-js",
        ),
        pytest.param(
            samples.document(
                mutate=samples.link_annotation(
                    Dictionary(S=Name.SubmitForm, F=String("https://example.org"))
                )
            ),
            "SubmitForm",
            id="link-submitform",
        ),
        pytest.param(
            samples.document(
                mutate=samples.link_annotation(
                    Dictionary(S=Name.GoToR, F=String("outro.pdf"), D=[0, Name.Fit])
                )
            ),
            "GoToR",
            id="link-gotor",
        ),
        pytest.param(
            samples.document(
                mutate=samples.link_annotation(
                    Dictionary(S=Name.GoTo, D=[0, Name.Fit], Next=Dictionary(S=Name.Launch))
                )
            ),
            "Launch",
            id="acao-encadeada-em-next",
        ),
        pytest.param(
            samples.document(mutate=samples.page_additional_action), "/AA", id="acao-automatica"
        ),
        pytest.param(samples.document(mutate=samples.embedded_file), "anexo", id="anexo"),
        pytest.param(
            samples.document(mutate=samples.file_attachment_annotation),
            "anexo",
            id="anotacao-de-anexo",
        ),
        pytest.param(samples.document(mutate=samples.acroform), "formulário", id="acroform"),
        pytest.param(samples.document(mutate=samples.xfa), "formulário", id="xfa"),
        pytest.param(
            samples.document(mutate=samples.widget_without_acroform),
            "formulário",
            id="widget-sem-acroform",
        ),
        pytest.param(
            samples.document(mutate=samples.signature_field), "assinatura", id="assinatura"
        ),
        pytest.param(
            samples.document(mutate=samples.certification_permissions),
            "assinatura",
            id="certificacao",
        ),
        pytest.param(
            samples.document(mutate=samples.annotation("/RichMedia")),
            "multimídia",
            id="richmedia",
        ),
        pytest.param(
            samples.document(mutate=samples.annotation("/Screen")), "multimídia", id="screen"
        ),
        pytest.param(samples.document(mutate=samples.portfolio), "portfólio", id="portfolio"),
    ],
)
def test_recurso_nao_suportado_falha_com_pdf_unsupported_feature(
    tmp_path: Path, data: bytes, reason: str
) -> None:
    failure = refusal(tmp_path, data)

    assert failure.code is FailureCode.PDF_UNSUPPORTED_FEATURE
    assert reason in failure.detail


def test_paginas_acima_do_que_resta_do_teto_falham(tmp_path: Path) -> None:
    failure = refusal(tmp_path, samples.document(pages=3), page_budget=2)

    assert failure.code is FailureCode.INPUT_LIMIT_EXCEEDED
    assert "páginas" in failure.detail


def test_bomba_de_objetos_falha_antes_de_percorrer_o_documento(tmp_path: Path) -> None:
    data = samples.document(mutate=samples.extra_objects(50))

    failure = refusal(tmp_path, data, limits=Limits(max_objects_per_document=20))

    assert failure.code is FailureCode.INPUT_LIMIT_EXCEEDED
    assert "objetos" in failure.detail


def test_detalhe_da_falha_nao_traz_caminho_nem_objeto_interno(tmp_path: Path) -> None:
    failure = refusal(tmp_path, samples.broken_xref(samples.document()))

    assert str(tmp_path) not in failure.detail
    assert "BytesIO" not in failure.detail
    assert "entrada 1" in failure.detail
