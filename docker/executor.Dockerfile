# Imagem do executor: roda uma tentativa dentro do container gVisor (docs/GoblinShiftPlan.md, 3.6).
# Só a biblioteca padrão, o Pillow fixado (ADR 018), o pikepdf fixado com o qpdf da própria wheel
# e suas dependências lxml e packaging (ADR 035), o FFmpeg mínimo do Media Mill (ADR 036) e o
# pacote goblinshift.processing; nada de banco, web nem outras dependências de terceiros, para que
# o conteúdo do usuário encontre a menor superfície possível. O executor-ctl só aceita esta imagem
# pelo digest que o deploy grava na allowlist (deploy/release.sh).
#
# Bases fixadas pelo digest do índice multiplataforma (amd64 na VPS e na CI, arm64 no Mac).
FROM ghcr.io/astral-sh/uv:0.12.15@sha256:62f8c047d0a0e9ece6b53fc63df902585a67a47a7f318ddec4a37db586edc8e3 AS uv

# Estágio de build: instala o grupo `executor` do uv.lock (Pillow e pikepdf) com os hashes do lock e só
# a partir de wheels. Nem o uv nem o requirements gerado chegam à imagem final.
FROM python:3.14.9-slim-trixie@sha256:a145a961ec2cf5741772d5e39773d979271132884fd778f6c3fad34e52b85402 AS deps
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
# os demuxers mov e matroska, os decoders da allowlist, os encoders de JPEG, WAV e FLAC e os
# protocolos file e pipe. hls, concat, image2 como entrada, http e afins nem existem no binário;
# as flags de cada chamada (-protocol_whitelist file, -f forçado) continuam valendo por cima.
# Sem `--enable-gpl` nem bibliotecas externas: LGPL 2.1, com a licença copiada para a imagem.
# Mesma base da imagem final, para o binário ligar contra a mesma glibc.
FROM python:3.14.9-slim-trixie@sha256:a145a961ec2cf5741772d5e39773d979271132884fd778f6c3fad34e52b85402 AS ffmpeg
ARG FFMPEG_VERSION=8.1.2
ARG FFMPEG_SHA256=464beb5e7bf0c311e68b45ae2f04e9cc2af88851abb4082231742a74d97b524c
# nasm só no amd64 (assembly x86 do decoder H.264); no arm64 o NEON vem do próprio compilador.
RUN apt-get update -q \
 && apt-get install -yq --no-install-recommends gcc libc6-dev make pkg-config xz-utils curl \
        ca-certificates $(if [ "$(dpkg --print-architecture)" = amd64 ]; then echo nasm; fi) \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /build
RUN curl -fsSL -o ffmpeg.tar.xz "https://ffmpeg.org/releases/ffmpeg-${FFMPEG_VERSION}.tar.xz" \
 && echo "${FFMPEG_SHA256}  ffmpeg.tar.xz" | sha256sum -c - \
 && tar -xJf ffmpeg.tar.xz --strip-components=1 \
 && rm ffmpeg.tar.xz
RUN ./configure --prefix=/opt/ffmpeg \
        --disable-everything --disable-autodetect --disable-network --disable-doc \
        --disable-debug --disable-ffplay --disable-avdevice --disable-shared --enable-static \
        --enable-ffmpeg --enable-ffprobe --enable-avfilter --enable-swscale --enable-swresample \
        --enable-protocol=file,pipe \
        --enable-demuxer=mov,matroska \
        --enable-decoder=h264,vp8,vp9,aac,opus,vorbis,pcm_s16le,pcm_s16be,pcm_s24le,pcm_s24be \
        --enable-parser=h264,vp8,vp9,aac,opus,vorbis \
        --enable-bsf=vp9_superframe_split \
        --enable-encoder=mjpeg,pcm_s16le,flac \
        --enable-muxer=wav,flac,image2 \
        --enable-filter=scale,format,aformat,aresample,showinfo,transpose,hflip,vflip,null,anull,trim,atrim \
 && make -j"$(nproc)" \
 && make install \
 && install -D -m 0644 COPYING.LGPLv2.1 /opt/ffmpeg/share/doc/ffmpeg/COPYING.LGPLv2.1 \
 && install -D -m 0644 LICENSE.md /opt/ffmpeg/share/doc/ffmpeg/LICENSE.md

FROM python:3.14.9-slim-trixie@sha256:a145a961ec2cf5741772d5e39773d979271132884fd778f6c3fad34e52b85402

# PYTHONSAFEPATH: com `python -m`, o diretório corrente (/work, preenchido pelo worker) entraria
# no sys.path e um .py ali sombrearia a biblioteca padrão.
# PYTHONDONTWRITEBYTECODE: a raiz é somente leitura; o bytecode já vem compilado abaixo.
ENV PYTHONPATH=/opt/goblinshift \
    PYTHONSAFEPATH=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

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

USER 61000:61001
WORKDIR /work
ENTRYPOINT ["python", "-m", "goblinshift.processing.executor", "/work"]
