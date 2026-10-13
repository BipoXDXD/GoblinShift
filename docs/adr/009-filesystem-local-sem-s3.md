# ADR 009. Filesystem local para arquivos privados, sem backend S3 na v1

- **Estado:** aceito
- **Plano:** [1.7](../GoblinShiftPlan.md#s1-7), [2.1](../GoblinShiftPlan.md#s2-1), [3.6](../GoblinShiftPlan.md#s3-6), [3.8](../GoblinShiftPlan.md#s3-8), [3.10](../GoblinShiftPlan.md#s3-10)
- **Referências:** [D10](../GoblinShiftReferencias.md#d-2)

## Contexto

O serviço guarda uploads e artefatos privados, que expiram (upload não usado em 24 horas; entradas usadas e saídas 48 horas após o término) e não têm backup por decisão. Na v1 há um único servidor, a VPS KVM 2 (ADR 011).

## Decisão

Os arquivos ficam no filesystem local da VPS, em `/srv/goblinshift/storage`, um filesystem de tamanho fixo de 24 GiB (os 20 GiB do ambiente mais folga), o que torna o teto do ambiente também um limite físico. A escrita usa criação exclusiva (`O_CREAT | O_EXCL`), fora da raiz pública, atrás de uma interface estreita no módulo `files`: `create_exclusive(key, chunks) -> (sha256, size)`, `open(key)`, `stat(key)` e `delete(key)`. Um backend compatível com S3 só entra se aparecer o gatilho de [2.1](../GoblinShiftPlan.md#s2-1): mais de um servidor ou falta de disco. Se entrar, cumpre o mesmo contrato: bucket privado, chaves exclusivas, escrita condicional, e a ETag não é o SHA-256.

## Alternativas descartadas

| Alternativa       | Motivo                                                |
|-------------------|-------------------------------------------------------|
| S3 desde o início | Generalidade especulativa enquanto houver um servidor |

## Consequências

- O teto de 20 GiB do ambiente vale também como limite físico do filesystem; encher o storage não derruba o banco nem o sistema, porque cada compartimento é um filesystem separado ([3.8](../GoblinShiftPlan.md#s3-8)).
- Não há backup de arquivos. Depois de restaurar o banco, os objetos ausentes ficam `missing`, nunca substituídos por arquivos vazios ([1.7](../GoblinShiftPlan.md#s1-7)).
- "Storage acima de 85%" é alerta de causa ([3.7](../GoblinShiftPlan.md#s3-7)).
- A interface cobre só criar, ler, consultar metadados e excluir; é esse contrato que um backend S3 futuro teria de cumprir.

## Como verificar

- `pyproject.toml` não declara dependência de cliente S3 (como `boto3`) enquanto o gatilho de [2.1](../GoblinShiftPlan.md#s2-1) não ocorrer.
- Na VPS, `df -h /srv/goblinshift/storage` mostra o filesystem de 24 GiB (criado na fatia 0).
- A partir da fatia 3, a implementação de `Storage` no módulo `files` expõe só as quatro operações acima.
- A partir da fatia 8, o alerta de storage acima de 85% está configurado.
