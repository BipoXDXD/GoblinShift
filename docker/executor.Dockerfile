# Imagem do executor: roda uma tentativa dentro do container gVisor (docs/GoblinShiftPlan.md, 3.6).
# Só a biblioteca padrão, o Pillow fixado (ADR 018), o pikepdf fixado com o qpdf da própria wheel
# e suas dependências lxml e packaging (ADR 035), o FFmpeg mínimo do Media Mill (ADR 036) e o
# pacote goblinshift.processing; nada de banco, web nem outras dependências de terceiros, para que
# o conteúdo do usuário encontre a menor superfície possível. O executor-ctl só aceita esta imagem
# pelo digest que a release do Coolify grava na allowlist (deploy/coolify/release/receiver.py).
#
# Bases fixadas pelo digest do índice multiplataforma (amd64 na VPS e na CI, arm64 no Mac).
FROM ghcr.io/astral-sh/uv:0.12.15@sha256:62f8c047d0a0e9ece6b53fc63df902585a67a47a7f318ddec4a37db586edc8e3 AS uv

# Estágio de build: instala o grupo `executor` do uv.lock (Pillow e pikepdf) com os hashes do lock e só
# a partir de wheels. Nem o uv nem o requirements gerado chegam à imagem final.
FROM python:3.14.10-slim-trixie@sha256:930e1abba7c4e8f96ed93e8012c35d174d7732752731bbd5e8e757d7ec6f3966 AS deps
COPY --from=uv /uv /bin/uv
WORKDIR /build
COPY pyproject.toml uv.lock ./
RUN uv export --locked --only-group executor --no-emit-project --format requirements.txt \
              --output-file requirements.txt \
 && uv pip install --require-hashes --only-binary :all: --no-cache \
                   --python /usr/local/bin/python3.14 --target /opt/deps -r requirements.txt

