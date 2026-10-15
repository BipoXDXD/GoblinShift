"""Textos jurídicos (docs/legal) e a versão aceita pelas contas (1.8; ADR 029).

O aceite é um só para os dois textos e grava `CURRENT_TERMS_VERSION`: o cabeçalho de cada texto
precisa declarar essa mesma versão, e o histórico no fim precisa registrá-la com a data de
vigência. Assim, mudar uma cláusula sem subir a constante (ou o contrário) reprova a suíte.
"""

import re
from pathlib import Path

import pytest

from goblinshift.identity import CURRENT_TERMS_VERSION

ROOT = Path(__file__).resolve().parent.parent
LEGAL = ROOT / "docs" / "legal"
TEXTS = [LEGAL / "termos-de-uso.md", LEGAL / "aviso-de-privacidade.md"]
SERVICE_ORIGIN = "https://goblinshift.dev"
HISTORY_HEADING = "## Histórico de versões"
ISO_DATE = r"\d{4}-\d{2}-\d{2}"


def header_field(text: str, field: str) -> str:
    match = re.search(rf"^- \*\*{field}:\*\* (.+)$", text, re.MULTILINE)
    assert match is not None, f"cabeçalho sem {field}"
    return match.group(1).strip()


def history_rows(text: str) -> list[tuple[str, str]]:
    """Linhas (versão, data) da tabela do histórico, na ordem do arquivo."""
    assert HISTORY_HEADING in text, "texto sem histórico de versões"
    section = text.split(HISTORY_HEADING, 1)[1]
    rows = re.findall(rf"^\| `?([^|`]+?)`? \| ({ISO_DATE}) \|", section, re.MULTILINE)
    return [(version.strip(), date) for version, date in rows]


@pytest.mark.parametrize("path", TEXTS, ids=lambda path: path.name)
def test_cabecalho_declara_a_versao_aceita_pelas_contas(path: Path) -> None:
    text = path.read_text(encoding="utf-8")

    assert header_field(text, "Versão") == CURRENT_TERMS_VERSION


@pytest.mark.parametrize("path", TEXTS, ids=lambda path: path.name)
def test_cabecalho_declara_vigencia_e_endereco(path: Path) -> None:
    text = path.read_text(encoding="utf-8")

    assert re.fullmatch(ISO_DATE, header_field(text, "Vigência"))
    assert header_field(text, "Endereço do serviço") == SERVICE_ORIGIN


@pytest.mark.parametrize("path", TEXTS, ids=lambda path: path.name)
def test_historico_termina_na_versao_vigente_com_a_data_de_vigencia(path: Path) -> None:
    text = path.read_text(encoding="utf-8")

    rows = history_rows(text)

    assert len(rows) >= 2
    assert rows[-1] == (CURRENT_TERMS_VERSION, header_field(text, "Vigência"))
    assert len({version for version, _ in rows}) == len(rows)


def test_os_dois_textos_tem_a_mesma_vigencia() -> None:
    terms, privacy = (path.read_text(encoding="utf-8") for path in TEXTS)

    assert header_field(terms, "Vigência") == header_field(privacy, "Vigência")


CONTROLLER = "Luca Sebastian Pimentel Perez, pessoa física, de Curitiba (PR)"
PRIVACY_CHANNEL = "privacidade@goblinshift.dev"
# CPF com ou sem pontuação: o controlador publica nome e cidade, nunca documento.
CPF = r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b"


@pytest.mark.parametrize("path", TEXTS, ids=lambda path: path.name)
def test_identifica_o_controlador_com_nome_cidade_e_canal(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    section = text.split("\n## 2.", 1)[0]

    assert CONTROLLER in section
    assert PRIVACY_CHANNEL in section
    assert "nome completo do controlador" not in text


@pytest.mark.parametrize("path", TEXTS, ids=lambda path: path.name)
def test_nao_publica_cpf(path: Path) -> None:
    text = path.read_text(encoding="utf-8")

    assert re.search(CPF, text) is None
