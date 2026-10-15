"""Política de divulgação de vulnerabilidades (J-7): SECURITY.md e /.well-known/security.txt.

O security.txt segue a RFC 9116 e é servido pelo Nginx a partir do build da SPA (web/public/); o
teste de que ele sai como texto, e não como o index.html, fica em tests/test_nginx_spa.py.
"""

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SECURITY_TXT = ROOT / "web" / "public" / ".well-known" / "security.txt"
SECURITY_MD = ROOT / "SECURITY.md"

ADVISORY_URL = "https://github.com/BipoXDXD/GoblinShift/security/advisories/new"
SECURITY_EMAIL = "abuso@goblinshift.dev"
CANONICAL_URL = "https://goblinshift.dev/.well-known/security.txt"
POLICY_URL = "https://github.com/BipoXDXD/GoblinShift/blob/main/SECURITY.md"
# A RFC 9116 (2.5.5) recomenda Expires a menos de um ano; com 30 dias de folga dá tempo de
# renovar e implantar antes de o arquivo vencer.
MAX_VALIDITY = timedelta(days=366)
RENEWAL_NOTICE = timedelta(days=30)


def _fields() -> list[tuple[str, str]]:
    fields = []
    for line in SECURITY_TXT.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        name, separator, value = line.partition(": ")
        assert separator, f"linha fora do formato 'Campo: valor': {line!r}"
        fields.append((name.lower(), value))
    return fields


def _values(name: str) -> list[str]:
    return [value for field, value in _fields() if field == name]


def _expires() -> datetime:
    (value,) = _values("expires")
    return datetime.fromisoformat(value)


def test_contatos_sao_o_advisory_do_github_e_depois_o_email_de_seguranca() -> None:
    # A ordem indica a preferência (RFC 9116, 2.5.3): o relato privado do GitHub primeiro.
    assert _values("contact") == [ADVISORY_URL, f"mailto:{SECURITY_EMAIL}"]


def test_expires_e_unico_em_utc_e_a_menos_de_um_ano() -> None:
    expires = _expires()

    assert expires.utcoffset() == timedelta(0)
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", _values("expires")[0])
    assert expires - datetime.now(UTC) <= MAX_VALIDITY


def test_expires_nao_esta_a_menos_de_30_dias_de_vencer() -> None:
    # Lembrete de renovação com o relógio real de propósito: o que importa é a data de hoje.
    # Para renovar, adiantar o Expires em até um ano e implantar.
    restante = _expires() - datetime.now(UTC)

    assert restante >= RENEWAL_NOTICE, (
        f"security.txt vence em {restante.days} dias: renove o Expires em {SECURITY_TXT}"
    )


def test_idiomas_preferidos_sao_portugues_e_ingles() -> None:
    assert _values("preferred-languages") == ["pt, en"]


def test_canonical_e_policy_apontam_para_os_enderecos_publicos() -> None:
    assert _values("canonical") == [CANONICAL_URL]
    assert _values("policy") == [POLICY_URL]


def test_security_txt_so_tem_campos_da_rfc() -> None:
    conhecidos = {
        "acknowledgments",
        "canonical",
        "contact",
        "encryption",
        "expires",
        "hiring",
        "policy",
        "preferred-languages",
        "csaf",
    }
    assert {name for name, _ in _fields()} <= conhecidos


def test_security_md_cita_os_dois_canais_e_o_prazo_de_resposta() -> None:
    texto = SECURITY_MD.read_text(encoding="utf-8")

    assert ADVISORY_URL in texto
    assert SECURITY_EMAIL in texto
    assert "3 dias úteis" in texto
    assert "StillOn" in texto
