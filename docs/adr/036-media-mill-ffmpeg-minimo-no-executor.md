# ADR 036. Media Mill: FFmpeg mínimo compilado na imagem do executor, allowlist antes do FFmpeg e tetos de tempo por chamada

- **Estado:** aceito na implementação do Media Mill (2026-10-15); pendente de revisão do usuário no PR e da medição do pior caso na VPS
- **Plano:** [1.5](../GoblinShiftPlan.md#s1-5), [1.6](../GoblinShiftPlan.md#s1-6), [1.7](../GoblinShiftPlan.md#s1-7), [2.3](../GoblinShiftPlan.md#s2-3), [3.6](../GoblinShiftPlan.md#s3-6)
- **Relacionado:** [ADR 004](004-container-gvisor-por-tentativa.md), [ADR 005](005-area-do-executor-em-filesystem-fixo.md), [ADR 018](018-pillow-na-imagem-do-executor.md), [ADR 019](019-layout-de-out-pacote-e-papeis.md)

## Contexto

O Media Mill (`media-mill` v1) extrai uma faixa de áudio e gera miniaturas de até 5 vídeos MP4/MOV (H.264 + AAC/PCM) ou WebM (VP8/VP9 + Opus/Vorbis), com até 300 s, 1920 × 1080 e 60 fps. Decodificar vídeo é a maior superfície de ataque do serviço: o conteúdo é do usuário e o FFmpeg tem demuxers que abrem outros arquivos por conta própria (HLS, `concat`, `.ffconcat`, `image2` com padrão, referências de dados do MOV). O plano (2.3) exige que o FFmpeg nunca abra outro arquivo além da entrada, nem por conteúdo disfarçado, que não haja flags livres e que tempo, memória e threads tenham teto sob gVisor.

A imagem do executor era biblioteca padrão + Pillow (ADR 018). O FFmpeg do Debian (`apt install ffmpeg`) traz centenas de MiB de bibliotecas (X11, SDL, codecs GPL) e todos os demuxers; os builds estáticos de terceiros também trazem tudo, e a confiança passaria a um mantenedor externo.

## Decisão

**FFmpeg mínimo compilado num estágio do Dockerfile.** O FFmpeg 8.1.2 é compilado do tarball oficial, conferido pelo SHA-256 `464beb5e…b524c` fixado no `ARG`. Ao fixar a versão, o tarball foi verificado com a assinatura GPG da chave de releases do FFmpeg (`FCF9 86EA 15E6 E293 A564 4F10 B432 2F04 D676 58D8`). O estágio usa a mesma base da imagem final, fixada por digest, para o binário ligar contra a mesma glibc. O `configure` parte de `--disable-everything --disable-autodetect --disable-network` e habilita só:

| Componente | Habilitado |
|---|---|
| Protocolos | `file`, `pipe` |
| Demuxers | `mov`, `matroska` |
| Decoders | `h264`, `vp8`, `vp9`, `aac`, `opus`, `vorbis`, `pcm_s16le/be`, `pcm_s24le/be` |
| Encoders | `mjpeg`, `pcm_s16le`, `flac` |
| Muxers | `wav`, `flac`, `image2` (só saída, com `-update 1`, sem padrão) |
| Filtros | `scale`, `format`, `aformat`, `aresample`, `showinfo`, `transpose`, `hflip`, `vflip`, `null`, `anull`, `trim`, `atrim` |

`hls`, `concat`, `image2` como entrada, `lavfi`, `http` e afins nem existem no binário. Sem `--enable-gpl` nem bibliotecas externas: LGPL 2.1 ou posterior, com o texto da licença copiado para `/usr/local/share/doc/ffmpeg/` na imagem e citado no `NOTICE`. A versão final do executor continua fixada por digest na allowlist do `executor-ctl` (ADR 021).

**Assinatura antes do FFmpeg, demuxer forçado e flags fixas.** A receita lê os primeiros 64 bytes: caixa ISO BMFF (`ftyp`, `moov`, `mdat`, `wide`, `free`, `skip`) é MOV; EBML com DocType `webm` é WebM; o resto (playlist HLS renomeada para `.mp4`, lista do `concat`, Matroska que não é WebM) é recusado como `UNSUPPORTED_FORMAT` sem nenhuma chamada ao FFmpeg. Toda chamada usa `-nostdin`, `-protocol_whitelist file`, `-f mov` ou `-f matroska` pela assinatura, `-enable_drefs 0` no MOV, entrada `file:<caminho absoluto fixado pelo executor>`, `-threads 1` e `-filter_threads 1`, `-map_metadata -1 -map_chapters -1` (o local de gravação do celular não chega às saídas) e `+bitexact` (mesma entrada, mesmos bytes). Os parâmetros do usuário são enums e inteiros validados; nenhum texto dele entra numa linha de comando. As flags continuam valendo por cima do binário mínimo, que é a segunda barreira.

**Allowlist pelo ffprobe, na mesma fronteira de isolamento.** O nome do demuxer relatado precisa ser o esperado; toda faixa de vídeo e de áudio precisa estar na allowlist da família (faixas de dados, legendas e timecode são ignoradas, porque nunca são mapeadas); pixel quadrado; rotação de 0/90/180/270; até 16 faixas. Limites de 1.5: lado maior até 1920 e lado menor até 1080 (um retrato 1080 × 1920 gravado sem rotação cabe), 60 fps, 300 s pelo contêiner e pela faixa de vídeo.

**Áudio.** `audio_stream_index` conta só as faixas de áudio (o `N` de `0:a:N`), de 0 a 15; ausente, vale a 0, e o relatório registra o índice efetivo. A faixa escolhida precisa ter até 48 kHz e 2 canais, senão `AUDIO_LIMIT_EXCEEDED`; a saída usa `-ar` e `-ac` iguais aos da origem (sem resample nem downmix) e 16 bits (WAV `pcm_s16le` ou FLAC `s16`), a conversão declarada de 1.5. Vídeo sem a faixa pedida: `AUDIO_STREAM_NOT_FOUND`; sem áudio e só com miniaturas, aceito.

**Miniaturas.** 10%, ou 10/50/90%, da duração da faixa de vídeo (do contêiner quando a faixa não declara, como no Matroska). `-ss` antes do `-i` faz a busca precisa: o FFmpeg decodifica a partir do keyframe anterior e descarta os quadros antes do alvo; o filtro `showinfo` informa o instante do quadro entregue. Sem quadro em ou depois do alvo, uma segunda chamada lê do início até o alvo e guarda o último quadro, com o aviso `THUMBNAIL_BEFORE_TARGET`. Instante pedido e efetivo vão para `details` em ms. Caixa de 640 px sem ampliar, com a rotação aplicada pelo autorotate do FFmpeg.

**Dano é recusa.** O stderr de cada chamada vai para arquivo com `-loglevel level+…` e é lido linha a linha: qualquer `[error]`, `[fatal]` ou `[panic]` recusa o vídeo como `UNSUPPORTED_FORMAT`, mesmo que o FFmpeg tenha entregado um quadro remendado ou um áudio mais curto (um MP4 truncado com `moov` no início só mostra o dano ao decodificar). Tudo o que um vídeo produz fica no rascunho até o fim; só então vai para `out/`, para um vídeo recusado no meio não deixar saída que o relatório não cita.

**Execução dos processos.** Binário por caminho absoluto, ambiente vazio (`FFREPORT` e afins não chegam ao filho), `stdin` em `/dev/null`, `stdout` e `stderr` em arquivos do rascunho, `RLIMIT_FSIZE` de 256 MiB e, no Linux, `RLIMIT_AS` de 1 GiB no filho (o macOS recusa baixar o `RLIMIT_AS`; o executor só roda em Linux, e o Mac fica nos testes locais). O rascunho é `work/tmp`, criado vazio pelo executor e apontado por `tempfile.tempdir` enquanto a receita roda, porque a raiz do container é somente leitura; ele divide o filesystem de 6 GiB da tentativa (ADR 005).

**Tempo.** Cada chamada tem prazo igual ao menor entre o seu teto (ffprobe 20 s, áudio 60 s, miniatura 60 s) e o que sobra do orçamento de 8 min da execução (a tentativa tem 10 min; o resto é relatório, manifesto e ZIP). Passar do teto da chamada recusa só aquele vídeo como `MEDIA_TOO_LARGE`; esgotar o orçamento falha a execução com `RESOURCE_LIMIT_EXCEEDED`.

**Saídas, códigos e entradas maiores.**
- Grupo novo `audio/` em `OutputGroup` (a lista fechada do ADR 019, que a coleta do worker também impõe); miniaturas em `thumbnails/<radical>-10pct.jpg`. `.flac` vai para o ZIP com `ZIP_STORED`, como JPEG; o WAV continua com DEFLATE.
- Códigos de rejeição novos no catálogo de 1.6: `MEDIA_TOO_LARGE`, `AUDIO_LIMIT_EXCEEDED`, `AUDIO_STREAM_NOT_FOUND`; `UNSUPPORTED_FORMAT` cobre allowlist, perfil e dano. `RESOURCE_LIMIT_EXCEEDED` passa a nascer também no executor.
- 1.7 ("vídeo, quando disponível, até 256 MiB"): o envio aceita até 256 MiB para nomes terminados em `.mp4`, `.mov` ou `.webm`. A extensão é só indício: **toda** versão de receita publica `max_input_bytes` (32 MiB nas três v1 existentes, 256 MiB no Media Mill), e a aceitação recusa com `422` qualquer entrada acima do teto da receita escolhida, pelo tamanho guardado, nunca pelo nome. O conteúdo de verdade é conferido no executor (assinatura, ffprobe). O lote do Media Mill segue o teto comum de 512 MiB. `/me` publica `video_file_bytes`.
- Contrato do import-linter "Só o executor roda o FFmpeg": API, worker e manutenção importam parâmetros e limites (pelo catálogo), nunca `media_mill.tools` nem `media_mill.recipe`.

## Alternativas descartadas

| Alternativa | Motivo |
|---|---|
| `ffmpeg` do Debian na imagem | Todos os demuxers (inclusive HLS e `concat`), bibliotecas de X11/SDL e componentes GPL; a imagem cresceria centenas de MiB e a única proteção contra demuxers perigosos seriam as flags |
| Build estático de terceiros fixado por digest (`COPY --from=<imagem>@sha256:…`) | Rápido de construir, mas traz o FFmpeg completo e passa a confiança da cadeia de build a um mantenedor externo |
| Imagem própria só do FFmpeg publicada no GHCR e copiada por digest | Compilaria uma vez só, mas o digest só existe depois de a CI publicá-la: a primeira versão do PR referenciaria uma imagem inexistente, e publicar do laptop fere a regra de deploy só pela CI. Fica como evolução se o tempo de build incomodar |
| Uma passada sequencial por vídeo para as três miniaturas | Melhor só quando há um keyframe para o vídeo inteiro; com GOP normal (1 a 10 s), três buscas precisas decodificam dezenas ou centenas de quadros em vez de milhares |
| Teto de tempo que falha a execução inteira em qualquer chamada lenta | Um vídeo com GOP enorme derrubaria os outros quatro do lote; o teto por chamada recusa só ele |
| `audio_stream_index` como índice global do stream | O usuário vê "faixas de áudio"; o índice global mistura vídeo, dados e legendas e mudaria entre arquivos |

## Consequências

- Construir a imagem do executor compila o FFmpeg: cerca de 2,5 min no Mac sem cache (5,5 min com o `apt` do estágio), mais na CI. Por isso os testes `executor_image` saíram do job "Verificações" (commit stage de 5 min) e rodam só no job `gvisor`, sob `runsc`, que depende dele; o job de publicação usa o cache do GitHub Actions.
- O estágio baixa `gcc`, `make`, `pkg-config` e `nasm` (amd64) do Debian no build: o binário final é reproduzível pela versão e pela base, não bit a bit entre builds em datas diferentes; o digest publicado pela CI é o que vale.
- No Compose local, o Runner local roda a receita no container do worker, e não na imagem do executor. O worker de desenvolvimento (`docker/worker-dev.Dockerfile`) copia o `ffmpeg` e o `ffprobe` do mesmo estágio `ffmpeg` deste Dockerfile, com as mesmas instruções `COPY` da imagem do executor (conferidas por `tests/test_compose_dev_ffmpeg.py`); os binários saem idênticos aos do executor pelo SHA-256. Instalar o FFmpeg do sistema no worker faria o Media Mill local aceitar demuxers e protocolos que a produção não tem. A imagem de produção da aplicação não muda.
- Os testes da receita usam o FFmpeg completo do ambiente (Homebrew no Mac, `apt` na CI) para gerar os vídeos sintéticos e para ler; a imagem roda o build mínimo sobre os mesmos tipos de arquivo e prova que ele basta.
- Atualizar o FFmpeg: mudar `FFMPEG_VERSION` e `FFMPEG_SHA256` (verificando a assinatura GPG), `FFMPEG_VERSION` em `tests/test_executor_image.py` e publicar uma imagem nova; Runs já aceitas seguem na imagem fixada nelas.
- GOP muito longo vira recusa: com keyframe a cada 10 s (600 quadros a 60 fps), cada miniatura cabe no teto; um vídeo de 300 s com um keyframe só decodifica até 16.200 quadros para a miniatura de 90% e é recusado como `MEDIA_TOO_LARGE`. Referência local (Mac, Docker com `--cpus=1`, runc, build mínimo): cerca de 100 quadros 1080p60 por segundo.
- Pior caso: 5 vídeos de 300 s em 1080p60 com keyframe a cada 10 s, FLAC e 3 miniaturas (`scripts/media-mill-worst-case.sh`, cerca de 77 MiB cada). Referência local: 95 s, os 5 vídeos aceitos e 20 saídas (Mac M-series, Docker com `--cpus=1 --memory=2g`, runc, sem gVisor, 2026-10-15). A medição que vale, sob gVisor e 1 vCPU, é feita na VPS antes do merge com `sudo python3 -B -m acceptance.media_mill_load <dir>`; se não couber nos 10 min, os tetos ou o `max_inputs` da v1 caem antes de a versão chegar à `main`, e o número medido é registrado aqui.

## Como verificar

- `uv run pytest tests/media_mill`: parâmetros, assinatura, ffprobe, linhas de comando, execução dos processos e a receita de ponta a ponta com vídeos gerados na hora, inclusive os hostis (HLS disfarçado, `concat`, codec fora da allowlist, 4K, 120 fps, 6 canais, 96 kHz, 301 s, pixel não quadrado, truncado, danificado no meio).
- `uv run pytest tests/api/test_runs.py -k 40_mib`: um `x.mp4` de 40 MiB só entra numa Run do Media Mill.
- `uv run lint-imports`: "Só o executor roda o FFmpeg" e as receitas independentes.
- `uv run pytest -m executor_image` (também sob `runsc` no job `gvisor`): versão 8.1.2, só os demuxers `mov` e `matroska`, só os protocolos `file` e `pipe`, `hls`/`concat`/`image2`/`lavfi` desconhecidos, e o Media Mill na imagem com o mesmo ZIP em duas execuções.
