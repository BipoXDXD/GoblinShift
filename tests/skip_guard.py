"""Na CI, teste pulado reprova a suíte (`--fail-on-skip`).

Um skip em runtime parece verde e não prova nada. O teste que só pode rodar em certas condições sai
do job por marcador (`-m`) e roda num job onde a condição existe. Assim, qualquer skip ou xfail que
sobre na CI é um teste que deixou de rodar sem ninguém ver, e a suíte falha.

Fora da CI a opção fica desligada: quem roda local sem o shellcheck, por exemplo, vê o motivo no
resumo do `-ra` e segue.
"""

import pytest

FAIL_ON_SKIP_OPTION = "--fail-on-skip"
# Desfechos em que a suíte ainda "passaria"; um skip no módulo inteiro dá NO_TESTS_COLLECTED.
PASSING_EXIT_CODES = (pytest.ExitCode.OK, pytest.ExitCode.NO_TESTS_COLLECTED)


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        FAIL_ON_SKIP_OPTION,
        action="store_true",
        help="reprova a suíte se algum teste terminar pulado ou em xfail",
    )


def pytest_configure(config: pytest.Config) -> None:
    if config.getoption(FAIL_ON_SKIP_OPTION):
        config.pluginmanager.register(NotRunCounter(config), "skip-guard-counter")


class NotRunCounter:
    """Conta pelos relatórios, não pelas estatísticas do terminal: com `-p no:terminal` elas não
    existem, e o guard passaria calado."""

    def __init__(self, config: pytest.Config) -> None:
        self._config = config
        self._not_run = 0

    def pytest_collectreport(self, report: pytest.CollectReport) -> None:
        # Skip no nível do módulo (`allow_module_level=True`) só aparece na coleta.
        if report.skipped:
            self._not_run += 1

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        # O xfail chega como skipped (com `wasxfail`); o xpass não strict, como passed.
        if report.skipped:
            self._not_run += 1

    def pytest_sessionfinish(self, session: pytest.Session) -> None:
        if self._not_run == 0:
            return
        reporter = self._config.pluginmanager.get_plugin("terminalreporter")
        if reporter is not None:
            reporter.write_line(
                f"{FAIL_ON_SKIP_OPTION}: {self._not_run} teste(s) pulado(s) ou em xfail; tire-os do"
                " job por marcador e rode-os onde a condição existe",
                red=True,
                bold=True,
            )
        if session.exitstatus in PASSING_EXIT_CODES:
            session.exitstatus = pytest.ExitCode.TESTS_FAILED
