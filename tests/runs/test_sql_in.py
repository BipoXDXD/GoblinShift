"""Escape dos literais que `_sql_in` põe na CHECK do catálogo.

Os códigos de hoje não têm aspas, e a migração 0022 congela o texto `IN ('A', 'B')`. O PostgreSQL
normaliza esse texto e o do modelo no mesmo `pg_get_constraintdef`; mudar o formato (dólar, `E''`,
cast) faria o teste de drift ver CHECKs diferentes. O escape só dobra a aspa simples, que é o
literal SQL padrão.
"""

from enum import StrEnum

from goblinshift.processing.recipe import ItemRejectionCode
from goblinshift.runs.lifecycle import ErrorCode
from goblinshift.runs.tables import _sql_in


class _Amostra(StrEnum):
    OK = "OK"
    APOSTROFO = "O'BRIEN"
    SO_ASPAS = "'"
    VARIAS = "A'B'C"
    BARRA = "A\\B"


def test_codigo_sem_aspas_sai_entre_aspas_simples() -> None:
    assert _sql_in("error_code", [_Amostra.OK]) == "error_code IN ('OK')"


def test_aspas_simples_no_valor_sao_dobradas() -> None:
    assert _sql_in("error_code", [_Amostra.APOSTROFO]) == "error_code IN ('O''BRIEN')"


def test_valor_que_e_uma_aspa_dobra_as_aspas() -> None:
    assert _sql_in("error_code", [_Amostra.SO_ASPAS]) == "error_code IN ('''')"


def test_varias_aspas_saem_dobradas_na_ordem_do_enum() -> None:
    assert _sql_in("col", list(_Amostra)) == "col IN ('OK', 'O''BRIEN', '''', 'A''B''C', 'A\\B')"


def test_barra_invertida_nao_e_escapada() -> None:
    # Literal padrão, não `E''`: a barra é caractere comum e o PostgreSQL não a reescreve.
    assert _sql_in("error_code", [_Amostra.BARRA]) == "error_code IN ('A\\B')"


def test_catalogo_real_permanece_o_literal_congelado_na_migracao() -> None:
    for code in (*ErrorCode, *ItemRejectionCode):
        assert "'" not in code
        assert "\\" not in code
        assert _sql_in("error_code", [code]) == f"error_code IN ('{code}')"
