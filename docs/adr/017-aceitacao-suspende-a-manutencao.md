# ADR 017. O teste de aceitação suspende a manutenção durante as sondas

- **Estado:** aceito (correção na VPS depois da fatia 4, 2026-10-14)
- **Plano:** [3.4](../GoblinShiftPlan.md#s3-4), [3.5](../GoblinShiftPlan.md#s3-5), [3.8](../GoblinShiftPlan.md#s3-8)
- **Relacionado:** [ADR 004](004-container-gvisor-por-tentativa.md), [ADR 005](005-area-do-executor-em-filesystem-fixo.md)

## Contexto

As sondas do teste de aceitação (`deploy/acceptance/run.py`) criam `<área>/<uuid>` e o container `gs-exec-<uuid>` com o perfil e as regras de produção, para provar os limites do executor real. Para a manutenção, isso é uma tentativa sem linha no banco: `sweep_work_areas` pede `status`, `stop` e `remove` ao `executor-ctl` no ciclo seguinte (30 s). As sondas curtas terminam antes; a de disco leva minutos e, na VPS, foi parada com SIGTERM (saída 143) e falhou com "a sonda não relatou nada".

No worker não existe janela equivalente: o claim (que cria a Attempt) é commitado antes de `prepare` criar a área, então toda área de produção tem linha no banco quando a manutenção a lista.

## Decisão

O teste de aceitação para o container `maintenance` do Compose (`docker stop --time=60`, que deixa o ciclo em curso terminar) antes das sondas e o religa (`docker start`) depois, também se uma sonda levantar exceção. A volta é conferida (`State.Running`) e vira o critério "manutenção religada depois das sondas", que reprova o teste se falhar e traz o comando de recuperação. Manutenção que já estava parada fica como estava. A manutenção de produção não muda.

## Alternativas descartadas

| Alternativa | Motivo |
|---|---|
| Carência na manutenção antes de reagir a tentativa desconhecida | Muda produção por causa de um teste. A janela que ela cobriria (área criada antes do commit) não existe no worker. A carência teria de passar do prazo da sonda de disco (15 min) e atrasaria a limpeza das sobras reais, que travam o próximo `start` |
| Prefixo de nome que a manutenção ignora | A sonda deixaria de ser idêntica a uma tentativa de produção, que é o que o teste prova. Além disso, uma lista de exceções na limpeza é uma porta para sobras que nunca são removidas |

## Consequências

- Durante as sondas (cerca de 10 a 20 min), não há reconciliação de lease, prazos nem retenção. O teste já exige o worker parado, então não há tentativa de produção para reconciliar. A retenção só atrasa.
- Se o processo do teste for morto à força (SIGKILL ou queda da sessão), a manutenção fica parada: `restart: unless-stopped` não religa um container parado por `docker stop`. O teste imprime o comando de recuperação antes de parar a manutenção.
- O teste de caos (`acceptance.chaos`) continua precisando da manutenção no ar e não é afetado: ele não usa esta suspensão.

## Como verificar

- `tests/acceptance/test_run.py`: as sondas rodam com a manutenção parada; ela volta e é conferida, inclusive quando uma sonda falha; falha na volta reprova com o comando de recuperação.
- Na VPS, `sudo python3 -B -m acceptance.run` passa em "ENOSPC aos 6 GiB" e em "manutenção religada depois das sondas", e `docker ps` mostra o `maintenance` rodando ao fim.
