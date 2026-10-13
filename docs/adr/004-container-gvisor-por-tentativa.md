# ADR 004. Um container gVisor por tentativa, criado pelo `executor-ctl`

- **Estado:** aceito
- **Plano:** [1.7](../GoblinShiftPlan.md#s1-7), [2.1](../GoblinShiftPlan.md#s2-1), [3.1](../GoblinShiftPlan.md#s3-1), [3.5](../GoblinShiftPlan.md#s3-5), [3.6](../GoblinShiftPlan.md#s3-6), [3.8](../GoblinShiftPlan.md#s3-8), [3.9](../GoblinShiftPlan.md#s3-9), [3.10](../GoblinShiftPlan.md#s3-10)
- **Referências:** [D17](../GoblinShiftReferencias.md#d-4), [D18](../GoblinShiftReferencias.md#d-4)

## Contexto

O serviço é aberto a qualquer conta e processa arquivos de terceiros, então o código que decodifica esse conteúdo precisa de isolamento forte. O worker é confiável (acessa banco e storage) e precisa iniciar e parar o executor. Quem controla o daemon do Docker pode montar `/` do host num container, ou seja, tem root sobre a VPS; um worker com o socket do Docker teria, na prática, esse root.

## Decisão

Três peças com privilégios separados. O worker confiável prepara o diretório da tentativa e coleta os resultados, sem acesso ao Docker. O `executor-ctl` é um serviço pequeno do host (systemd, fora do Compose), o único com acesso ao Docker; expõe por socket Unix, a um grupo restrito, só `start(attempt_id)`, `status(attempt_id)`, `stop(attempt_id, grace)` e `remove(attempt_id)`, valida o `attempt_id` como UUID, deriva sozinho os caminhos e o nome `gs-exec-{attempt_id}` e usa um perfil fixo de `docker run`, sem que o worker escolha imagem, montagens ou opções. O executor é um container novo por tentativa, com imagem fixada por digest, sob gVisor (`--runtime=runsc`), `--network=none`, raiz `--read-only`, `--cap-drop=ALL`, `no-new-privileges`, usuário não-root e limites de processos, memória e CPU de [1.7](../GoblinShiftPlan.md#s1-7). Recebe só as entradas daquela tentativa, somente leitura, e a configuração validada; sem tokens, diretórios pessoais ou credenciais.

## Alternativas descartadas

| Alternativa                            | Motivo                                                                                                                     |
|----------------------------------------|----------------------------------------------------------------------------------------------------------------------------|
| Socket do Docker no worker             | Quem controla o daemon tem root no host; o worker teria, na prática, root sobre a VPS                                      |
| Serviço executor fixo com subprocessos | Não dá estado novo por tentativa: o mesmo serviço sobrevive de uma tentativa para a outra                                  |
| nsjail                                 | O plano não registra o motivo específico; o motivo principal da decisão é o isolamento forte com estado novo por tentativa |

## Consequências

- O gVisor reduz muito a superfície do kernel do host exposta ao conteúdo, mas não elimina falhas: nem container nem gVisor são garantia absoluta. Os limites declarados precisam funcionar no runtime utilizado e são provados pelo teste de aceitação do ambiente.
- Como a VPS é compartilhada com outro projeto (ADR 011), o isolamento entre os projetos depende do gVisor, risco aceito.
- Lease vencido não prova que o executor morreu: a manutenção consulta o `executor-ctl` pelo nome determinístico e só reagenda depois de confirmar o término ou de parar o container.
- Se o `executor-ctl` cair, não é possível iniciar nem parar containers; as execuções aguardam dentro dos prazos e a manutenção não reagenda nada que não consiga inspecionar.
- O `Runner` local, em subprocesso, só existe fora de produção; em produção o serviço recusa iniciar com ele.
- A área de trabalho do executor é um filesystem de tamanho fixo (ADR 005).

## Como verificar

- O teste de aceitação do ambiente ([3.8](../GoblinShiftPlan.md#s3-8)) passa na VPS: sem rede, `ENOSPC` aos 6 GiB, morte por falta de memória acima de 2 GiB, fork bomb contido em 64 processos, container sob `runsc` e o worker sem acesso a `/var/run/docker.sock`. É o critério da fatia 1 (protótipo) e continua valendo na fatia 4 e a cada mudança no host.
- Nenhum arquivo Compose (`compose.yaml`, `compose.prod.yaml`, `compose.override.yaml`) monta `/var/run/docker.sock` (vale desde já).
- Na fatia 4, o `executor-ctl` de produção roda como serviço systemd do host, expõe só as quatro operações e o serviço recusa iniciar com o `Runner` local.
