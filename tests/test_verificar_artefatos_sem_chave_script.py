"""scripts/verificar_artefatos_sem_chave.py: o canário que barra o upload de artefato com chave.

O job do gVisor subiu trace.zip com a chave `gsk_...` dentro. O canário varre os diretórios que
iriam para o artefato, inclusive o conteúdo dos .zip, e reprova se achar o formato da chave. O log
do CI é público: a saída traz só o caminho e a contagem, nunca o trecho que bateu.
"""

import io
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "verificar_artefatos_sem_chave.py"
SCRIPT_TIMEOUT_S = 30
PUBLIC_ID = "0123456789abcdef"
SECRET = "Zq" * 20 + "_-x"
# Montada em pedaços: o arquivo de teste não carrega a chave inteira para o scanner de segredos.
CHAVE = "gsk" + "_" + PUBLIC_ID + "_" + SECRET
PEDACOS_DA_CHAVE = (CHAVE, PUBLIC_ID, SECRET, "gsk_0123")


def varrer(*caminhos: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - interpretador atual e script do repositório
        [sys.executable, str(SCRIPT), *map(str, caminhos)],
        capture_output=True,
        text=True,
        check=False,
        timeout=SCRIPT_TIMEOUT_S,
    )


def saida_inteira(resultado: subprocess.CompletedProcess[str]) -> str:
    return resultado.stdout + resultado.stderr


def zip_com(membros: dict[str, str | bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as arquivo:
        for nome, conteudo in membros.items():
            arquivo.writestr(nome, conteudo)
    return buffer.getvalue()


@pytest.fixture
def relatorio(tmp_path: Path) -> Path:
    pasta = tmp_path / "playwright-report"
    pasta.mkdir()
    (pasta / "index.html").write_text("<html>relatório sem segredo</html>")
    return pasta


def test_diretorio_limpo_passa(relatorio: Path) -> None:
    (relatorio / "trace.zip").write_bytes(zip_com({"0-trace.network": '{"headers": []}'}))

    resultado = varrer(relatorio)

    assert resultado.returncode == 0


def test_prefixo_sem_o_formato_completo_passa(relatorio: Path) -> None:
    (relatorio / "notas.txt").write_text("a chave começa com gsk_ e tem 64 caracteres; gsk_curta_x")

    resultado = varrer(relatorio)

    assert resultado.returncode == 0


def test_diretorio_inexistente_e_ignorado(tmp_path: Path) -> None:
    resultado = varrer(tmp_path / "nao-existe")

    assert resultado.returncode == 0


def test_chave_em_arquivo_de_texto_reprova_sem_imprimir_a_chave(relatorio: Path) -> None:
    arquivo = relatorio / "dados" / "erro.txt"
    arquivo.parent.mkdir()
    arquivo.write_text(f'{{"authorization": "Bearer {CHAVE}"}}')

    resultado = varrer(relatorio)

    saida = saida_inteira(resultado)
    assert resultado.returncode != 0
    assert f"{arquivo}: 1 ocorrência(s)" in saida
    for pedaco in PEDACOS_DA_CHAVE:
        assert pedaco not in saida


def test_chave_dentro_de_zip_reprova_com_o_caminho_do_zip_e_do_membro(relatorio: Path) -> None:
    trace = relatorio / "data" / "trace.zip"
    trace.parent.mkdir()
    trace.write_bytes(zip_com({"0-trace.network": f'"authorization":"{CHAVE}"'}))

    resultado = varrer(relatorio)

    saida = saida_inteira(resultado)
    assert resultado.returncode != 0
    assert str(trace) in saida
    assert "0-trace.network" in saida
    for pedaco in PEDACOS_DA_CHAVE:
        assert pedaco not in saida


def test_chave_em_zip_dentro_de_zip_reprova(relatorio: Path) -> None:
    interno = zip_com({"rede.json": CHAVE})
    externo = relatorio / "pacote.zip"
    externo.write_bytes(zip_com({"interno.zip": interno}))

    resultado = varrer(relatorio)

    saida = saida_inteira(resultado)
    assert resultado.returncode != 0
    assert str(externo) in saida
    for pedaco in PEDACOS_DA_CHAVE:
        assert pedaco not in saida


def test_conta_as_ocorrencias_por_arquivo(relatorio: Path) -> None:
    (relatorio / "tres.log").write_text(f"{CHAVE}\n{CHAVE}\n{CHAVE}\n")

    resultado = varrer(relatorio)

    assert resultado.returncode != 0
    assert f"{relatorio / 'tres.log'}: 3 ocorrência(s)" in saida_inteira(resultado)


def test_chave_na_fronteira_de_leitura_e_achada(relatorio: Path) -> None:
    # Maior que um bloco de leitura, com a chave cruzando a emenda.
    (relatorio / "grande.bin").write_bytes(b"x" * (1024 * 1024 - 10) + CHAVE.encode())

    resultado = varrer(relatorio)

    assert resultado.returncode != 0


def test_varre_todos_os_diretorios_recebidos(relatorio: Path, tmp_path: Path) -> None:
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "compose.log").write_text(CHAVE)

    resultado = varrer(relatorio, logs)

    assert resultado.returncode != 0
    assert str(logs / "compose.log") in saida_inteira(resultado)


def test_zip_corrompido_reprova_porque_nao_da_para_conferir(relatorio: Path) -> None:
    (relatorio / "trace.zip").write_bytes(b"PK\x03\x04isto nao e um zip")

    resultado = varrer(relatorio)

    assert resultado.returncode != 0


def test_sem_argumentos_e_erro_de_uso() -> None:
    resultado = varrer()

    assert resultado.returncode == 2
