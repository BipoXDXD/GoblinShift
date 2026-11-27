"""scripts/ci-apt-instalar.sh: o apt da CI não fica preso num espelho lento.

`sudo`, `timeout` e `apt-get` falsos no PATH registram as chamadas; o `apt-get` falso "trava"
(sai com 124, como o `timeout` faz ao matar) quando o espelho configurado está na lista de lentos.
"""

import os
import re
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "ci-apt-instalar.sh"
SCRIPT_TIMEOUT_S = 30
TENTATIVA_S = 7
UBUNTU_SOURCES = "Types: deb\nURIs: mirror+file:/etc/apt/apt-mirrors.txt\nSuites: noble\n"
MIRRORS = (
    "http://azure.archive.ubuntu.com/ubuntu/\tpriority:1\n"
    "https://archive.ubuntu.com/ubuntu/\tpriority:2\n"
    "https://security.ubuntu.com/ubuntu/\tpriority:3\n"
)

FAKE_SUDO = '#!/usr/bin/env bash\nexec "$@"\n'
FAKE_TIMEOUT = """#!/usr/bin/env bash
[[ $1 == -k ]] && shift 2
echo "timeout $1" >> "$FAKE_LOG"
shift
exec "$@"
"""
FAKE_APT = r"""#!/usr/bin/env bash
echo "apt-get $*" >> "$FAKE_LOG"
archives=$(printf '%s\n' "$@" | sed -n 's/^Dir::Cache::archives=//p')
case " $* " in
  *" --no-download "*.deb*)
    [[ ${FAKE_CACHE_RUIM:-} == 1 ]] && exit 100
    exit 0 ;;
  *" update "*)
    fase=update ;;
  *" --download-only "*)
    fase=download ;;
  *)
    exit 0 ;;
esac

sourcelist=$(printf '%s\n' "$@" | sed -n 's/^Dir::Etc::sourcelist=//p' | tail -n 1)
sourceparts=$(printf '%s\n' "$@" | sed -n 's/^Dir::Etc::sourceparts=//p' | tail -n 1)
[[ -z $sourcelist ]] && sourcelist="$CI_APT_ETC/sources.list"
[[ -z $sourceparts ]] && sourceparts="$CI_APT_ETC/sources.list.d"

fontes_lidas=()
if [[ $sourcelist != - && $sourcelist != /dev/null && -e $sourcelist ]]; then
  # Em versões mais antigas do apt, Dir::Etc::sourcelist não aceita DEB822 (.sources).
  [[ $sourcelist == *.sources ]] && exit 100
  fontes_lidas+=("$sourcelist")
fi
if [[ $sourceparts != - && -d $sourceparts ]]; then
  shopt -s nullglob
  for arquivo in "$sourceparts"/*.list "$sourceparts"/*.sources; do
    fontes_lidas+=("$arquivo")
  done
  shopt -u nullglob
fi
((${#fontes_lidas[@]} == 0)) && exit 100

if [[ -n ${FAKE_FONTE_QUEBRADA:-} ]] && grep -qF "$FAKE_FONTE_QUEBRADA" "${fontes_lidas[@]}"; then
  echo "E: Failed to fetch $FAKE_FONTE_QUEBRADA 403 Forbidden" >&2
  exit 100
fi

ubuntu_sources=""
for arquivo in "${fontes_lidas[@]}"; do
  if [[ $(basename "$arquivo") == "ubuntu.sources" ]]; then
    ubuntu_sources="$arquivo"
    break
  fi
done

if [[ -n $ubuntu_sources ]]; then
  uris=$(awk '$1 == "URIs:" {print $2; exit}' "$ubuntu_sources")
  if [[ $uris == mirror+file:/etc/apt/* ]]; then
    lista="$CI_APT_ETC/${uris#mirror+file:/etc/apt/}"
    espelho=$(grep -o '//[^/]*/ubuntu' "$lista" | head -n 1)
    printf '%s %s\n' "$fase" "$(paste -sd '|' "$lista")" >> "$FAKE_FONTES"
  elif [[ -n $uris ]]; then
    espelho=$(printf '%s\n' "$uris" | grep -o '//[^/]*/ubuntu' | head -n 1)
    printf '%s %s\n' "$fase" "$uris" >> "$FAKE_FONTES"
  else
    exit 100
  fi
elif [[ -f ${fontes_lidas[0]} ]]; then
  espelho=$(grep -o '//[^/]*/ubuntu' "${fontes_lidas[0]}" | head -n 1)
  [[ -z $espelho ]] && exit 100
  printf '%s %s\n' "$fase" "$(paste -sd '|' "${fontes_lidas[0]}")" >> "$FAKE_FONTES"
else
  exit 100
fi

lentos=${FAKE_LENTOS:-}
if [[ $fase == download ]]; then
  lentos="$lentos ${FAKE_DOWNLOAD_LENTOS:-}"
fi
for lento in $lentos; do
  [[ $espelho == "//$lento/ubuntu" ]] && exit 124
done
if [[ $fase == download ]]; then
  mkdir -p "$archives/partial"
  touch "$archives/ffmpeg_7.deb" "$archives/lock"
fi
exit 0
"""


