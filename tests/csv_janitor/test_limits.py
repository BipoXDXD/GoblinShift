"""Fronteiras dos limites de tabela (docs/GoblinShiftPlan.md, 1.7), contados em bytes UTF-8.

Os limites reais são usados onde a fatia 2 os exige (64 KiB, 1 MiB, 20.000, 100, 8 MiB).
"""

from goblinshift.processing.csv_janitor import (
    CaseMode,
    CaseRule,
    Delimiter,
    FormulaGuard,
    Limits,
    Params,
    RejectionCode,
)
from tests.csv_janitor.support import cleaned, rejected

KIB = 1024
MIB = 1024 * KIB
# "é" ocupa 2 bytes em UTF-8: um limite contado em caracteres deixaria passar o dobro.
TWO_BYTE_CHAR = "é"


def test_campo_com_64_kib_exatos_em_utf8_multibyte_passa() -> None:
    field = TWO_BYTE_CHAR * (64 * KIB // 2)

    assert cleaned(f"a\r\n{field}\r\n").counts.written == 1


def test_campo_com_um_byte_alem_de_64_kib_rejeita_a_tabela() -> None:
    field = TWO_BYTE_CHAR * (64 * KIB // 2) + "x"

    rejection = rejected(f"a\r\n{field}\r\n")

    assert rejection.code is RejectionCode.FIELD_LIMIT_EXCEEDED
    assert "registro 1, campo 1: 65537 bytes" in rejection.reason


def test_limite_de_campo_vale_na_entrada_mesmo_para_coluna_fora_da_saida() -> None:
    field = TWO_BYTE_CHAR * (64 * KIB // 2) + "x"
    params = Params(output_columns=("id",))

    rejection = rejected(f"id,grande\r\n1,{field}\r\n", params)

    assert rejection.code is RejectionCode.FIELD_LIMIT_EXCEEDED
    assert rejection.reason == "o registro 1, campo 2: 65537 bytes; o limite é 65536"


def test_limite_de_campo_vale_para_o_cabecalho() -> None:
    rejection = rejected("abcde\r\n", limits=Limits(field_bytes=4))

    assert rejection.code is RejectionCode.FIELD_LIMIT_EXCEEDED
    assert "cabeçalho" in rejection.reason


def _record_of(total_bytes: int) -> tuple[str, str]:
    """Cabeçalho de 16 colunas e um registro com `total_bytes`, contando vírgulas e CRLF."""
    columns = 16
    payload = total_bytes - (columns - 1) - len("\r\n")
    fields = ["x" * (64 * KIB)] * (columns - 1)
    fields.append("x" * (payload - sum(map(len, fields))))
    header = ",".join(f"c{i}" for i in range(columns))
    return header, ",".join(fields)


def test_registro_com_1_mib_exato_passa() -> None:
    header, record = _record_of(MIB)

    assert cleaned(f"{header}\r\n{record}\r\n").counts.written == 1


def test_registro_com_um_byte_alem_de_1_mib_rejeita_a_tabela() -> None:
    header, record = _record_of(MIB + 1)

    rejection = rejected(f"{header}\r\n{record}\r\n")

    assert rejection.code is RejectionCode.FIELD_LIMIT_EXCEEDED
    assert "registro 1 passa de 1048576 bytes" in rejection.reason


def test_registro_de_varias_linhas_conta_todas_elas() -> None:
    # '"12\r\n' + '34"\r\n' = 10 bytes: no limite exato passa, com um byte a mais não.
    limits = Limits(record_bytes=10)

    assert cleaned('a\r\n"12\r\n34"\r\n', limits=limits).counts.written == 1
    assert rejected('a\r\n"12\r\n345"\r\n', limits=limits).code is (
        RejectionCode.FIELD_LIMIT_EXCEEDED
    )


def test_20000_registros_passam() -> None:
    assert cleaned("a\r\n" + "x\r\n" * 20_000).counts.written == 20_000


def test_20001_registros_rejeitam_a_tabela() -> None:
    rejection = rejected("a\r\n" + "x\r\n" * 20_001)

    assert rejection.code is RejectionCode.FIELD_LIMIT_EXCEEDED
    assert "passa de 20000 registros" in rejection.reason


def test_linhas_vazias_ignoradas_nao_contam_como_registros() -> None:
    assert cleaned("a,b\r\n" + "x,y\r\n\r\n" * 20_000).counts.written == 20_000


def _header_of(columns: int) -> str:
    return ",".join(f"c{i}" for i in range(columns)) + "\r\n"


def test_100_colunas_passam() -> None:
    assert cleaned(_header_of(100)).counts.records_read == 0


def test_101_colunas_rejeitam_a_tabela() -> None:
    rejection = rejected(_header_of(101))

    assert rejection.code is RejectionCode.FIELD_LIMIT_EXCEEDED
    assert "101 colunas" in rejection.reason


def _table_of(total_bytes: int) -> bytes:
    """20.000 registros de 419 bytes; o cabeçalho completa o tamanho pedido."""
    records = ("x" * 417 + "\r\n") * 20_000
    header = "h" * (total_bytes - len(records) - len("\r\n"))
    return f"{header}\r\n{records}".encode()


def test_tabela_com_8_mib_exatos_passa() -> None:
    table = _table_of(8 * MIB)

    assert len(table) == 8 * MIB
    assert cleaned(table).counts.written == 20_000


def test_tabela_com_um_byte_alem_de_8_mib_rejeita() -> None:
    rejection = rejected(_table_of(8 * MIB + 1))

    assert rejection.code is RejectionCode.FIELD_LIMIT_EXCEEDED
    assert "8388609 bytes" in rejection.reason


def test_caixa_que_aumenta_o_texto_alem_do_limite_rejeita_na_saida() -> None:
    # "İ" (2 bytes) vira "i" + ponto combinante (3 bytes) em minúsculas.
    field = "İ" * 21_846  # 43.692 bytes na entrada, 65.538 na saída
    params = Params(case_rules=(CaseRule("a", CaseMode.LOWER),))

    rejection = rejected(f"a\r\n{field}\r\n", params)

    assert rejection.code is RejectionCode.FIELD_LIMIT_EXCEEDED
    assert "na saída, o registro 1, campo 1: 65538 bytes" in rejection.reason


def test_prefixo_contra_formula_conta_no_limite_da_saida() -> None:
    field = "=" + "x" * (64 * KIB - 1)
    table = f"a\r\n{field}\r\n"

    assert cleaned(table).counts.written == 1
    rejection = rejected(table, Params(formula_guard=FormulaGuard.TAB_PREFIX))
    assert rejection.code is RejectionCode.FIELD_LIMIT_EXCEEDED


def test_limite_de_registro_na_saida_conta_as_aspas_acrescentadas() -> None:
    # Com saída em ponto e vírgula, cada ";" passa a exigir aspas: 2 bytes a mais.
    params = Params(output_delimiter=Delimiter.SEMICOLON)

    assert rejected("a\r\nx;y\r\n", params, Limits(record_bytes=6)).code is (
        RejectionCode.FIELD_LIMIT_EXCEEDED
    )