# Estágio do FFmpeg (ADR 036): compilado do tarball oficial, conferido pelo SHA-256 (o tarball foi
# verificado com a assinatura GPG da chave de releases do FFmpeg, FCF9 86EA 15E6 E293 A564 4F10
# B432 2F04 D676 58D8, ao fixar a versão). `--disable-everything` e só o que o Media Mill usa:
# os demuxers mov e matroska, os decoders da allowlist, os encoders de JPEG, WAV, FLAC, AAC (o
# nativo), MP3 e Opus e os protocolos file e pipe. hls, concat, image2 como entrada, http e afins
# nem existem no binário; as flags de cada chamada (-protocol_whitelist file, -f forçado)
# continuam valendo por cima. As únicas bibliotecas externas são o LAME (LGPL 2+) e o Opus
# (BSD-3), estáticas (ADR 063): sem `--enable-gpl` nem `--enable-nonfree`, o binário continua
# LGPL 2.1 e autocontido, com a licença copiada para a imagem.
# Mesma base da imagem final, para o binário ligar contra a mesma glibc.
FROM python:3.14.10-slim-trixie@sha256:930e1abba7c4e8f96ed93e8012c35d174d7732752731bbd5e8e757d7ec6f3966 AS ffmpeg
# Cada ARG fica logo antes do RUN que o usa: subir uma versão não invalida o cache das camadas
# anteriores (o apt e as outras bibliotecas).
# nasm só no amd64 (assembly x86 do decoder H.264); no arm64 o NEON vem do próprio compilador.
RUN apt-get update -q \
 && apt-get install -yq --no-install-recommends gcc libc6-dev make pkg-config xz-utils curl \
        ca-certificates $(if [ "$(dpkg --print-architecture)" = amd64 ]; then echo nasm; fi) \
 && rm -rf /var/lib/apt/lists/*
# Mesmo padrão do FFmpeg: origem oficial, o `.orig` do Debian só se o download falhar, e o
# SHA-256 conferido depois, venha de onde vier; hash que não confere derruba o build.
# LAME e Opus (ADR 063): o Trivy não os vê dentro do binário, então as CVEs se acompanham por
# estas versões.
# Estáticos em /opt/codecs, que não vai para a imagem final; as licenças vão junto das do FFmpeg,
# que a imagem e o worker de desenvolvimento já copiam. Do LAME, só a biblioteca de codificação:
# sem o executável e sem o decoder de MP3 (o mpg123 embutido), que ninguém chama.
WORKDIR /build/lame
# O SHA-256 do LAME é o mesmo no SourceForge e no `.orig` do Debian.
ARG LAME_VERSION=3.100
ARG LAME_SHA256=ddfe36cab873794038ae2c1210557ad34857a4b6bdc515785d1da9e175b1da1e
RUN { curl -fsSL --retry 3 --retry-all-errors --retry-delay 5 --connect-timeout 20 -o lame.tar.gz "https://downloads.sourceforge.net/project/lame/lame/${LAME_VERSION}/lame-${LAME_VERSION}.tar.gz" \
      || curl -fsSL --retry 3 --retry-all-errors --retry-delay 5 --connect-timeout 20 -o lame.tar.gz "https://deb.debian.org/debian/pool/main/l/lame/lame_${LAME_VERSION}.orig.tar.gz"; } \
 && echo "${LAME_SHA256}  lame.tar.gz" | sha256sum -c - \
 && tar -xzf lame.tar.gz --strip-components=1 \
 && rm lame.tar.gz \
 && ./configure --disable-shared --enable-static --prefix=/opt/codecs \
        --disable-frontend --disable-decoder --disable-analyzer-hooks \
 && make -j"$(nproc)" \
 && make install \
 && install -D -m 0644 COPYING /opt/ffmpeg/share/doc/ffmpeg/lame/COPYING \
 && install -D -m 0644 LICENSE /opt/ffmpeg/share/doc/ffmpeg/lame/LICENSE
WORKDIR /build/opus
# O SHA-256 da Xiph é o do SHA256SUMS.txt dela; o Debian republica o mesmo conteúdo como
# `.tar.xz`, com o hash do `Checksums-Sha256` do opus_1.6.1-1.dsc. O arquivo baixado tem de
# conferir com um dos dois.
ARG OPUS_VERSION=1.6.1
ARG OPUS_SHA256=6ffcb593207be92584df15b32466ed64bbec99109f007c82205f0194572411a1
ARG OPUS_DEBIAN_SHA256=001ac615b9f8f8994b229ad2b1a5969ed12ab48b0d154d0af1016a5899cd6ba1
RUN { curl -fsSL --retry 3 --retry-all-errors --retry-delay 5 --connect-timeout 20 -o opus.tar "https://downloads.xiph.org/releases/opus/opus-${OPUS_VERSION}.tar.gz" \
      || curl -fsSL --retry 3 --retry-all-errors --retry-delay 5 --connect-timeout 20 -o opus.tar "https://deb.debian.org/debian/pool/main/o/opus/opus_${OPUS_VERSION}.orig.tar.xz"; } \
 && { echo "${OPUS_SHA256}  opus.tar" | sha256sum -c --status - \
      || echo "${OPUS_DEBIAN_SHA256}  opus.tar" | sha256sum -c -; } \
 && tar -xf opus.tar --strip-components=1 \
 && rm opus.tar \
 && ./configure --disable-shared --enable-static --prefix=/opt/codecs \
        --disable-doc --disable-extra-programs \
 && make -j"$(nproc)" \
 && make install \
 && install -D -m 0644 COPYING /opt/ffmpeg/share/doc/ffmpeg/opus/COPYING
WORKDIR /build/ffmpeg
# Segunda origem: o ffmpeg.org já ficou fora do ar e travou CI e release. O Debian publica o mesmo
# tarball, e o SHA-256 abaixo é o que garante a integridade, venha de onde vier.
ARG FFMPEG_VERSION=8.1.2
ARG FFMPEG_SHA256=464beb5e7bf0c311e68b45ae2f04e9cc2af88851abb4082231742a74d97b524c
RUN curl -fsSL --retry 3 --retry-all-errors --retry-delay 5 --connect-timeout 20 -o ffmpeg.tar.xz "https://ffmpeg.org/releases/ffmpeg-${FFMPEG_VERSION}.tar.xz" \
  || curl -fsSL --retry 3 --retry-all-errors --retry-delay 5 --connect-timeout 20 -o ffmpeg.tar.xz "https://deb.debian.org/debian/pool/main/f/ffmpeg/ffmpeg_${FFMPEG_VERSION}.orig.tar.xz" \
 && echo "${FFMPEG_SHA256}  ffmpeg.tar.xz" | sha256sum -c - \
 && tar -xJf ffmpeg.tar.xz --strip-components=1 \
 && rm ffmpeg.tar.xz
RUN PKG_CONFIG_PATH=/opt/codecs/lib/pkgconfig ./configure --prefix=/opt/ffmpeg \
        --disable-everything --disable-autodetect --disable-network --disable-doc \
        --disable-debug --disable-ffplay --disable-avdevice --disable-shared --enable-static \
        --enable-ffmpeg --enable-ffprobe --enable-avfilter --enable-swscale --enable-swresample \
        --enable-libmp3lame --enable-libopus --pkg-config-flags=--static \
        --extra-cflags=-I/opt/codecs/include --extra-ldflags=-L/opt/codecs/lib \
        --enable-protocol=file,pipe \
        --enable-demuxer=mov,matroska \
        --enable-decoder=h264,vp8,vp9,aac,opus,vorbis,pcm_s16le,pcm_s16be,pcm_s24le,pcm_s24be \
        --enable-parser=h264,vp8,vp9,aac,opus,vorbis \
        --enable-bsf=vp9_superframe_split \
        --enable-encoder=mjpeg,pcm_s16le,flac,aac,libmp3lame,libopus \
        --enable-muxer=wav,flac,image2,mp3,ipod,opus \
        --enable-filter=scale,format,aformat,aresample,asetpts,showinfo,ashowinfo,transpose,hflip,vflip,null,anull,trim,atrim \
 && make -j"$(nproc)" \
 && make install \
 && install -D -m 0644 COPYING.LGPLv2.1 /opt/ffmpeg/share/doc/ffmpeg/COPYING.LGPLv2.1 \
 && install -D -m 0644 LICENSE.md /opt/ffmpeg/share/doc/ffmpeg/LICENSE.md

FROM python:3.14.10-slim-trixie@sha256:930e1abba7c4e8f96ed93e8012c35d174d7732752731bbd5e8e757d7ec6f3966

# PYTHONSAFEPATH: com `python -m`, o diretório corrente (/work, preenchido pelo worker) entraria
# no sys.path e um .py ali sombrearia a biblioteca padrão.
# PYTHONDONTWRITEBYTECODE: a raiz é somente leitura; o bytecode já vem compilado abaixo.
ENV PYTHONPATH=/opt/goblinshift \
    PYTHONSAFEPATH=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Sobe apt, zlib e libzstd até a base oficial trazer as correções da CVE-2026-109518 (apt 3.0.4),
# das CVE-2026-76844 e CVE-2026-85091 (zlib1g deb13u1) e da CVE-2026-124851 (libzstd1 deb13u1),
# que barram a release no Trivy.
RUN apt-get update -qq \
 && apt-get install -yq --no-install-recommends --only-upgrade apt libapt-pkg7.0 zlib1g libzstd1 \
 && rm -rf /var/lib/apt/lists/*
# O pip não é usado em tempo de execução; sai para não sobrar um instalador dentro do sandbox.
# 61001 é o GID do grupo gs-exec do host, dono da área de trabalho montada em /work.
RUN rm -rf /usr/local/lib/python3.14/site-packages/pip \
           /usr/local/lib/python3.14/site-packages/pip-*.dist-info \
           /usr/local/lib/python3.14/ensurepip \
           /usr/local/bin/pip /usr/local/bin/pip3 /usr/local/bin/pip3.14 \
 && groupadd --gid 61001 gs-exec \
 && useradd --uid 61000 --gid 61001 --no-create-home --home-dir /nonexistent \
            --shell /usr/sbin/nologin executor

# FFmpeg e ffprobe no PATH padrão da base; o Media Mill os resolve por caminho absoluto e os roda
# com ambiente vazio (ADR 036).
COPY --from=ffmpeg /opt/ffmpeg/bin/ffmpeg /opt/ffmpeg/bin/ffprobe /usr/local/bin/
COPY --from=ffmpeg /opt/ffmpeg/share/doc/ffmpeg /usr/local/share/doc/ffmpeg

# No site-packages da base, as dependências aparecem para o importlib.metadata como qualquer
# distribuição.
COPY --from=deps /opt/deps /usr/local/lib/python3.14/site-packages/
COPY src/goblinshift/__init__.py /opt/goblinshift/goblinshift/__init__.py
COPY src/goblinshift/processing /opt/goblinshift/goblinshift/processing
# unchecked-hash: a imagem é imutável, então o .pyc nunca é revalidado contra o fonte.
RUN python -m compileall -q --invalidation-mode unchecked-hash \
        /opt/goblinshift /usr/local/lib/python3.14/site-packages/PIL \
        /usr/local/lib/python3.14/site-packages/pikepdf
# Nenhum pacote GPL/AGPL nem ferramenta de dev no runtime (ADR 057). Roda no build de cada
# plataforma, antes do push; o bind mount não deixa o script na imagem.
RUN --mount=type=bind,source=scripts/check_runtime_licenses.py,target=/tmp/check_runtime_licenses.py \
    python -I -B /tmp/check_runtime_licenses.py

USER 61000:61001
WORKDIR /work
ENTRYPOINT ["python", "-m", "goblinshift.processing.executor", "/work"]
