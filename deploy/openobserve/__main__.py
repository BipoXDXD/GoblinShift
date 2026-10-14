"""Linha de comando dos SLOs e alertas (runbook em deploy/openobserve/README.md).

    cd deploy && python3 -m openobserve provision --secrets-file ~/.config/goblinshift/alertas.env
    cd deploy && python3 -m openobserve sli --secrets-file ~/.config/goblinshift/alertas.env

Credencial e URLs de destino vêm só dos arquivos de segredos (formato .env, chmod 600); não há
opção para elas na linha de comando. Saída: 0 com sucesso, 1 se o OpenObserve falhou ou recusou
algo, 2 para configuração inválida.
"""

import argparse
import sys
import time
from collections.abc import Sequence
from pathlib import Path

from openobserve.catalog import SLOS, DestinationKey, alerts
from openobserve.client import Credentials, OpenObserve, OpenObserveError
from openobserve.provision import ProvisionError, provision
from openobserve.report import SliReport
from openobserve.settings import ProvisionSettings, SettingsError

PRODUCTION_PREFIX = "gs-"
DEFAULT_URL = "http://127.0.0.1:5080"
DEFAULT_STREAM = "default"
# Janela deslizante dos SLOs (3.7), igual à retenção da telemetria (ADR 013).
SLO_WINDOW_DAYS = 30
US_PER_DAY = 86_400_000_000
EXIT_FAILURE = 1
EXIT_CONFIG = 2


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        settings = ProvisionSettings.from_files(args.secrets_file)
    except SettingsError as error:
        print(f"configuração: {error}", file=sys.stderr)
        return EXIT_CONFIG
    api = OpenObserve(args.url, Credentials(settings.email, settings.password))
    try:
        if args.command == "provision":
            _provision(api, settings, args.stream)
        else:
            _report(api, args.stream)
    except (OpenObserveError, ProvisionError) as error:
        print(f"falhou: {error}", file=sys.stderr)
        return EXIT_FAILURE
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 -m openobserve", description=__doc__.split("\n")[0]
    )
    commands = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (
        ("provision", "cria ou atualiza template, destinos e alertas (idempotente)"),
        ("sli", f"SLI e orçamento de erro dos últimos {SLO_WINDOW_DAYS} dias"),
    ):
        command = commands.add_parser(name, help=help_text)
        command.add_argument(
            "--secrets-file",
            type=Path,
            action="append",
            required=True,
            help="arquivo .env com chmod 600; repetível, e o último vence numa chave repetida",
        )
        command.add_argument(
            "--url", default=DEFAULT_URL, help=f"OpenObserve (padrão: {DEFAULT_URL}, o do túnel)"
        )
        command.add_argument(
            "--stream", default=DEFAULT_STREAM, help="stream de traces (padrão: default)"
        )
    return parser


def _provision(api: OpenObserve, settings: ProvisionSettings, stream: str) -> None:
    provision(
        api,
        alerts=alerts(stream=stream, prefix=PRODUCTION_PREFIX),
        destinations={
            DestinationKey.URGENT: settings.urgent_url,
            DestinationKey.WARNING: settings.warning_url,
            DestinationKey.HEARTBEAT: settings.heartbeat_url,
        },
        prefix=PRODUCTION_PREFIX,
        log=print,
    )


def _report(api: OpenObserve, stream: str) -> None:
    end_us = time.time_ns() // 1000
    start_us = end_us - SLO_WINDOW_DAYS * US_PER_DAY
    reports = []
    for slo in SLOS:
        rows = api.search(slo.sli_sql(stream), "traces", start_us, end_us)
        if not rows:
            raise OpenObserveError(f"a consulta do SLI de {slo.name} não devolveu linha")
        row = rows[0]
        reports.append(SliReport(slo, eligible=int(row["eligible"]), good=int(row["good"])))
    for report in reports:
        print(report.line())
    exhausted = [report.slo.name for report in reports if report.exhausted]
    if exhausted:
        print(
            f"Orçamento ESGOTADO: {', '.join(exhausted)}. Pela política de 3.7, a próxima fatia "
            "não começa; o trabalho é estabilidade até o orçamento voltar."
        )


if __name__ == "__main__":
    raise SystemExit(main())