def write_executable(path: Path, content: str) -> None:
    path.write_text(content)
    path.chmod(0o755)


@pytest.fixture
def ambiente(tmp_path: Path) -> dict[str, Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    write_executable(bin_dir / "sudo", FAKE_SUDO)
    write_executable(bin_dir / "timeout", FAKE_TIMEOUT)
    write_executable(bin_dir / "apt-get", FAKE_APT)
    etc = tmp_path / "etc-apt"
    (etc / "sources.list.d").mkdir(parents=True)
    (etc / "apt.conf.d").mkdir()
    (etc / "sources.list.d" / "ubuntu.sources").write_text(UBUNTU_SOURCES)
    (etc / "apt-mirrors.txt").write_text(MIRRORS)
    return {
        "bin": bin_dir,
        "etc": etc,
        "debs": tmp_path / "debs",
        "log": tmp_path / "chamadas",
        "fontes": tmp_path / "fontes",
    }


def instalar(
    ambiente: dict[str, Path], *pacotes: str, **fake_env: str
) -> subprocess.CompletedProcess[str]:
    env = {
        "PATH": f"{ambiente['bin']}{os.pathsep}{os.environ['PATH']}",
        "CI_APT_ETC": str(ambiente["etc"]),
        "CI_APT_TENTATIVA_S": str(TENTATIVA_S),
        "FAKE_LOG": str(ambiente["log"]),
        "FAKE_FONTES": str(ambiente["fontes"]),
        **fake_env,
    }
    return subprocess.run(  # noqa: S603 - script do repositório
        ["bash", str(SCRIPT), str(ambiente["debs"]), *pacotes],  # noqa: S607 - bash do PATH
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=SCRIPT_TIMEOUT_S,
        cwd=ROOT,
    )


def chamadas(ambiente: dict[str, Path]) -> list[str]:
    log = ambiente["log"]
    return log.read_text().splitlines() if log.exists() else []


def hosts_dos_espelhos(ambiente: dict[str, Path]) -> list[str]:
    """Host de cada URI do apt-mirrors.txt, na ordem do arquivo."""
    linhas = (ambiente["etc"] / "apt-mirrors.txt").read_text().splitlines()
    return [urlsplit(linha.split("\t")[0]).hostname or "" for linha in linhas]


def espelho_atual(ambiente: dict[str, Path]) -> str:
    first = (ambiente["etc"] / "apt-mirrors.txt").read_text().splitlines()[0]
    match = re.search(r"//([^/]+)/ubuntu", first)
    assert match is not None
    return match.group(1)


def test_sem_pacotes_falha_com_uso(ambiente: dict[str, Path]) -> None:
    result = instalar(ambiente)

    assert result.returncode == 2
    assert "uso:" in result.stderr
    assert chamadas(ambiente) == []


def test_espelho_bom_baixa_e_instala_do_archive_ubuntu(ambiente: dict[str, Path]) -> None:
    result = instalar(ambiente, "age", "ffmpeg")

    assert result.returncode == 0, result.stderr
    apt = [c for c in chamadas(ambiente) if c.startswith("apt-get")]
    assert len(apt) == 3
    assert " update" in apt[0]
    assert "--download-only" in apt[1]
    assert apt[1].endswith("age ffmpeg")
    assert "--no-download" in apt[2]
    assert apt[2].endswith("age ffmpeg")
    assert all("--no-install-recommends" in c for c in apt[1:])
    assert espelho_atual(ambiente) == "archive.ubuntu.com"
    assert any(host == "security.ubuntu.com" for host in hosts_dos_espelhos(ambiente))


def test_configura_o_apt_sem_traducoes_e_com_timeout_de_rede(ambiente: dict[str, Path]) -> None:
    instalar(ambiente, "age")

    conf = "".join(p.read_text() for p in (ambiente["etc"] / "apt.conf.d").iterdir())
    assert 'Acquire::Languages "none";' in conf
    assert "Acquire::http::Timeout" in conf
    assert "Acquire::https::Timeout" in conf


def test_cada_comando_de_rede_tem_teto_da_tentativa(ambiente: dict[str, Path]) -> None:
    instalar(ambiente, "age")

    tetos = [int(c.split()[1]) for c in chamadas(ambiente) if c.startswith("timeout")]
    assert len(tetos) == 2
    assert all(0 < teto <= TENTATIVA_S for teto in tetos)


def test_espelho_lento_passa_para_o_proximo(ambiente: dict[str, Path]) -> None:
    result = instalar(ambiente, "ffmpeg", FAKE_LENTOS="archive.ubuntu.com")

    assert result.returncode == 0, result.stderr
    assert espelho_atual(ambiente) == "mirrors.edge.kernel.org"
    assert (
        f"::warning::apt: archive.ubuntu.com não entregou os pacotes em {TENTATIVA_S} s; "
        "tentando o próximo espelho"
    ) in result.stdout.splitlines()
    assert "::warning::" in result.stdout
    assert chamadas(ambiente)[-1].startswith("apt-get")
    assert "--no-download" in chamadas(ambiente)[-1]


def test_download_lento_tambem_passa_para_o_proximo(ambiente: dict[str, Path]) -> None:
    result = instalar(ambiente, "ffmpeg", FAKE_LENTOS="archive.ubuntu.com mirrors.edge.kernel.org")

    assert result.returncode == 0, result.stderr
    assert espelho_atual(ambiente) == "azure.archive.ubuntu.com"


def test_todos_os_espelhos_lentos_falha_depois_de_tres_tentativas(
    ambiente: dict[str, Path],
) -> None:
    lentos = "archive.ubuntu.com mirrors.edge.kernel.org azure.archive.ubuntu.com"

    result = instalar(ambiente, "ffmpeg", FAKE_LENTOS=lentos)

    assert result.returncode == 1
    assert "::error::" in result.stdout
    updates = [c for c in chamadas(ambiente) if c.startswith("apt-get") and " update" in c]
    assert len(updates) == 3
    assert not any("--no-download" in c for c in chamadas(ambiente))


@pytest.mark.parametrize(("protocolo", "prefixo"), [("http", ""), ("https", " \t")])
@pytest.mark.parametrize(
    ("lentos", "saida"),
    [
        ("archive.ubuntu.com mirrors.edge.kernel.org", 0),
        ("archive.ubuntu.com mirrors.edge.kernel.org azure.archive.ubuntu.com", 1),
    ],
    ids=["fallback-conclui", "todos-os-downloads-expiram"],
)
def test_mirror_file_regional_troca_o_host_efetivo_apos_timeout_no_download(
    ambiente: dict[str, Path], lentos: str, saida: int, protocolo: str, prefixo: str
) -> None:
    sources = ambiente["etc"] / "sources.list.d" / "ubuntu.sources"
    deb822 = UBUNTU_SOURCES + (
        "Components: main universe\nSigned-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg\n"
    )
    sources.write_text(deb822)
    regional = f"{prefixo}{protocolo}://us-east-2.ec2.archive.ubuntu.com/ubuntu/\tpriority:1"
    security = "http://security.ubuntu.com/ubuntu/\tpriority:3"
    (ambiente["etc"] / "apt-mirrors.txt").write_text(f"{regional}\n{security}\n")
    # O endpoint regional também fica lento se o script deixar de trocar sua URI.
    result = instalar(
        ambiente, "ffmpeg", FAKE_DOWNLOAD_LENTOS=f"us-east-2.ec2.archive.ubuntu.com {lentos}"
    )

    assert ambiente["fontes"].read_text().splitlines() == [
        f"{fase} {prefixo}{protocolo}://{host}/ubuntu/\tpriority:1|{security}"
        for host in ("archive.ubuntu.com", "mirrors.edge.kernel.org", "azure.archive.ubuntu.com")
        for fase in ("update", "download")
    ]
    assert sources.read_text() == deb822
    assert result.returncode == saida, result.stdout + result.stderr
    installs = [c for c in chamadas(ambiente) if c.startswith("apt-get") and "--no-download" in c]
    assert len(installs) == (1 if saida == 0 else 0)


@pytest.mark.parametrize(
    "uri_alheia",
    [
        "https://packages.example.org/proxy//us-east-2.ec2.archive.ubuntu.com/ubuntu/",
        "file:/srv/cache//us-east-2.ec2.archive.ubuntu.com/ubuntu/",
    ],
    ids=["proxy-http", "caminho-file"],
)
def test_host_ubuntu_em_caminho_de_outra_fonte_fica_intacto(
    ambiente: dict[str, Path], uri_alheia: str
) -> None:
    extra = ambiente["etc"] / "sources.list.d" / "extra.list"
    extra.write_text(f"deb {uri_alheia} noble main\n")
    lista = ambiente["etc"] / "apt-mirrors.txt"
    regional = "https://us-east-2.ec2.archive.ubuntu.com/ubuntu/\tpriority:1"
    fonte_alheia = f"{uri_alheia}\tpriority:2"
    lista.write_text(f"{regional}\n{fonte_alheia}\n")

    result = instalar(ambiente, "ffmpeg")

    assert result.returncode == 0, result.stdout + result.stderr
    fontes_esperadas = f"https://archive.ubuntu.com/ubuntu/\tpriority:1|{fonte_alheia}"
    assert ambiente["fontes"].read_text().splitlines() == [
        f"update {fontes_esperadas}",
        f"download {fontes_esperadas}",
    ]
    assert extra.read_text() == f"deb {uri_alheia} noble main\n"


def test_debs_do_cache_instalam_sem_rede(ambiente: dict[str, Path]) -> None:
    ambiente["debs"].mkdir()
    (ambiente["debs"] / "ffmpeg_7.deb").touch()

    result = instalar(ambiente, "ffmpeg")

    assert result.returncode == 0, result.stderr
    apt = [c for c in chamadas(ambiente) if c.startswith("apt-get")]
    assert len(apt) == 1
    assert "--no-download" in apt[0]
    assert apt[0].endswith(f"{ambiente['debs']}/ffmpeg_7.deb")
    assert not any(c.startswith("timeout") for c in chamadas(ambiente))


def test_cache_que_nao_instala_cai_para_a_rede(ambiente: dict[str, Path]) -> None:
    ambiente["debs"].mkdir()
    (ambiente["debs"] / "ffmpeg_6.deb").touch()

    result = instalar(ambiente, "ffmpeg", FAKE_CACHE_RUIM="1")

    assert result.returncode == 0, result.stderr
    assert any(" update" in c for c in chamadas(ambiente))
    assert chamadas(ambiente)[-1].endswith("--no-download ffmpeg")


def test_diretorio_fica_so_com_os_debs_para_o_cache(ambiente: dict[str, Path]) -> None:
    result = instalar(ambiente, "ffmpeg")

    assert result.returncode == 0, result.stderr
    assert sorted(p.name for p in ambiente["debs"].iterdir()) == ["ffmpeg_7.deb"]


def test_ignora_fontes_de_terceiros_quebradas_no_ubuntu_24_04(
    ambiente: dict[str, Path],
) -> None:
    sources_list = ambiente["etc"] / "sources.list"
    sources_list.write_text("# Ubuntu sources have moved to ubuntu.sources\n")
    ms_list = ambiente["etc"] / "sources.list.d" / "microsoft-prod.list"
    ms_list.write_text("deb https://packages.microsoft.com/ubuntu/24.04/prod noble main\n")
    ms_sources = ambiente["etc"] / "sources.list.d" / "microsoft-prod.sources"
    ms_sources.write_text(
        "Types: deb\nURIs: https://packages.microsoft.com/ubuntu/24.04/prod\nSuites: noble\n"
    )

    result = instalar(ambiente, "ffmpeg", FAKE_FONTE_QUEBRADA="packages.microsoft.com")

    assert result.returncode == 0, result.stdout + result.stderr
    updates = [c for c in chamadas(ambiente) if c.startswith("apt-get") and " update" in c]
    assert len(updates) == 1
    assert "Dir::Etc::sourceparts=" in updates[0]
    assert "Dir::Etc::sourcelist=-" in updates[0]
    assert ms_list.exists()
    assert ms_sources.exists()


def test_layout_tradicional_com_sources_list_ignora_sources_list_d(
    ambiente: dict[str, Path],
) -> None:
    (ambiente["etc"] / "sources.list.d" / "ubuntu.sources").unlink()
    sources_list = ambiente["etc"] / "sources.list"
    sources_list.write_text("deb https://azure.archive.ubuntu.com/ubuntu noble main\n")
    ms_list = ambiente["etc"] / "sources.list.d" / "microsoft-prod.list"
    ms_list.write_text("deb https://packages.microsoft.com/ubuntu/24.04/prod noble main\n")

    result = instalar(ambiente, "ffmpeg", FAKE_FONTE_QUEBRADA="packages.microsoft.com")

    assert result.returncode == 0, result.stdout + result.stderr
    assert sources_list.read_text() == "deb https://archive.ubuntu.com/ubuntu noble main\n"
    assert ms_list.exists()
