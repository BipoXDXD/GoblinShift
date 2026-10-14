"""CLI do administrador: contas e chaves de API (docs/GoblinShiftPlan.md, 1.8 e 3.9).

Sem painel web na v1: criar, bloquear, liberar e listar contas é por aqui. Usa as mesmas
variáveis GS_* da API para chegar ao banco e ao armazenamento.

    gs-admin create-user --display-name "Ana"
    gs-admin issue-key --user <id> --scopes files:read,files:write,runs:read,runs:write --days 30
    gs-admin revoke-key --key <public_id>
    gs-admin block --user <id>
    gs-admin unblock --user <id>
    gs-admin list-users [--pending]

`issue-key` imprime o segredo uma única vez na saída padrão, e só ele, para que
`> arquivo` o capture sem metadados; o identificador público e a expiração vão para stderr.
`block` bloqueia, revoga sessões e chaves e cancela a execução ativa; repetir completa o que
faltou. `list-users` imprime id, emissor, estado e criação separados por tab, sem o sujeito da
identidade externa; `--pending` mostra só as contas à espera de liberação (ADR 023).
Sai com 0 em sucesso, 1 se a conta ou a chave não existir e 2 em erro de uso.
"""

import argparse
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import Session, sessionmaker

from goblinshift.files.storage import FileStorage
from goblinshift.identity import (
    DEFAULT_KEY_DAYS,
    MAX_KEY_DAYS,
    AuditEventType,
    AuditOutcome,
    InvalidDisplayNameError,
    InvalidKeyLifetimeError,
    InvalidScopesError,
    UnknownAccountError,
    block_account,
    create_admin_user,
    issue_key,
    parse_scopes,
    record_audit_event,
    revoke_key,
    unblock_account,
)
from goblinshift.identity.accounts import AccountListing, list_accounts, list_pending_accounts
from goblinshift.platform.db import make_engine, make_session_factory, unit_of_work
from goblinshift.platform.settings import Settings
from goblinshift.runs.purge import cancel_active_run

EXIT_OK = 0
EXIT_NOT_FOUND = 1
EXIT_USAGE = 2


@dataclass(frozen=True)
class _Context:
    factory: sessionmaker[Session]
    settings: Settings


type Command = Callable[[argparse.Namespace, _Context], int]


def main(argv: Sequence[str] | None = None) -> int:
    args = _argument_parser().parse_args(argv)
    command: Command = args.command
    settings = Settings()
    engine = make_engine(settings)
    try:
        return command(args, _Context(make_session_factory(engine), settings))
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

    block = commands.add_parser(
        "block", help="bloqueia a conta, revoga sessões e chaves e cancela a execução ativa"
    )
    block.add_argument("--user", type=UUID, required=True, help="id da conta")
    block.set_defaults(command=_block)

    unblock = commands.add_parser("unblock", help="libera a conta bloqueada ou pendente")
    unblock.add_argument("--user", type=UUID, required=True, help="id da conta")
    unblock.set_defaults(command=_unblock)

    list_users = commands.add_parser("list-users", help="lista as contas, das mais novas primeiro")
    list_users.add_argument(
        "--pending", action="store_true", help="só as bloqueadas que nunca tiveram sessão"
    )
    list_users.set_defaults(command=_list_users)
    return parser


def _create_user(args: argparse.Namespace, context: _Context) -> int:
    try:
        with unit_of_work(context.factory) as session:
            user_id = create_admin_user(session, args.display_name)
    except InvalidDisplayNameError as error:
        return _fail(EXIT_USAGE, str(error))
    print(user_id)
    return EXIT_OK


def _issue_key(args: argparse.Namespace, context: _Context) -> int:
    try:
        scopes = parse_scopes(args.scopes)
        with unit_of_work(context.factory) as session:
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


def _revoke_key(args: argparse.Namespace, context: _Context) -> int:
    with unit_of_work(context.factory) as session:
        found = revoke_key(session, args.key, datetime.now(UTC))
    if not found:
        return _fail(EXIT_NOT_FOUND, f"chave {args.key} não existe")
    print(f"chave {args.key} revogada", file=sys.stderr)
    return EXIT_OK


def _block(args: argparse.Namespace, context: _Context) -> int:
    now = datetime.now(UTC)
    with unit_of_work(context.factory) as session:
        found = block_account(session, args.user, now)
        if found:
            _audit(session, AuditEventType.ACCOUNT_BLOCKED, args.user, now)
    if not found:
        return _fail(EXIT_NOT_FOUND, f"conta {args.user} não existe")
    # Depois do commit: a partir daqui nenhuma credencial da conta autentica, e o cancelamento
    # abre as próprias transações. Se cair aqui, rodar `block` de novo completa.
    storage = FileStorage(context.settings.storage_root)
    canceled = cancel_active_run(context.factory, storage, args.user)
    print(f"conta {args.user} bloqueada", file=sys.stderr)
    if canceled is not None:
        print(f"execução {canceled.run_id}: {canceled.status}", file=sys.stderr)
    return EXIT_OK


def _unblock(args: argparse.Namespace, context: _Context) -> int:
    with unit_of_work(context.factory) as session:
        found = unblock_account(session, args.user)
        if found:
            _audit(session, AuditEventType.ACCOUNT_UNBLOCKED, args.user, datetime.now(UTC))
    if not found:
        return _fail(EXIT_NOT_FOUND, f"conta {args.user} não existe ou tem exclusão pedida")
    print(f"conta {args.user} liberada", file=sys.stderr)
    return EXIT_OK


def _list_users(args: argparse.Namespace, context: _Context) -> int:
    with unit_of_work(context.factory) as session:
        accounts = list_pending_accounts(session) if args.pending else list_accounts(session)
    for account in accounts:
        print(_listing_line(account))
    return EXIT_OK


def _listing_line(account: AccountListing) -> str:
    fields = [str(account.id), account.issuer, account.status, account.created_at.isoformat()]
    return "\t".join(fields)


def _audit(session: Session, event: AuditEventType, user_id: UUID, now: datetime) -> None:
    # Ação do administrador no servidor: não há IP de cliente a registrar.
    record_audit_event(
        session, event=event, user_id=user_id, ip=None, outcome=AuditOutcome.SUCCESS, now=now
    )


def _fail(exit_code: int, message: str) -> int:
    print(f"gs-admin: {message}", file=sys.stderr)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
