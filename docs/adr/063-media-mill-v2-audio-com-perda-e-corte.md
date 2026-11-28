# ADR 063. Media Mill v2: LAME e Opus estáticos no FFmpeg do executor, e o AAC nativo

- **Estado:** aceito (2026-11-28, por decisão do dono: D8 = a, plano `paridade-image-forge-e-media-mill-v2`, Tarefa 12)
- **Plano:** [1.5](../GoblinShiftPlan.md#s1-5), [2.3](../GoblinShiftPlan.md#s2-3), [3.6](../GoblinShiftPlan.md#s3-6)
- **Relacionado:** [ADR 036](036-media-mill-ffmpeg-minimo-no-executor.md) (atualizado por este) e o `NOTICE`; #564

## Contexto

O Media Mill v1 extrai o áudio só em WAV e FLAC. A v2 (Tarefas 13 a 16 do mesmo plano) acrescenta MP3, AAC em `.m4a` e Opus, além do corte por trecho. O FFmpeg mínimo do ADR 036 não tem nenhum encoder com perda, nem os muxers desses formatos, nem o filtro `asetpts`, que zera o tempo da faixa depois do `atrim`.

O AAC tem encoder nativo no FFmpeg, LGPL. MP3 e Opus pedem bibliotecas externas: o LAME (LGPL 2 ou posterior) e a libopus (BSD-3). As duas são compatíveis com um binário LGPL e não exigem `--enable-gpl` nem `--enable-nonfree`. Vídeo continua fora: x264 e x265 são GPL.

## Decisão

**LAME e Opus compilados dos tarballs oficiais no estágio `ffmpeg` (D8 = a).** Mesmo modelo de confiança e de fixação do FFmpeg:

| Biblioteca | Versão | SHA-256 | Origem oficial | Segunda origem |
|---|---|---|---|---|
| LAME | 3.100 | `ddfe36ca…b1da1e` | SourceForge (`lame-3.100.tar.gz`) | Debian, `lame_3.100.orig.tar.gz`, com o mesmo hash |
| Opus | 1.6.1 (2026-01-15, a última estável) | `6ffcb593…2411a1`, igual ao `SHA256SUMS.txt` da Xiph | `downloads.xiph.org` (`opus-1.6.1.tar.gz`) | Debian, `opus_1.6.1.orig.tar.xz`: `001ac615…cd6ba1` |

- A segunda origem só entra quando o **download** da primeira falha. O SHA-256 é conferido depois, venha o arquivo de onde vier, e um hash que não confere derruba o build, sem tentar a outra origem.
- O Debian republica o Opus como `.tar.xz`. O conteúdo é idêntico ao da Xiph (conferido com `diff -r` ao fixar a versão), mas o hash é outro. Por isso o Opus tem dois `ARG` de SHA-256, e o arquivo baixado tem de conferir com um dos dois. A âncora pública do hash do Debian é o `Checksums-Sha256` do `.dsc` assinado do pacote: <https://deb.debian.org/debian/pool/main/o/opus/opus_1.6.1-1.dsc>.
- Os dois entram com `--disable-shared --enable-static --prefix=/opt/codecs`. O FFmpeg liga contra os `.a`, com `PKG_CONFIG_PATH=/opt/codecs/lib/pkgconfig`, `--pkg-config-flags=--static`, `--extra-cflags=-I/opt/codecs/include` e `--extra-ldflags=-L/opt/codecs/lib`. `/opt/codecs` fica no estágio de build.
- Do LAME, só a biblioteca de codificação: `--disable-frontend --disable-decoder --disable-analyzer-hooks`. O decoder de MP3 embutido (mpg123) nem é compilado. Do Opus, `--disable-doc --disable-extra-programs`, sem DRED, deep PLC nem OSCE (o padrão do `configure`).
- O texto das licenças vai para `/usr/local/share/doc/ffmpeg/lame/` e `/usr/local/share/doc/ffmpeg/opus/`, dentro da pasta que a imagem e o worker de desenvolvimento já copiam.

**Linha nova da tabela do ADR 036:**

| Componente | Habilitado |
|---|---|
| Bibliotecas externas | `libmp3lame`, `libopus` (estáticas) |
| Encoders | `mjpeg`, `pcm_s16le`, `flac`, `aac` (nativo), `libmp3lame`, `libopus` |
| Muxers | `wav`, `flac`, `image2`, `mp3`, `ipod` (AAC em `.m4a`), `opus`; o `configure` acrescenta `mov` e `ogg` por dependência (`ipod_muxer_select="mov_muxer"`, `opus_muxer_select="ogg_muxer"`) |
| Filtros | os do ADR 036 mais `asetpts` |

Protocolos, demuxers, decoders e parsers não mudam: os muxers novos são só de saída, e o `-f` de cada chamada continua fixo em `media_mill/commands.py`.

**O binário continua autocontido.** Sem dependência dinâmica nova, o `docker/worker-dev.Dockerfile` copia os mesmos dois arquivos e a mesma pasta de licenças (`tests/test_compose_dev_ffmpeg.py`).

## Alternativas descartadas

| Alternativa | Motivo |
|---|---|
| (b) `libmp3lame0` e `libopus0` do Debian, ligação dinâmica | O Trivy veria as bibliotecas, mas o worker de desenvolvimento (`FROM app`) teria de instalar pacotes como root, a versão flutuaria com o repositório e o binário deixaria de ser autocontido |
| (c) `.a` dos pacotes `-dev` do Debian | Tão invisível ao Trivy quanto (a), e sem versão fixada |
| `libfdk_aac` | Exige `--enable-nonfree`: o binário não poderia ser distribuído |
| Desligar os muxers `mov` e `ogg` | O `ipod` e o `opus` são o mesmo código; desligar a dependência desliga o muxer pedido |

## Consequências

- **Trivy:** como o FFmpeg, o LAME e o Opus não aparecem no scanner, porque estão dentro do binário. As CVEs se acompanham pelas versões dos `ARG LAME_VERSION` e `ARG OPUS_VERSION`. O LAME 3.100 é de 2017 e é a última versão publicada. Frontend e decoder ficam fora do build, o que tira do binário a parte do LAME que lê arquivos.
- **Tamanho:** a estimativa é de cerca de +1 MiB na imagem (os `.a` têm 0,35 MiB do LAME e 1,9 MiB do Opus antes de ligar; o binário só leva os objetos usados), e o orçamento é de **+3 MiB**. Medido no CI do PR #574 (run 46115701212, commit 14feb56d): 193.164.505 bytes, contra 190.178.375 da base publicada `sha-c5966af94aaf`, ou seja, **+2.986.130 bytes (≈ +2,85 MiB)**, dentro do orçamento. O passo "Tamanho da imagem do executor (amd64)" do job `gvisor` imprime no log e no resumo do run o tamanho absoluto e a diferença para a imagem da base. Acima do orçamento, a decisão volta ao dono.
- **Build:** sem cache, cerca de 1 min a mais (LAME e Opus antes do FFmpeg). Cada `ARG` fica logo antes do `RUN` que o usa. Subir o Opus refaz o Opus e o FFmpeg; subir o FFmpeg não refaz o apt, o LAME nem o Opus. Com o cache quente e nada mudado, o build não muda.
- **Licenças:** o `NOTICE` cita o LAME (LGPL 2 ou posterior) e o Opus (BSD-3). O binário continua LGPL 2.1 ou posterior.

## Como verificar

- `uv run pytest tests/test_executor_ffmpeg_configure.py tests/test_compose_dev_ffmpeg.py`: o `configure` sem `--enable-gpl`, `--enable-nonfree` nem `--enable-version3`; LAME e Opus fixados por versão e SHA-256 e estáticos; todo download conferido pelo SHA-256 depois de todas as origens; a lista exata de encoders e muxers; o worker de desenvolvimento copiando os mesmos arquivos. Rodam em qualquer lugar.
- `uv run pytest -m executor_image -k ffmpeg` (job `gvisor` da CI): os encoders e muxers do binário, `ffmpeg -L` com a LGPL, o `-buildconf` sem `--enable-gpl` e o `ldd` sem `mp3lame`, `opus` nem `not found`.
