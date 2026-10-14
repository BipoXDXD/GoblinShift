# Imagem do executor: roda uma tentativa dentro do container gVisor (docs/GoblinShiftPlan.md, 3.6).
# Só a biblioteca padrão e o pacote goblinshift.processing; nada de banco, web nem dependências de
# terceiros, para que o conteúdo do usuário encontre a menor superfície possível. O executor-ctl
# só aceita esta imagem pelo digest que o deploy grava na allowlist (deploy/release.sh).
#
# Base fixada pelo digest do índice multiplataforma (amd64 na VPS e na CI, arm64 no Mac).
FROM python:3.14.8-slim-trixie@sha256:a2b82f3c48559aa0a8446d9af49826b6e2b2016f4cd2afabfe6013ec53729170

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
           /usr/local/bin/pip /usr/local/bin/pip3 /usr/local/bin/pip3.14 \
 && groupadd --gid 61001 gs-exec \
 && useradd --uid 61000 --gid 61001 --no-create-home --home-dir /nonexistent \
            --shell /usr/sbin/nologin executor

COPY src/goblinshift/__init__.py /opt/goblinshift/goblinshift/__init__.py
COPY src/goblinshift/processing /opt/goblinshift/goblinshift/processing
# unchecked-hash: a imagem é imutável, então o .pyc nunca é revalidado contra o fonte.
RUN python -m compileall -q --invalidation-mode unchecked-hash /opt/goblinshift

USER 61000:61001
WORKDIR /work
ENTRYPOINT ["python", "-m", "goblinshift.processing.executor", "/work"]
