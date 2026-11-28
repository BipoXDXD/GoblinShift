"""O token do teste de contrato não passa pela linha de comando (issue #366, scripts).

`scripts/contract-test.sh` emite uma chave real com o `gs-admin` e a entrega ao Schemathesis. Em
argumento de processo, qualquer usuário do runner a leria em `ps`. O script roda aqui com `uv` e
`curl` falsos no `PATH`: o `uv` grava o argv de toda chamada e, na do Schemathesis, o conteúdo e o
modo do arquivo passado em `--config-file`.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "contract-test.sh"
TOKEN = "gs_contract_CANARIO"  # noqa: S105 - canário do teste, não é chave
CANARY = "CANARIO"
FAKE_UV = f"""#!{sys.executable}
import json, os, stat, sys

args = sys.argv[1:]
with open(os.environ["CALLS"], "a", encoding="utf-8") as calls:
    calls.write(json.dumps(args) + "\\n")
if "create-user" in args:
    print("0b7f6c1e-2f4a-4d0b-9c1e-0a1b2c3d4e5f")
elif "issue-key" in args:
    print("{TOKEN}")
elif "schemathesis" in args and "--config-file" in args:
    path = args[args.index("--config-file") + 1]
    with open(path, encoding="utf-8") as config:
        content = config.read()
    mode = stat.S_IMODE(os.stat(path).st_mode)
    with open(os.environ["CONFIG_DUMP"], "w", encoding="utf-8") as dump:
        json.dump({{"path": path, "content": content, "mode": oct(mode)}}, dump)
"""
FAKE_CURL = "#!/bin/sh\nexit 0\n"


def _run_contract(tmp_path: Path) -> tuple[list[list[str]], dict[str, Any]]:
    # O script entra na raiz do próprio checkout: uma cópia isolada evita tocar no repositório.
    (tmp_path / "scripts").mkdir()
    shutil.copy(SCRIPT, tmp_path / "scripts" / "contract-test.sh")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in (("uv", FAKE_UV), ("curl", FAKE_CURL)):
        (bin_dir / name).write_text(body, encoding="utf-8")
        (bin_dir / name).chmod(0o755)
    temp = tmp_path / "tmp"
    temp.mkdir()
    calls, dump = tmp_path / "calls.jsonl", tmp_path / "config.json"
    result = subprocess.run(  # noqa: S603 - argumentos montados pelo teste, sem shell
        ["/bin/bash", str(tmp_path / "scripts" / "contract-test.sh")],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
        env={
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "HOME": str(tmp_path),
            "TMPDIR": str(temp),
            "CALLS": str(calls),
            "CONFIG_DUMP": str(dump),
        },
    )
    assert result.returncode == 0, result.stderr
    argv = [json.loads(line) for line in calls.read_text(encoding="utf-8").splitlines()]
    config: dict[str, Any] = json.loads(dump.read_text(encoding="utf-8")) if dump.exists() else {}
    return argv, config


def test_token_do_contrato_nao_aparece_em_argumento_de_processo(tmp_path: Path) -> None:
    argv, _ = _run_contract(tmp_path)

    assert any("schemathesis" in args for args in argv), "o Schemathesis não foi chamado"
    assert not [args for args in argv if any(CANARY in arg for arg in args)]


def test_schemathesis_recebe_o_token_por_arquivo_privado(tmp_path: Path) -> None:
    _, config = _run_contract(tmp_path)

    assert config, "o Schemathesis não recebeu --config-file"
    assert f'Authorization = "Bearer {TOKEN}"' in config["content"]
    assert config["mode"] == oct(0o600)


def test_arquivo_do_token_some_no_fim(tmp_path: Path) -> None:
    _, config = _run_contract(tmp_path)

    assert config, "o Schemathesis não recebeu --config-file"
    assert not Path(config["path"]).exists()
