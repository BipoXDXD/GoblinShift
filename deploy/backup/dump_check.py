"""Conferências da restauração testada que o bash não faz bem (docs/adr/9xx-backup-externo.md).

Só biblioteca padrão: roda com o `python3` do runner, fora do ambiente do projeto.

    dump_check.py latest --prefix pg/ --max-age-hours 26 < listagem.xml
        Escolhe o dump mais recente pelo `LastModified` que o S3 grava, não pelo nome, que a
        chave da VPS escreve como quiser. Reprova nome ou envio com data futura além da folga e
        nome posterior ao envio. Imprime a chave e a idade em horas, uma por linha.

    dump_check.py retention --min-days 30 < headers-do-head.txt
        Confere que o objeto está sob Object Lock em modo compliance por pelo menos `min-days`
        a partir do envio: sem isso, a chave que escreve sobrescreve ou esconde o dump.

Qualquer reprovação sai com status 1 e a mensagem no stderr. `--now` serve aos testes.
"""

import argparse
import re
import sys
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.message import Message
from email.parser import HeaderParser
from email.utils import parsedate_to_datetime

# Folga para o relógio do runner, da VPS e do S3 não concordarem.
CLOCK_SKEW = timedelta(hours=1)
SECONDS_PER_HOUR = 3600
NAME = re.compile(r"goblinshift-(?P<instant>\d{8}T\d{6}Z)-[0-9a-f]{32}\.dump\.age")
COMPLIANCE = "COMPLIANCE"


class CheckFailedError(Exception):
    """Reprovação com a mensagem que vai para o log."""


@dataclass(frozen=True)
class Dump:
    key: str
    named_at: datetime
    sent_at: datetime


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    now = datetime.fromisoformat(args.now) if args.now else datetime.now(UTC)
    try:
        if args.command == "latest":
            dump = latest_dump(sys.stdin.read(), args.prefix, now)
            age_hours = check_age(dump, now, args.max_age_hours)
            print(dump.key)
            print(age_hours)
        else:
            check_retention(sys.stdin.read(), timedelta(days=args.min_days))
    except CheckFailedError as error:
        print(error, file=sys.stderr)
        return 1
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    latest = commands.add_parser("latest")
    latest.add_argument("--prefix", required=True)
    latest.add_argument("--max-age-hours", type=int, required=True)
    retention = commands.add_parser("retention")
    retention.add_argument("--min-days", type=int, required=True)
    for command in (latest, retention):
        command.add_argument("--now", help="instante ISO 8601 com fuso (testes)")
    return parser


def latest_dump(listing_xml: str, prefix: str, now: datetime) -> Dump:
    """O dump enviado por último, entre as chaves no formato do pg-backup.sh."""
    parsed = (_dump(key, sent_at) for key, sent_at in _listed(listing_xml, prefix))
    dumps = [dump for dump in parsed if dump is not None]
    if not dumps:
        raise CheckFailedError(f"nenhum dump em {prefix}")
    for dump in dumps:
        if dump.named_at > now + CLOCK_SKEW or dump.sent_at > now + CLOCK_SKEW:
            raise CheckFailedError(f"dump com data futura: {_instant(dump.named_at)}")
    chosen = max(dumps, key=lambda dump: (dump.sent_at, dump.key))
    if chosen.named_at > chosen.sent_at + CLOCK_SKEW:
        raise CheckFailedError(
            f"o dump de {_instant(chosen.named_at)} tem nome posterior ao envio "
            f"({_instant(chosen.sent_at)})"
        )
    return chosen


def check_age(dump: Dump, now: datetime, max_age_hours: int) -> int:
    """Idade em horas inteiras desde o envio; reprova a partir de `max_age_hours`."""
    age_hours = max(0, int((now - dump.sent_at).total_seconds() // SECONDS_PER_HOUR))
    if age_hours >= max_age_hours:
        raise CheckFailedError(
            f"o dump mais recente tem {age_hours} horas (máximo: {max_age_hours})"
        )
    return age_hours


def check_retention(head_response: str, minimum: timedelta) -> None:
    headers = _headers(head_response)
    mode = (headers.get("x-amz-object-lock-mode") or "").strip().upper()
    if mode != COMPLIANCE:
        raise CheckFailedError(
            f"retenção ausente: o dump não está sob Object Lock em modo compliance ({mode or '-'})"
        )
    until = _parse_time(headers.get("x-amz-object-lock-retain-until-date"))
    sent_at = _parse_http_date(headers.get("last-modified"))
    if until is None or sent_at is None:
        raise CheckFailedError("retenção ilegível: sem data de envio ou de fim da retenção")
    if until - sent_at < minimum - CLOCK_SKEW:
        days = (until - sent_at) / timedelta(days=1)
        raise CheckFailedError(
            f"retenção curta: {days:.1f} dias a partir do envio (mínimo: {minimum.days})"
        )


def _listed(listing_xml: str, prefix: str) -> list[tuple[str, datetime]]:
    # A resposta do S3 não tem DTD; recusá-la fecha entidades externas e expansão exponencial.
    if "<!DOCTYPE" in listing_xml or "<!ENTITY" in listing_xml:
        raise CheckFailedError("listagem do S3 ilegível: DTD inesperado")
    try:
        root = ET.fromstring(listing_xml)  # noqa: S314 - sem DTD, conferido acima
    except ET.ParseError as error:
        raise CheckFailedError(f"listagem do S3 ilegível: {error}") from error
    if _text(root, "IsTruncated") == "true":
        raise CheckFailedError(f"listagem truncada em {prefix}")
    listed: list[tuple[str, datetime]] = []
    for contents in root.iter():
        if _local(contents.tag) != "Contents":
            continue
        key = _text(contents, "Key")
        sent_at = _parse_time(_text(contents, "LastModified"))
        if key is not None and sent_at is not None and key.startswith(prefix):
            listed.append((key, sent_at))
    return listed


def _dump(key: str, sent_at: datetime) -> Dump | None:
    match = NAME.fullmatch(key.rsplit("/", 1)[-1])
    if match is None:
        return None
    try:
        named_at = datetime.strptime(match["instant"], "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
    except ValueError:
        return None
    return Dump(key, named_at, sent_at)


def _headers(head_response: str) -> Message:
    # A primeira linha é a de status; o resto, headers no formato RFC 822.
    _, _, rest = head_response.replace("\r\n", "\n").partition("\n")
    return HeaderParser().parsestr(rest)


def _text(element: ET.Element, name: str) -> str | None:
    for child in element:
        if _local(child.tag) == name:
            return child.text
    return None


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _parse_http_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return parsedate_to_datetime(value.strip())
    except TypeError, ValueError:
        return None


def _instant(moment: datetime) -> str:
    return f"{moment.astimezone(UTC):%Y-%m-%dT%H:%M:%SZ}"


if __name__ == "__main__":
    sys.exit(main())
