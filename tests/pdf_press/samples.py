"""PDFs sintéticos gerados no próprio teste: nenhum PDF de terceiros entra no repositório.

Cada página leva um marcador no content stream (`% gs:<tag>:<página>`), que `markers` lê de volta
para conferir ordem e repetição das páginas de saída. As variantes maliciosas acrescentam ao
documento o objeto que a inspeção precisa recusar, ou estragam os bytes depois de salvos.
"""

import io
import re
import warnings
from collections.abc import Callable

import pikepdf
from pikepdf import Array, Dictionary, Name, String
from pikepdf.exceptions import PageCopyWarning

type Mutation = Callable[[pikepdf.Pdf], None]

_MARKER = re.compile(rb"% gs:([^\n]+)\n")
PAGE_SIZE = (200, 200)


def document(
    tag: str = "a",
    pages: int = 1,
    *,
    version: str = "1.4",
    mutate: Mutation | None = None,
    encryption: pikepdf.Encryption | None = None,
    compress: bool = True,
    object_streams: bool = False,
) -> bytes:
    pdf = pikepdf.new()
    for number in range(1, pages + 1):
        page = pdf.add_blank_page(page_size=PAGE_SIZE)
        page.obj.Contents = pdf.make_stream(f"% gs:{tag}:{number}\n0 0 m 9 9 l S\n".encode())
    if mutate is not None:
        mutate(pdf)
    buffer = io.BytesIO()
    with warnings.catch_warnings():
        # O pikepdf avisa de widget fora do /AcroForm: é justamente o caso malicioso de teste.
        warnings.simplefilter("ignore", PageCopyWarning)
        _save(pdf, buffer, version, encryption, _stream_mode(compress, object_streams))
    return buffer.getvalue()


def _save(
    pdf: pikepdf.Pdf,
    buffer: io.BytesIO,
    version: str,
    encryption: pikepdf.Encryption | None,
    mode: pikepdf.ObjectStreamMode,
) -> None:
    pdf.save(
        buffer,
        min_version=version,
        encryption=encryption or False,
        compress_streams=mode is not pikepdf.ObjectStreamMode.disable,
        object_stream_mode=mode,
        deterministic_id=encryption is None,
    )


def _stream_mode(compress: bool, object_streams: bool) -> pikepdf.ObjectStreamMode:
    mode: pikepdf.ObjectStreamMode = pikepdf.ObjectStreamMode.preserve
    if not compress:
        mode = pikepdf.ObjectStreamMode.disable
    elif object_streams:
        mode = pikepdf.ObjectStreamMode.generate
    return mode


def markers(data: bytes) -> list[str]:
    """Marcadores das páginas, na ordem do documento: ["a:1", "b:2", …]."""
    with pikepdf.open(io.BytesIO(data)) as pdf:
        return [_marker(page) for page in pdf.pages]


def _marker(page: pikepdf.Page) -> str:
    found = _MARKER.search(page.obj.Contents.read_bytes())
    assert found is not None
    return found.group(1).decode()


# --- conteúdo ativo, formulários, assinaturas, anexos --------------------------------------


def javascript_name_tree(pdf: pikepdf.Pdf) -> None:
    script = pdf.make_indirect(Dictionary(S=Name.JavaScript, JS=String("app.alert(1)")))
    pdf.Root.Names = Dictionary(JavaScript=Dictionary(Names=Array([String("x"), script])))


def open_action(action: Dictionary) -> Mutation:
    def mutate(pdf: pikepdf.Pdf) -> None:
        pdf.Root.OpenAction = action

    return mutate


def open_action_destination(pdf: pikepdf.Pdf) -> None:
    pdf.Root.OpenAction = Array([pdf.pages[0].obj, Name.Fit])


def link_annotation(action: Dictionary) -> Mutation:
    def mutate(pdf: pikepdf.Pdf) -> None:
        annotation = Dictionary(Type=Name.Annot, Subtype=Name.Link, Rect=[0, 0, 9, 9], A=action)
        pdf.pages[0].obj.Annots = Array([pdf.make_indirect(annotation)])

    return mutate


def annotation(subtype: str) -> Mutation:
    def mutate(pdf: pikepdf.Pdf) -> None:
        annot = Dictionary(Type=Name.Annot, Subtype=Name(subtype), Rect=[0, 0, 9, 9])
        pdf.pages[0].obj.Annots = Array([pdf.make_indirect(annot)])

    return mutate


