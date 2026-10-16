# LIA J-25: backup do banco de dados

> **RASCUNHO para o advogado, não publicar.** Avaliação de legítimo interesse (LGPD, art. 7º,
> IX, e art. 10) redigida a partir do que o sistema faz (código e plano técnico), não por
> advogado(a). Fora do build da SPA, como a J-23. Quatro fases do guia orientativo da ANPD
> (2024) e perguntas ao revisor no fim. Id do levantamento: J-25. A transferência
> internacional (art. 33) é a J-19 e fica fora daqui.

## 0. O tratamento avaliado

| | |
|---|---|
| Titulares | Todas as pessoas com conta e quem aparece nos eventos de segurança (J-23) |
| Dados | Cópia lógica do banco inteiro (`pg_dump`): contas (provedor, sujeito, nome de exibição), sessões, chaves (só o hash), histórico de execuções, nomes de arquivo e parâmetros ainda não anulados (1.7: 30 dias), presets, eventos de segurança. **Sem arquivos**: uploads e resultados não têm backup |
| Como | Dump diário às 06:00 UTC, cifrado com `age` na VPS antes de sair, para uma chave pública versionada; a chave privada não fica na VPS |
| Onde | Backblaze B2, região Estados Unidos (US East), bucket com Object Lock em modo compliance |
| Prazo | 30 dias por versão, imutável nesse período (nem a conta dona apaga), e expiração automática depois |
| Quem acessa | Ninguém lê o conteúdo sem a chave privada. O teste mensal de restauração decifra o dump num runner efêmero do GitHub Actions, descartado ao fim |
| Fontes técnicas | `docs/GoblinShiftPlan.md` 3.8; ADR 022; `deploy/backup/pg-backup.sh`; `.github/workflows/restore-check.yml` |

## 1. Finalidade

- **Interesse:** recuperar o serviço e os dados das próprias pessoas usuárias depois de um
  desastre (perda do disco, invasão com destruição, erro de operação), o que também protege
  os titulares contra a perda das suas contas, presets e histórico.
- **Situação concreta:** uma única VPS; os snapshots do provedor ficam no mesmo provedor e
  não atendem à regra 3-2-1. A restauração é testada todo mês, porque backup não testado não
  serve à finalidade.

## 2. Necessidade

- Só o banco: arquivos expiram em 48 h e não têm backup, o que já tira do backup o dado mais
  volumoso e mais sensível (o conteúdo).
- O dump leva o banco inteiro porque restaurar parte dele quebraria a integridade (chaves
  estrangeiras); a minimização acontece antes, no próprio banco (anulação de nomes e
  parâmetros aos 30 dias, remoção do histórico aos 90).
- 30 dias de retenção: o tempo de descobrir uma corrupção silenciosa ou uma invasão. Menos
  que isso arrisca não ter uma cópia boa; mais aumentaria o tempo em que dados de contas
  excluídas sobrevivem.
- Cifra antes de sair da VPS: o provedor do armazenamento nunca vê o conteúdo.

## 3. Balanceamento

- **Expectativa legítima:** backup é esperado de qualquer serviço que guarde contas; o Aviso
  declara o backup cifrado, o prazo de 30 dias e o país (itens 5, 6 e 7).
- **Impacto:** baixo enquanto a chave privada estiver protegida. O ponto sensível é o direito
  de eliminação: uma conta excluída continua nas cópias por até 30 dias e voltaria numa
  restauração (J-27).
- **Imutabilidade:** o Object Lock impede apagar uma cópia antes dos 30 dias, inclusive a
  pedido do titular. Isso é deliberado (protege contra ransomware), e o prazo curto limita o
  efeito.

## 4. Salvaguardas

- Cifra ponta a ponta com `age`; credencial da VPS só de escrita; credencial de leitura só no
  environment do teste de restauração.
- Nome do dump com 128 bits aleatórios e logs sem o nome completo.
- Restauração mensal num banco efêmero, com papel sem superusuário, e alerta externo
  (healthchecks.io) se o backup ou a restauração falharem.
- Proposta da J-27: um diário de exclusões pseudonimizado, fora do dump, reaplicado depois
  de toda restauração, para que a conta excluída não volte.

## Perguntas ao revisor

1. O backup cabe em legítimo interesse, ou no próprio contrato (inciso V), já que protege o
   serviço contratado?
2. A permanência por até 30 dias de uma conta excluída, em cópia imutável e cifrada, atende
   ao art. 16, ou é preciso declarar a hipótese de conservação de forma mais específica?
3. O runner do GitHub Actions que decifra o dump uma vez por mês é operador, e entra na J-19?
