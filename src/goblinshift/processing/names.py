"""Nome original como dado, nunca caminho: radical e extensão seguros para um componente (3.6).

Comum às receitas que derivam o nome da saída do nome enviado (CSV Janitor, Image Forge e
Downloads Detox). O `OutputDir` ainda aplica NFC, o teto de 180 bytes e as colisões; aqui só
se garante que nada do nome original vire separador, controle, arquivo oculto ou `..`.

"Controle" inclui o formato invisível do Unicode (categoria Cf: U+202E, U+200B, U+FEFF no meio,
os isolates U+2066 a U+2069): ele não aparece na tela, mas reordena ou disfarça o nome exibido
(`fatura\u202egpj.exe` aparece terminando em `.jpg`). A mesma regra vale onde o nome é recusado
em vez de trocado (`has_unsafe_char`): `OutputDir`, coleta do worker e `name_prefix` do Detox.
"""

import unicodedata

# Uma "extensão" maior que isto não é tratada como extensão: o nome é do usuário, e uma
# extensão gigante impediria o `OutputDir` de encurtar o radical para caber em 180 bytes.
MAX_EXTENSION_BYTES = 16
_UNSAFE_REPLACEMENT = "_"
# Cc: controles (NUL, quebra de linha, DEL); Cf: formato invisível (bidi, largura zero, BOM).
_UNSAFE_CATEGORIES = frozenset({"Cc", "Cf"})


def has_unsafe_char(text: str) -> bool:
    """Se há controle ou formato invisível: o que um nome de saída nunca pode conter."""
    return any(unicodedata.category(char) in _UNSAFE_CATEGORIES for char in text)


def safe_stem(original_name: str, fallback: str) -> str:
    """Radical do nome original como componente seguro.

    O radical vai até o último ponto; sem ponto, ou com o ponto só no início (`.bashrc`), é o
    nome inteiro. NFC; separadores e controles viram `_`; pontos iniciais saem (sem arquivo
    oculto nem `..`); vazio vira `fallback`.
    """
    stem, _ = _split(original_name)
    return _sanitized(stem).lstrip(".") or fallback


def extension_of(original_name: str) -> str:
    """Extensão depois do último ponto, com o ponto e a caixa original (".JPG"), ou "".

    Separadores e controles viram `_`. Acima de `MAX_EXTENSION_BYTES` não há extensão.
    """
    _, extension = _split(original_name)
    if not extension:
        return ""
    safe = "." + _sanitized(extension)
    return safe if len(safe.encode()) <= MAX_EXTENSION_BYTES else ""


def _split(name: str) -> tuple[str, str]:
    stem, dot, extension = name.rpartition(".")
    if not dot or not stem:
        return name, ""
    return stem, extension


def _sanitized(text: str) -> str:
    normalized = unicodedata.normalize("NFC", text)
    return "".join(_UNSAFE_REPLACEMENT if _is_unsafe(char) else char for char in normalized)


def _is_unsafe(char: str) -> bool:
    return char in ("/", "\\") or has_unsafe_char(char)
