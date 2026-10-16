"""O comando inteiro contra um servidor TLS local, com CA e certificados gerados no teste."""

import socket
import ssl
import threading
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from tls_check.__main__ import main

HOST = "localhost"
# Instante fixo para o cálculo; a validade dos certificados é relativa a ele.
NOW = datetime.now(UTC).replace(microsecond=0)


@dataclass(frozen=True)
class Authority:
    key: ec.EllipticCurvePrivateKey
    cert: x509.Certificate
    pem_path: Path


def _name(common_name: str) -> x509.Name:
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])


def _make_authority(directory: Path, common_name: str) -> Authority:
    key = ec.generate_private_key(ec.SECP256R1())
    ski = x509.SubjectKeyIdentifier.from_public_key(key.public_key())
    cert = (
        x509.CertificateBuilder()
        .subject_name(_name(common_name))
        .issuer_name(_name(common_name))
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(NOW - timedelta(days=1))
        .not_valid_after(NOW + timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=False,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(ski, critical=False)
        .sign(key, hashes.SHA256())
    )
    pem_path = directory / f"{common_name}.pem"
    pem_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return Authority(key, cert, pem_path)


def _issue_leaf(
    directory: Path, authority: Authority, dns_name: str, not_after: datetime
) -> tuple[Path, Path]:
    key = ec.generate_private_key(ec.SECP256R1())
    cert = (
        x509.CertificateBuilder()
        .subject_name(_name(dns_name))
        .issuer_name(authority.cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(NOW - timedelta(days=1))
        .not_valid_after(not_after)
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(dns_name)]), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(authority.key.public_key()),
            critical=False,
        )
        .sign(authority.key, hashes.SHA256())
    )
    cert_path = directory / f"{dns_name}-leaf.pem"
    key_path = directory / f"{dns_name}-leaf.key"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return cert_path, key_path


@pytest.fixture
def authority(tmp_path: Path) -> Authority:
    return _make_authority(tmp_path, "CA de teste")


@contextmanager
def _serve_once(cert_path: Path, key_path: Path) -> Iterator[int]:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_path, key_path)
    listener = socket.create_server(("127.0.0.1", 0))
    port: int = listener.getsockname()[1]

    def accept() -> None:
        connection, _ = listener.accept()
        try:
            with context.wrap_socket(connection, server_side=True):
                pass
        except OSError:
            # O cliente aborta o handshake nos cenários de certificado recusado; o que importa
            # é o código de saída dele, conferido pelo teste.
            pass

    thread = threading.Thread(target=accept, daemon=True)
    thread.start()
    with listener:
        yield port
    thread.join(timeout=5)


class Serve(Protocol):
    def __call__(
        self, *, not_after: datetime, dns_name: str = HOST, issuer: Authority | None = None
    ) -> int: ...


@pytest.fixture
def serve(tmp_path: Path, authority: Authority) -> Iterator[Serve]:
    with ExitStack() as servers:

        def start(
            *, not_after: datetime, dns_name: str = HOST, issuer: Authority | None = None
        ) -> int:
            cert_path, key_path = _issue_leaf(tmp_path, issuer or authority, dns_name, not_after)
            return servers.enter_context(_serve_once(cert_path, key_path))

        yield start


def _run(port: int, authority: Authority) -> int:
    return main([HOST, "--port", str(port), "--cafile", str(authority.pem_path)], now=NOW)


def test_exits_zero_with_fourteen_days_left(serve: Serve, authority: Authority) -> None:
    port = serve(not_after=NOW + timedelta(days=14))

    assert _run(port, authority) == 0


def test_exits_one_with_thirteen_days_left(serve: Serve, authority: Authority) -> None:
    port = serve(not_after=NOW + timedelta(days=13))

    assert _run(port, authority) == 1


def test_reports_days_left(
    serve: Serve, authority: Authority, capsys: pytest.CaptureFixture[str]
) -> None:
    port = serve(not_after=NOW + timedelta(days=60))

    _run(port, authority)

    assert "60 dias" in capsys.readouterr().out


def test_exits_one_when_chain_is_untrusted(
    serve: Serve, tmp_path: Path, authority: Authority
) -> None:
    stranger = _make_authority(tmp_path, "CA estranha")
    port = serve(not_after=NOW + timedelta(days=60), issuer=stranger)

    assert _run(port, authority) == 1


def test_exits_one_when_hostname_does_not_match(serve: Serve, authority: Authority) -> None:
    port = serve(dns_name="outro.example", not_after=NOW + timedelta(days=60))

    assert _run(port, authority) == 1


def test_exits_one_when_certificate_expired(serve: Serve, authority: Authority) -> None:
    port = serve(not_after=NOW - timedelta(hours=1))

    assert _run(port, authority) == 1


def test_exits_one_when_connection_refused(authority: Authority) -> None:
    with socket.create_server(("127.0.0.1", 0)) as unused:
        port = unused.getsockname()[1]

    assert _run(port, authority) == 1


def test_exits_one_when_name_does_not_resolve(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["goblinshift.invalid"], now=NOW) == 1
    assert "goblinshift.invalid" in capsys.readouterr().err
