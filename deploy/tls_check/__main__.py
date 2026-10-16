"""Confere se o certificado TLS ainda tem 14 dias ou mais (workflow tls-check).

    cd deploy && python3 -m tls_check goblinshift.dev

Saída: 0 com 14 dias ou mais; 1 com menos, ou em qualquer erro (DNS, conexão, handshake, cadeia
inválida, hostname errado). O workflow transforma isso no ping do healthchecks.io.
"""

import argparse
import ssl
import sys
from collections.abc import Sequence
from datetime import UTC, datetime

from tls_check import MIN_DAYS_LEFT, days_remaining, has_enough_margin, read_not_after

DEFAULT_HOST = "goblinshift.dev"
DEFAULT_PORT = 443
EXIT_OK = 0
EXIT_FAILURE = 1


def main(argv: Sequence[str] | None = None, *, now: datetime | None = None) -> int:
    args = _parse_args(argv)
    context = ssl.create_default_context(cafile=args.cafile)
    try:
        not_after = read_not_after(args.host, args.port, context)
    except OSError as error:
        print(f"falha ao ler o certificado de {args.host}:{args.port}: {error}", file=sys.stderr)
        return EXIT_FAILURE

    days_left = days_remaining(not_after, now or datetime.now(UTC))
    print(f"{args.host}: certificado vence em {not_after:%Y-%m-%d %H:%M} UTC ({days_left} dias)")
    if has_enough_margin(days_left):
        return EXIT_OK
    print(f"{args.host}: faltam menos de {MIN_DAYS_LEFT} dias para o vencimento", file=sys.stderr)
    return EXIT_FAILURE


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python3 -m tls_check", description=__doc__)
    parser.add_argument("host", nargs="?", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument(
        "--cafile", help="CA confiável no lugar das do sistema (usado pelos testes)"
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    sys.exit(main())
