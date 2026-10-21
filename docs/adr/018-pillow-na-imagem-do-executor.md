# ADR 018. Pillow fixado na imagem do executor, instalado do lock por um estágio de build

- **Estado:** aceito na etapa 0 da fatia 5 (2026-10-14) e implementado na fatia 5 (estágio de build, testes da imagem com Image Forge e Detox sob `runsc`); pior caso medido na VPS em 193 s (ver Consequências)
- **Plano:** [1.2](../GoblinShiftPlan.md#s1-2), [1.7](../GoblinShiftPlan.md#s1-7), [2.3](../GoblinShiftPlan.md#s2-3), [3.6](../GoblinShiftPlan.md#s3-6)
- **Relacionado:** [ADR 004](004-container-gvisor-por-tentativa.md), [ADR 010](010-python-3-14.md)

## Contexto

O Image Forge decodifica, converte e grava JPEG e PNG, com perfis de cor e EXIF. Até a fatia 4 a imagem do executor era só biblioteca padrão, com a base `python:3.14.8-slim-trixie` fixada por digest, e o teste da imagem exigia que nenhuma distribuição de terceiros estivesse instalada. Pillow é a única dependência nova que a fatia 5 precisa (o Downloads Detox só usa a biblioteca padrão). A versão atual no PyPI é a 12.3.0, com wheels `cp314` manylinux para x86_64 e aarch64, que embutem libjpeg-turbo, zlib e LittleCMS.

## Decisão

- **Pin exato em dois lugares do `pyproject.toml`, uma versão no lock.** `"pillow==12.3.0"` em `[project.dependencies]` (testes, API e worker usam a mesma versão) e num grupo de dependências `executor` que contém só o Pillow. `tests/test_dependencies.py` exige que os dois pins sejam iguais, que o `uv.lock` tenha a 12.3.0 e que o ambiente de testes a importe.
- **A imagem instala o grupo a partir do lock** (frente A): um estágio de build com o `uv` fixado por digest roda `uv export --locked --only-group executor --no-emit-project` e `uv pip install --require-hashes --only-binary :all: --target /opt/deps`; a imagem final copia só `/opt/deps`, sem `pip` nem `uv`.
- **A política da imagem passa a ser "biblioteca padrão + Pillow fixado".** O teste da imagem exige que `pillow` seja a única distribuição de terceiros e que `PIL.features` tenha `jpg`, `zlib` e `littlecms2`.
- **Só o executor carrega o PIL.** O contrato "Só o executor carrega o PIL" do import-linter proíbe o PIL, inclusive por import indireto, em `catalog`, `files`, `runs`, `presets`, `identity`, `platform` e `entrypoints`.

## Alternativas descartadas

| Alternativa | Motivo |
|---|---|
| `docker/executor-requirements.txt` com hashes e `pip install --require-hashes` | Segunda fonte da versão, que precisaria de outra checagem no CI para não divergir do lock |
| `python3-pil` do Debian | Versão e Python diferentes da base 3.14: não serve |
| Pillow sem pin exato (`>=`) | A imagem e os testes poderiam rodar versões diferentes; a saída do encoder (determinismo do ZIP, 3.9) depende da versão |

## Consequências

- O wheel traz bibliotecas nativas (libjpeg-turbo, zlib, LittleCMS e outras): a superfície cresce. Ela é limitada por `Image.open(formats=("JPEG", "PNG", "MPO"))`, pela validação do cabeçalho antes de `load()` e pelo gVisor ([ADR 004](004-container-gvisor-por-tentativa.md)).
- Atualizar o Pillow é uma mudança de três lugares conferida por teste (dois pins e o lock) e exige uma imagem nova do executor; Runs já aceitas continuam na imagem fixada nelas.
- O grupo `executor` não entra no `uv sync` padrão: só o Dockerfile o usa.
- O executor chama `image_forge.inspection.configure_pillow()` uma vez no `main`: `MAX_IMAGE_PIXELS` em 24 MP e `DecompressionBombWarning` como erro, segunda barreira atrás da validação do cabeçalho.
- Pior caso (100 JPEGs de 24 MP em Display P3 com orientação 6 e miniaturas, `scripts/measure-image-forge.sh`): referência local de 133 s (Mac, worker com Runner local, sem gVisor nem limite de CPU, 2026-10-14). A medição que vale, sob gVisor e 1 vCPU, é feita na VPS antes do merge da fatia 5 com `sudo python3 -B -m acceptance.image_forge_load` (perfil de produção, sem passar pela API, porque o deploy só implanta a `main`) e, depois do deploy, com `scripts/measure-image-forge.sh`. Se não couber nos 10 min da tentativa, o `max_inputs` da v1 do Image Forge cai antes de a versão chegar à `main`, e o número medido é registrado aqui.
  **Medido na VPS, sob gVisor (1 vCPU, 2 GiB, sem rede):** 100 imagens de 24 MP em **193 s**, contra o teto de 600 s da tentativa, então o `max_inputs` de 100 ficou. O número vem da revisão do [PR #14](https://github.com/BipoXDXD/GoblinShift/pull/14), mergeado em 2026-10-14. A rodada de `scripts/measure-image-forge.sh` pela API depois do deploy não tem registro.

## Como verificar

- `uv run pytest tests/test_dependencies.py`: pins, lock e ambiente na 12.3.0.
- `uv run lint-imports`: contrato "Só o executor carrega o PIL" mantido.
- Na imagem do executor (marcador `executor_image`, também sob `runsc` no job `gvisor`): `PIL.__version__ == "12.3.0"`, `pillow` como única distribuição de terceiros, e Image Forge e Downloads Detox processando um work dir real com o mesmo ZIP em duas execuções (`tests/test_executor_image.py`).

## Atualização de segurança — 2026-10-21

Os pins da aplicação e do executor passam a Pillow 13.0.0, mantendo a estratégia de versão exata e wheels com hashes. A versão corrige falhas críticas e altas de decodificação e limites de memória reportadas no ciclo 13.0.0. As APIs removidas nessa versão não são utilizadas pelo Image Forge; o projeto já exige Python 3.14. As mídias existentes não são reprocessadas nem modificadas pela atualização.

O lock também atualiza certifi para 2026.10.19, removendo as raízes emSign afetadas. As imagens de runtime não precisam de instaladores: pip e ensurepip, incluindo cópias vendorizadas de bibliotecas, são removidos; o build continua usando uv e o lock. Os testes de pin/ambiente e da imagem exigem 13.0.0 e ausência de ambos os instaladores. A imagem web recebe as versões corrigidas de curl/libcurl e libexpat, sem trocar o Nginx nem seus assets. A promoção permanece bloqueada até os testes completos, gVisor e Trivy das imagens passarem.
