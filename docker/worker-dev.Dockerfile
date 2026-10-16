# Worker de desenvolvimento (compose.override.yaml): a imagem da aplicação mais o FFmpeg mínimo do
# Media Mill. Com o Runner local, a receita roda num subprocesso do worker, e não na imagem do
# executor; sem estes binários o Media Mill recusaria toda Run local.
#
# Os dois contextos vêm do Compose (`additional_contexts`): `app` é a imagem da aplicação que a
# própria pilha constrói, e `ffmpeg` é o estágio `ffmpeg` do docker/executor.Dockerfile, com a
# mesma versão, o mesmo SHA-256 e o mesmo `configure` da produção (ADR 036). O FFmpeg do sistema
# traria todos os demuxers e protocolos e mudaria o comportamento da receita. As cópias abaixo
# são as mesmas da imagem do executor (tests/test_compose_dev_ffmpeg.py confere).
FROM app

COPY --from=ffmpeg /opt/ffmpeg/bin/ffmpeg /opt/ffmpeg/bin/ffprobe /usr/local/bin/
COPY --from=ffmpeg /opt/ffmpeg/share/doc/ffmpeg /usr/local/share/doc/ffmpeg
