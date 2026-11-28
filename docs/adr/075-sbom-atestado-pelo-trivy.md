# ADR 075. SBOM das imagens gerado pelo Trivy e atestado fora do buildx

- **Estado:** aceito (2026-11-28, decisão do planejador, reversível)
- **Relacionado:** issue #366 (item L11 h), #442 (item 68c, D10), #538 (atestado de proveniência)

## Contexto

Os jobs `image`, `executor-image` e `web-image` do `ci.yml` constroem um índice OCI multiarch (`linux/amd64` e `linux/arm64`) num layout local (`type=oci,tar=false`). O `scripts/ci-oci.sh` sobe esse layout num registro descartável, confere o digest (`stage`) e escaneia as duas plataformas com o Trivy fixado por digest (`scan`). Depois, o job atesta a proveniência do índice com `actions/attest-build-provenance` (só no push) e envia o layout como artefato. O `publish-images` do `coolify.yml` baixa o layout, roda `gh attestation verify` no blob do índice e só então grava no GHCR.

O BuildKit já anexa ao índice a proveniência `mode=min` por padrão, como manifests `unknown/unknown`, e o `deploy/coolify/release/ci_oci.py` (`verify`) já os pula. O `gh attestation verify` sem `--predicate-type` confere o predicado SLSA de proveniência. Faltava um inventário assinado dos pacotes de cada imagem (L11 h).

## Alternativas

| Opção | Prós | Contras |
|---|---|---|
| (A) `sbom: true` (e `provenance: mode=max`) no `docker/build-push-action` | duas linhas; o BuildKit gera o SBOM; o `ci_oci.py verify` já tolera os manifests `unknown/unknown` | o SBOM vira mais um manifest de atestação dentro do índice que é atestado e publicado: o conteúdo do índice muda junto com o inventário, o SBOM fica fora da API de atestados do GitHub (onde está a proveniência que o `publish-images` confere) e o inventário sai do scanner do BuildKit, não do Trivy que já aprova o índice |
| (B) Trivy (`--format spdx-json`) no índice já escaneado e `actions/attest-sbom` no mesmo sujeito | não mexe no índice; reaproveita o Trivy fixado por digest, o mesmo do scan; um SBOM por plataforma, na mesma API de atestados da proveniência | seis passos a mais (um gerador e dois atestados por job) |

## Decisão

**(B).** `scripts/ci-oci.sh sbom <role> <index_digest> <platform>` roda o Trivy em `--image-src remote` contra `127.0.0.1:5000/<imagem>@<digest>` e grava `$RUNNER_TEMP/sbom-<role>-<arch>.spdx.json`. O subcomando falha alto (`::error::`, via `fail`) se o Trivy sair com erro, se o arquivo não existir ou estiver vazio, se passar de 16 MiB (limite do `attest-sbom`) ou se listar menos de dois pacotes. Assim um SBOM vazio nunca vira atestado.

Em cada job de imagem, depois do atestado de proveniência e antes do upload do layout:

1. um passo gera os SBOMs das duas plataformas, também no PR, para falhar cedo;
2. dois passos `actions/attest-sbom` (fixado por SHA), só no push, com o mesmo `subject-name` da proveniência, `subject-digest` do índice, o `sbom-path` da plataforma e `push-to-registry: false`.

## Consequências

- O SBOM é informativo. A verificação do SBOM não bloqueia o release, mas a geração bloqueia: sem SBOM válido o job de imagem falha. O `publish-images` continua exigindo só a proveniência, e nenhum `gh attestation verify` leva `--predicate-type` de SPDX. Quem quiser o inventário consulta `gh attestation verify --predicate-type https://spdx.dev/Document/v2.3` ou a API de atestados.
- Falha do Trivy ou SBOM inválido derruba o job de imagem, e sem o layout não há publicação. O release fica preso até o SBOM voltar a sair, o que é intencional: o atestado não sai vazio.
- Os dois SBOMs ficam ligados ao digest do índice, não ao de cada manifest de plataforma; a plataforma está no nome do arquivo e no conteúdo SPDX.
- `tests/test_ci_atestado.py` trava a ordem (scan, geração, atestados, upload), os campos dos atestados e as falhas do subcomando, com um `docker` falso.
- Reverter para (A) faz sentido se o `publish-images` passar a conferir atestações de dentro do índice em vez da API do GitHub, ou se o Trivy deixar de ser o scanner do CI. A troca é remover os seis passos e o subcomando e ligar `sbom: true` no buildx; o `ci_oci.py verify` já aceita os manifests extras.
