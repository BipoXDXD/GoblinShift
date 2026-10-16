"""Código de referência do login recusado (ponto J-10 dos termos: recurso contra o bloqueio).

A tela de login recusado mostra um código que a pessoa cita no e-mail de recurso. O código é o
`request_id` do callback (o mesmo do header `X-Request-ID`), vai no fragmento do redirect e fica
gravado no evento `LOGIN_FAILED`, com a conta recusada, para o operador achá-la.

STRIDE: enumeração de contas (Information Disclosure). Toda recusa tem o mesmo formato e só o
código aleatório muda; ele não carrega dado pessoal nem o motivo.
"""

import re
from dataclasses import replace
from typing import Any

import httpx2 as httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.identity.tables import app_user, audit_event
from goblinshift.platform.db import unit_of_work
from tests.api.auth.support import (
    accounts,
    assert_login_failed,
    audit_by_ref,
    browser,
    login,
    mask_ref,
    open_signup,
)
from tests.identity.fake_providers import FakeProviders

pytestmark = pytest.mark.integration

PENDING_SUBJECT = "pendente-111"
BLOCKED_SUBJECT = "bloqueada-222"


def login_as(app: FastAPI, fake: FakeProviders, subject: str) -> httpx.Response:
    fake.google_subject = subject
    with browser(app) as client:
        return login(client, fake, "google")


def account_id(factory: sessionmaker[Session], subject: str) -> Any:
    [account] = [row for row in accounts(factory) if row["subject"] == subject]
    return account["id"]


def blocked_after_a_session(
    app: FastAPI, fake: FakeProviders, factory: sessionmaker[Session]
) -> None:
    """Conta que já entrou e depois foi bloqueada (abuso), com o cadastro fechado de novo."""
    open_signup(app)
    assert login_as(app, fake, BLOCKED_SUBJECT).status_code == 303
    with unit_of_work(factory) as session:
        session.execute(
            app_user.update().where(app_user.c.subject == BLOCKED_SUBJECT).values(status="blocked")
        )
    app.state.web_identity = replace(app.state.web_identity, signup_open=False)


def shape(response: httpx.Response) -> tuple[int, str, bytes, list[str], list[str]]:
    """O que o navegador vê, com o código aleatório trocado por um marcador fixo."""
    location = mask_ref(response.headers["location"])
    cookies = sorted(cookie.split("=")[0] for cookie in response.headers.get_list("set-cookie"))
    return response.status_code, location, response.content, cookies, sorted(response.headers)


def test_conta_bloqueada_e_cadastro_fechado_respondem_igual(
    login_app: FastAPI, fake: FakeProviders, factory: sessionmaker[Session]
) -> None:
    blocked_after_a_session(login_app, fake, factory)

    pending = login_as(login_app, fake, PENDING_SUBJECT)
    blocked = login_as(login_app, fake, BLOCKED_SUBJECT)

    assert assert_login_failed(pending) != assert_login_failed(blocked)
    assert shape(pending) == shape(blocked)
    assert shape(pending)[1] == "/?login_error=failed#ref=<ref>"


@pytest.mark.parametrize("subject", [PENDING_SUBJECT, BLOCKED_SUBJECT])
def test_codigo_leva_o_operador_ao_evento_e_a_conta(
    login_app: FastAPI, fake: FakeProviders, factory: sessionmaker[Session], subject: str
) -> None:
    blocked_after_a_session(login_app, fake, factory)

    ref = assert_login_failed(login_as(login_app, fake, subject))

    assert audit_by_ref(factory, ref) == [("LOGIN_FAILED", account_id(factory, subject))]


def test_falha_no_provedor_tambem_tem_codigo_sem_conta(
    login_app: FastAPI, fake: FakeProviders, factory: sessionmaker[Session]
) -> None:
    fake.claims_patch = {"aud": "outro-cliente"}

    ref = assert_login_failed(login_as(login_app, fake, PENDING_SUBJECT))

    assert audit_by_ref(factory, ref) == [("LOGIN_FAILED", None)]


def test_codigo_nao_carrega_dado_da_conta(
    login_app: FastAPI, fake: FakeProviders, factory: sessionmaker[Session]
) -> None:
    response = login_as(login_app, fake, PENDING_SUBJECT)

    assert_login_failed(response)
    location = response.headers["location"]
    for value in (PENDING_SUBJECT, str(account_id(factory, PENDING_SUBJECT)), "Conta Google"):
        assert value not in location
    # Fragmento, e não query: o navegador nunca o envia a servidor, nem no Referer.
    assert "ref=" not in location.split("#", 1)[0]


def test_login_aceito_grava_o_request_id_na_auditoria(
    login_app: FastAPI, fake: FakeProviders, factory: sessionmaker[Session]
) -> None:
    open_signup(login_app)

    response = login_as(login_app, fake, PENDING_SUBJECT)

    assert response.headers["location"] == "/"
    with unit_of_work(factory) as session:
        stored = session.execute(select(audit_event.c.request_id)).scalar_one()
    assert str(stored) == response.headers["x-request-id"]
    assert re.fullmatch(r"[0-9a-f-]{36}", response.headers["x-request-id"])
