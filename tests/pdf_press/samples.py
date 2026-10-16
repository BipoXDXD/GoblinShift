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


# --- árvores de páginas hostis, montadas byte a byte ----------------------------------------
# O pikepdf não salva uma árvore com ciclo nem uma de 10 mil níveis; aqui o xref é calculado à mão.

_PAGE = b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 9 9] /Resources << >> >>"


def _page(parent: int) -> bytes:
    return b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 9 9] /Resources << >> >>" % parent


def raw_pdf(objects: dict[int, bytes]) -> bytes:
    """PDF com os objetos dados (o 1 é o catálogo) e um xref clássico com os offsets reais."""
    body = bytearray(b"%PDF-1.4\n")
    offsets: dict[int, int] = {}
    for number in sorted(objects):
        offsets[number] = len(body)
        body += b"%d 0 obj\n%s\nendobj\n" % (number, objects[number])
    size = max(objects) + 1
    xref = len(body)
    body += b"xref\n0 %d\n0000000000 65535 f \n" % size
    for number in range(1, size):
        if number in offsets:
            body += b"%010d 00000 n \n" % offsets[number]
        else:
            body += b"0000000000 65535 f \n"
    body += b"trailer << /Root 1 0 R /Size %d >>\nstartxref\n%d\n%%%%EOF\n" % (size, xref)
    return bytes(body)


def _catalog() -> bytes:
    return b"<< /Type /Catalog /Pages 2 0 R >>"


def page_tree_with_cycle() -> bytes:
    """O nó 2 tem como filho o nó 4, que tem como filho o próprio 2."""
    return raw_pdf(
        {
            1: _catalog(),
            2: b"<< /Type /Pages /Kids [3 0 R 4 0 R] /Count 2 >>",
            3: _PAGE,
            4: b"<< /Type /Pages /Parent 2 0 R /Kids [2 0 R] /Count 1 >>",
        }
    )


def deep_page_tree(depth: int) -> bytes:
    """Uma página no fundo de uma cadeia de `depth` nós /Pages."""
    objects = {1: _catalog()}
    for level in range(depth):
        number = 2 + level
        parent = b"" if level == 0 else b" /Parent %d 0 R" % (number - 1)
        objects[number] = b"<< /Type /Pages /Kids [%d 0 R] /Count 1%s >>" % (number + 1, parent)
    objects[2 + depth] = _page(1 + depth)
    return raw_pdf(objects)


def page_tree_with_count(declared: int, pages: int = 1) -> bytes:
    """`pages` páginas reais com um /Count declarado que pode mentir."""
    kids = b" ".join(b"%d 0 R" % (3 + n) for n in range(pages))
    objects = {1: _catalog(), 2: b"<< /Type /Pages /Kids [%s] /Count %d >>" % (kids, declared)}
    for n in range(pages):
        objects[3 + n] = _PAGE
    return raw_pdf(objects)


def page_tree_with_many_kids(count: int) -> bytes:
    """Um nó /Pages com `count` filhos, todos apontando para a mesma página."""
    kids = b"3 0 R " * count
    return raw_pdf(
        {
            1: _catalog(),
            2: b"<< /Type /Pages /Kids [%s] /Count %d >>" % (kids, count),
            3: _PAGE,
        }
    )
