"""Na CI, teste pulado reprova a suíte (`--fail-on-skip`).

Um skip em runtime parece verde e não prova nada. O teste que só pode rodar em certas condições sai
do job por marcador (`-m`) e roda num job onde a condição existe. Assim, qualquer skip ou xfail que
sobre na CI é um teste que deixou de rodar sem ninguém ver, e a suíte falha.

Fora da CI a opção fica desligada: quem roda local sem o shellcheck, por exemplo, vê o motivo no
resumo do `-ra` e segue.
"""

import pytest

FAIL_ON_SKIP_OPTION = "--fail-on-skip"
# Desfechos que contam como "não rodou de verdade".
NOT_RUN_OUTCOMES = ("skipped", "xfailed")


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        FAIL_ON_SKIP_OPTION,
        action="store_true",
        help="reprova a suíte se algum teste terminar pulado ou em xfail",
    )


def pytest_sessionfinish(session: pytest.Session) -> None:
    if not session.config.getoption(FAIL_ON_SKIP_OPTION):
        return
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    if reporter is None:
        return
    not_run = sum(len(reporter.stats.get(outcome, [])) for outcome in NOT_RUN_OUTCOMES)
    if not_run == 0:
        return
    reporter.write_line(
        f"{FAIL_ON_SKIP_OPTION}: {not_run} teste(s) pulado(s) ou em xfail; tire-os do job por"
        " marcador e rode-os onde a condição existe",
        red=True,
        bold=True,
    )
    if session.exitstatus == pytest.ExitCode.OK:
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
