"""Nomes dos arquivos que o lançador e o `LocalRunner` trocam em `meta_dir`.

Sem dependências de propósito: o lançador (`local_supervisor`) roda num interpretador novo
dentro do `LAUNCH_TIMEOUT_S`, e importar `runner` traria `runs.store` e o SQLAlchemy (#377).
"""

PGID_FILE = "pgid"
EXIT_FILE = "exit.json"
