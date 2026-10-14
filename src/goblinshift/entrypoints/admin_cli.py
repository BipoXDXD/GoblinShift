"""CLI do administrador: contas e chaves de API (docs/GoblinShiftPlan.md, fatia 3 em 3.9).

Até o login web da fatia 6, o acesso à API é por chave emitida aqui. Usa as mesmas
variáveis GS_* da API para chegar ao banco.

    gs-admin create-user --display-name "Ana"
    gs-admin issue-key --user <id> --scopes files:read,files:write,runs:read,runs:write --days 30
    gs-admin revoke-key --key <public_id>

`issue-key` imprime o segredo uma única vez na saída padrão, e só ele, para que
`> arquivo` o capture sem metadados; o identificador público e a expiração vão para stderr.
Sai com 0 em sucesso, 1 se a conta ou a chave não existir e 2 em erro de uso.
"""

import argparse
import sys
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import Session, sessionmaker

from goblinshift.identity import (
    DEFAULT_KEY_DAYS,
    MAX_KEY_DAYS,
    InvalidDisplayNameError,
    InvalidKeyLifetimeError,
    InvalidScopesError,
    UnknownAccountError,
    create_admin_user,
    issue_key,
    parse_scopes,
    revoke_key,
)
from goblinshift.platform.db import make_engine, make_session_factory, unit_of_work
from goblinshift.platform.settings import Settings

EXIT_OK = 0
EXIT_NOT_FOUND = 1
EXIT_USAGE = 2

type Command = Callable[[argparse.Namespace, sessionmaker[Session]], int]


def main(argv: Sequence[str] | None = None) -> int:
    args = _argument_parser().parse_args(argv)
    command: Command = args.command
    engine = make_engine(Settings())
    try:
        return command(args, make_session_factory(engine))
    finally:
        engine.dispose()


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="gs-admin", description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(required=True, metavar="comando")

    create_user = commands.add_parser("create-user", help="cria uma conta e imprime o id")
    create_user.add_argument("--display-name", required=True, help="nome exibido da conta")
    create_user.set_defaults(command=_create_user)

    issue = commands.add_parser("issue-key", help="emite uma chave e imprime o segredo")
    issue.add_argument("--user", type=UUID, required=True, help="id da conta")
    issue.add_argument("--scopes", required=True, help="escopos separados por vírgula")
    issue.add_argument(
        "--days",
        type=int,
        default=DEFAULT_KEY_DAYS,
        help=f"validade em dias, até {MAX_KEY_DAYS} (padrão: {DEFAULT_KEY_DAYS})",
    )
    issue.set_defaults(command=_issue_key)

    revoke = commands.add_parser("revoke-key", help="revoga uma chave pelo identificador")
    revoke.add_argument("--key", required=True, help="identificador público da chave")
    revoke.set_defaults(command=_revoke_key)
    return parser


def _create_user(args: argparse.Namespace, factory: sessionmaker[Session]) -> int:
    try:
        with unit_of_work(factory) as session:
            user_id = create_admin_user(session, args.display_name)
    except InvalidDisplayNameError as error:
        return _fail(EXIT_USAGE, str(error))
    print(user_id)
    return EXIT_OK


def _issue_key(args: argparse.Namespace, factory: sessionmaker[Session]) -> int:
    try:
        scopes = parse_scopes(args.scopes)
        with unit_of_work(factory) as session:
            issued = issue_key(
                session,
                owner_id=args.user,
                scopes=scopes,
                now=datetime.now(UTC),
                days=args.days,
            )
    except (InvalidScopesError, InvalidKeyLifetimeError) as error:
        return _fail(EXIT_USAGE, str(error))
    except UnknownAccountError as error:
        return _fail(EXIT_NOT_FOUND, str(error))
    print(f"chave {issued.public_id} válida até {issued.expires_at.isoformat()}", file=sys.stderr)
    print(issued.token)
    return EXIT_OK


def _revoke_key(args: argparse.Namespace, factory: sessionmaker[Session]) -> int:
    with unit_of_work(factory) as session:
        found = revoke_key(session, args.key, datetime.now(UTC))
    if not found:
        return _fail(EXIT_NOT_FOUND, f"chave {args.key} não existe")
    print(f"chave {args.key} revogada", file=sys.stderr)
    return EXIT_OK


def _fail(exit_code: int, message: str) -> int:
    print(f"gs-admin: {message}", file=sys.stderr)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
