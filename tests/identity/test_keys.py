"""Geração, leitura e verificação de chaves de API, sem banco (docs/GoblinShiftPlan.md, 3.6).

Os segredos de exemplo são montados com caracteres repetidos, de baixa entropia, para que o
gitleaks não os confunda com credenciais reais.
"""

from datetime import UTC, datetime, timedelta

import pytest

from goblinshift.identity import (
    KEY_PREFIX,
    InvalidKeyLifetimeError,
    InvalidScopesError,
    Scope,
    generate_key,
    key_is_active,
    key_lifetime,
    parse_key,
    parse_scopes,
    secret_matches,
)

PUBLIC_ID = "0" * 16
SECRET = "A" * 43
WELL_FORMED = f"gsk_{PUBLIC_ID}_{SECRET}"
# sha256(b"A" * 43), calculado à parte.
SECRET_SHA256 = bytes.fromhex("0f007385b6f9d4b7eeb2748605afe1a984a0a3bfa3f014d09e2a784ce9e5cd1a")
NOW = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)


def test_chave_gerada_tem_prefixo_e_formato_documentado() -> None:
    key = generate_key()

    assert key.token.startswith(KEY_PREFIX)
    assert key.token == f"gsk_{key.public_id}_{key.token[-43:]}"
    assert len(key.token) == 64
    assert len(key.secret_hash) == 32


def test_chave_gerada_e_lida_de_volta_e_confere_com_o_proprio_hash() -> None:
    key = generate_key()

    presented = parse_key(key.token)

    assert presented is not None
    assert presented.public_id == key.public_id
    assert secret_matches(presented, key.secret_hash)


def test_duas_chaves_geradas_nao_compartilham_identificador_nem_segredo() -> None:
    first, second = generate_key(), generate_key()

    assert first.public_id != second.public_id
    assert first.token != second.token
    assert first.secret_hash != second.secret_hash


def test_repr_da_chave_gerada_nao_mostra_o_segredo() -> None:
    key = generate_key()

    assert key.token not in repr(key)
    assert key.token[-43:] not in repr(parse_key(key.token))


def test_hash_armazenado_e_o_sha256_so_do_segredo() -> None:
    presented = parse_key(WELL_FORMED)

    assert presented is not None
    assert presented.public_id == PUBLIC_ID
    assert secret_matches(presented, SECRET_SHA256)


def test_segredo_com_um_caractere_diferente_nao_confere() -> None:
    presented = parse_key(f"gsk_{PUBLIC_ID}_{'A' * 42}B")

    assert presented is not None
    assert not secret_matches(presented, SECRET_SHA256)


@pytest.mark.parametrize(
    "token",
    [
        pytest.param("", id="vazia"),
        pytest.param("gsk_", id="so-prefixo"),
        pytest.param(f"gsx_{PUBLIC_ID}_{SECRET}", id="prefixo-errado"),
        pytest.param(f"GSK_{PUBLIC_ID}_{SECRET}", id="prefixo-maiusculo"),
        pytest.param(f"{PUBLIC_ID}_{SECRET}", id="sem-prefixo"),
        pytest.param(f"gsk_{PUBLIC_ID}{SECRET}", id="sem-separador"),
        pytest.param(f"gsk_{'0' * 15}_{SECRET}", id="identificador-curto"),
        pytest.param(f"gsk_{'0' * 17}_{SECRET}", id="identificador-longo"),
        pytest.param(f"gsk_{'A' * 16}_{SECRET}", id="identificador-maiusculo"),
        pytest.param(f"gsk_{'g' * 16}_{SECRET}", id="identificador-fora-do-hex"),
        pytest.param(f"gsk_{PUBLIC_ID}_{'A' * 42}", id="segredo-curto"),
        pytest.param(f"gsk_{PUBLIC_ID}_{'A' * 44}", id="segredo-longo"),
        pytest.param(f"gsk_{PUBLIC_ID}_{'A' * 42}=", id="segredo-com-padding"),
        pytest.param(f"gsk_{PUBLIC_ID}_{'A' * 42}+", id="segredo-base64-padrao"),
        pytest.param(f"gsk_{PUBLIC_ID}_{'A' * 42}é", id="segredo-nao-ascii"),
        pytest.param(f"gsk_{'\u0660' * 16}_{SECRET}", id="digitos-unicode"),
        pytest.param(f"{WELL_FORMED}\n", id="quebra-de-linha-no-fim"),
        pytest.param(f" {WELL_FORMED}", id="espaco-no-inicio"),
        pytest.param(f"gsk_{PUBLIC_ID}_{'A' * 42}\x00", id="nul"),
        pytest.param(WELL_FORMED * 1000, id="enorme"),
    ],
)
def test_chave_malformada_nao_e_lida(token: str) -> None:
    assert parse_key(token) is None


@pytest.mark.parametrize("days", [1, 30, 90])
def test_validade_dentro_da_faixa_vira_intervalo_em_dias(days: int) -> None:
    assert key_lifetime(days) == timedelta(days=days)


@pytest.mark.parametrize("days", [-1, 0, 91])
def test_validade_fora_da_faixa_e_recusada(days: int) -> None:
    with pytest.raises(InvalidKeyLifetimeError):
        key_lifetime(days)


def test_chave_que_expira_exatamente_agora_ja_nao_vale() -> None:
    assert not key_is_active(expires_at=NOW, revoked_at=None, now=NOW)


def test_chave_que_expira_no_proximo_microssegundo_ainda_vale() -> None:
    expires_at = NOW + timedelta(microseconds=1)

    assert key_is_active(expires_at=expires_at, revoked_at=None, now=NOW)


def test_chave_revogada_nao_vale_mesmo_antes_de_expirar() -> None:
    expires_at = NOW + timedelta(days=1)

    assert not key_is_active(expires_at=expires_at, revoked_at=NOW, now=NOW)


def test_escopos_separados_por_virgula_sao_lidos() -> None:
    assert parse_scopes("files:read, runs:write") == {Scope.FILES_READ, Scope.RUNS_WRITE}


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("", id="vazio"),
        pytest.param(" , ", id="so-separadores"),
        pytest.param("files:read,files:admin", id="escopo-desconhecido"),
        pytest.param("FILES:READ", id="maiusculo"),
    ],
)
def test_escopos_invalidos_sao_recusados(text: str) -> None:
    with pytest.raises(InvalidScopesError):
        parse_scopes(text)