def page_additional_action(pdf: pikepdf.Pdf) -> None:
    pdf.pages[0].obj.AA = Dictionary(O=Dictionary(S=Name.JavaScript, JS=String("x")))


def embedded_file(pdf: pikepdf.Pdf) -> None:
    pdf.attachments["leia.txt"] = pikepdf.AttachedFileSpec(
        pdf,
        b"segredo",
        description="",
        filename="leia.txt",
        mime_type="text/plain",
        creation_date="",
        mod_date="",
    )


def file_attachment_annotation(pdf: pikepdf.Pdf) -> None:
    stream = pdf.make_stream(b"x", Type=Name.EmbeddedFile)
    spec = Dictionary(Type=Name.Filespec, F=String("x.exe"), EF=Dictionary(F=stream))
    annot = Dictionary(Type=Name.Annot, Subtype=Name.FileAttachment, Rect=[0, 0, 9, 9], FS=spec)
    pdf.pages[0].obj.Annots = Array([pdf.make_indirect(annot)])


def acroform(pdf: pikepdf.Pdf) -> None:
    pdf.Root.AcroForm = Dictionary(Fields=Array([]))


def xfa(pdf: pikepdf.Pdf) -> None:
    pdf.Root.AcroForm = Dictionary(Fields=Array([]), XFA=pdf.make_stream(b"<xdp/>"))


def widget_without_acroform(pdf: pikepdf.Pdf) -> None:
    widget = Dictionary(
        Type=Name.Annot, Subtype=Name.Widget, FT=Name.Tx, T=String("nome"), Rect=[0, 0, 9, 9]
    )
    pdf.pages[0].obj.Annots = Array([pdf.make_indirect(widget)])


def signature_field(pdf: pikepdf.Pdf) -> None:
    signature = Dictionary(
        Type=Name.Sig, Filter=Name("/Adobe.PPKLite"), ByteRange=Array([0, 1, 2, 3])
    )
    field = pdf.make_indirect(
        Dictionary(FT=Name.Sig, T=String("assinatura"), V=pdf.make_indirect(signature))
    )
    pdf.Root.AcroForm = Dictionary(Fields=Array([field]), SigFlags=3)


def certification_permissions(pdf: pikepdf.Pdf) -> None:
    pdf.Root.Perms = Dictionary(DocMDP=pdf.make_indirect(Dictionary(Type=Name.Sig)))


def portfolio(pdf: pikepdf.Pdf) -> None:
    pdf.Root.Collection = Dictionary(Type=Name.Collection)


def extra_objects(count: int) -> Mutation:
    def mutate(pdf: pikepdf.Pdf) -> None:
        pdf.Root.Extra = Array([pdf.make_indirect(Dictionary(N=n)) for n in range(count)])

    return mutate


# --- bytes estragados -------------------------------------------------------------------------


def truncated(data: bytes) -> bytes:
    return data[: len(data) // 2]


def broken_xref(data: bytes) -> bytes:
    """`startxref` apontando para o meio do arquivo, onde não há tabela xref."""
    head, _, _ = data.rpartition(b"startxref")
    return head + b"startxref\n9\n%%EOF\n"


def wrong_stream_length(data: bytes) -> bytes:
    """Um /Length maior que o stream, com o mesmo número de dígitos (os offsets do xref seguem
    certos): o qpdf recupera, mas registra aviso estrutural. Pede um documento sem compressão."""
    found = re.search(rb"/Length (\d+)", data)
    assert found is not None
    longer = b"9" * len(found.group(1))
    assert longer != found.group(1)
    return data[: found.start(1)] + longer + data[found.end(1) :]


def with_orphan_object(data: bytes, body: bytes) -> bytes:
    """Atualização incremental com um objeto que nada referencia (o qpdf, ao salvar, descartaria
    objetos órfãos; por isso ele entra nos bytes). Pede um documento sem object streams."""
    found = re.search(rb"/Size (\d+)", data)
    previous = re.findall(rb"startxref\s+(\d+)", data)
    assert found is not None and previous
    number = int(found.group(1))
    obj = b"%d 0 obj\n%s\nendobj\n" % (number, body)
    xref = len(data) + len(obj)
    trailer = b"trailer << /Root 1 0 R /Size %d /Prev %s >>\n" % (number + 1, previous[-1])
    table = b"xref\n%d 1\n%010d 00000 n \n" % (number, len(data))
    return data + obj + table + trailer + b"startxref\n%d\n%%%%EOF\n" % xref
