"""Especificação da política de prontidão diante do schema (expand/contract)."""

from goblinshift.platform.schema import schema_is_ready

KNOWN = frozenset({"0001", "0002", "0003"})
HEADS = frozenset({"0003"})


def test_banco_sem_migracao_nao_esta_pronto() -> None:
    assert schema_is_ready(frozenset(), HEADS, KNOWN) is False


def test_banco_nos_heads_do_codigo_esta_pronto() -> None:
    assert schema_is_ready(frozenset({"0003"}), HEADS, KNOWN) is True


def test_migracao_pendente_nao_esta_pronto() -> None:
    assert schema_is_ready(frozenset({"0002"}), HEADS, KNOWN) is False


def test_banco_a_frente_durante_o_deploy_esta_pronto() -> None:
    # Uma migração "expand" mais nova já foi aplicada; este processo é da versão anterior.
    assert schema_is_ready(frozenset({"0004"}), HEADS, KNOWN) is True
