"""Medição do pior caso do Image Forge na VPS (deploy/acceptance/image_forge_load.py).

O script inteiro só roda no host, como root e sob runsc; aqui ficam as partes puras: o job que
ele monta a partir do expected.json do gerador e o veredito sobre o que a tentativa relatou.
"""

import pytest
from acceptance import image_forge_load as load
from acceptance.run import MAINTENANCE_CRITERION, Outcome

from tests.acceptance.test_run import FakeDocker

EXPECTED = {
    "recipe_id": "image-forge",
    "recipe_version": 1,
    "parameters": {"make_thumbnails": True},
    "status": "succeeded",
    "inputs": [
        {
            "path": "inputs/0/carga-001.jpg",
            "manifest": {
                "position": 0,
                "original_name": "carga-001.jpg",
                "size": 10,
                "sha256": "a" * 64,
                "status": "ok",
            },
        },
        {
            "path": "inputs/1/carga-002.jpg",
            "manifest": {
                "position": 1,
                "original_name": "carga-002.jpg",
                "size": 12,
                "sha256": "b" * 64,
                "status": "ok",
            },
        },
    ],
}


def test_job_tem_as_entradas_na_ordem_com_o_nome_do_worker() -> None:
    job, copies = load.plan_job(EXPECTED)

    assert job == {
        "recipe_id": "image-forge",
        "recipe_version": 1,
        "parameters": {"make_thumbnails": True},
        "inputs": [
            {
                "position": 0,
                "path": "0000.bin",
                "original_name": "carga-001.jpg",
                "size": 10,
                "sha256": "a" * 64,
            },
            {
                "position": 1,
                "path": "0001.bin",
                "original_name": "carga-002.jpg",
                "size": 12,
                "sha256": "b" * 64,
            },
        ],
    }
    assert copies == [
        ("inputs/0/carga-001.jpg", "0000.bin"),
        ("inputs/1/carga-002.jpg", "0001.bin"),
    ]


def summary(ok: int, outputs: int) -> dict[str, int]:
    return {"inputs": 2, "ok": ok, "rejected": 2 - ok, "duplicates": 0, "outputs": outputs}


def test_veredito_passa_com_todas_processadas_dentro_do_teto() -> None:
    outcome = load.judge(exit_code=0, duration_s=600.0, timed_out=False, summary=summary(2, 4))

    assert outcome.passed
    assert "600 s" in outcome.evidence


def test_veredito_falha_acima_do_teto_de_10_min() -> None:
    outcome = load.judge(exit_code=0, duration_s=600.5, timed_out=False, summary=summary(2, 4))

    assert not outcome.passed


def test_veredito_falha_quando_o_executor_foi_parado_pelo_prazo() -> None:
    outcome = load.judge(exit_code=137, duration_s=610.0, timed_out=True, summary=None)

    assert not outcome.passed


def test_veredito_falha_com_imagem_rejeitada() -> None:
    outcome = load.judge(exit_code=0, duration_s=100.0, timed_out=False, summary=summary(1, 2))

    assert not outcome.passed


def test_veredito_falha_com_saida_diferente_de_zero() -> None:
    outcome = load.judge(exit_code=3, duration_s=100.0, timed_out=False, summary=summary(2, 4))

    assert not outcome.passed


# --- Medição com a manutenção suspensa: a manutenção religada também entra no veredito ---------

MEASURED = Outcome(load.CRITERION, True, "420 s")


def test_imprime_medicao_e_manutencao_e_sai_com_0_se_as_duas_passam(
    capsys: pytest.CaptureFixture[str],
) -> None:
    docker = FakeDocker()

    code = load.measure_with_maintenance_suspended(lambda: MEASURED, docker)

    printed = capsys.readouterr().out
    assert code == 0
    assert f"PASSOU  {load.CRITERION}: 420 s" in printed
    assert f"PASSOU  {MAINTENANCE_CRITERION}" in printed
    assert docker.commands() == ["ps", "stop", "start", "inspect"]


def test_medicao_que_falha_sai_com_1_mesmo_com_a_manutencao_de_volta(
    capsys: pytest.CaptureFixture[str],
) -> None:
    slow = Outcome(load.CRITERION, False, "640 s")

    code = load.measure_with_maintenance_suspended(lambda: slow, FakeDocker())

    printed = capsys.readouterr().out
    assert code == 1
    assert f"FALHOU  {load.CRITERION}: 640 s" in printed
    assert f"PASSOU  {MAINTENANCE_CRITERION}" in printed


def test_manutencao_que_nao_volta_reprova_a_medicao_que_passou(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = load.measure_with_maintenance_suspended(
        lambda: MEASURED, FakeDocker(running_after_start="false")
    )

    printed = capsys.readouterr().out
    assert code == 1
    assert f"PASSOU  {load.CRITERION}: 420 s" in printed
    assert f"FALHOU  {MAINTENANCE_CRITERION}" in printed
