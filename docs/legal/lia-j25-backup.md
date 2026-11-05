# LIA J-25: backup do banco de dados

> **Registro interno, não publicar.** Revisado em 2026-11-05, por decisão do dono do serviço. Avaliação de legítimo interesse (LGPD, art. 7º,
> IX, e art. 10) redigida a partir do que o sistema faz (código e plano técnico). Fora do build da SPA, como a J-23. Quatro fases do guia orientativo da ANPD
> (2024). Id do levantamento: J-25. A transferência
> internacional (art. 33) é a J-19 e fica fora daqui.

## 0. O tratamento avaliado

| | |
|---|---|
| Titulares | Todas as pessoas com conta e quem aparece nos eventos de segurança (J-23) |
| Dados | Cópia lógica do banco inteiro (`pg_dump`): contas (provedor, sujeito, nome de exibição), sessões, chaves (só o hash), histórico de execuções, nomes de arquivo e parâmetros ainda não anulados (1.7: 30 dias), presets, eventos de segurança. **Sem arquivos**: uploads e resultados não têm backup |
| Como | Dump diário às 06:00 UTC, cifrado com `age` na VPS antes de sair, para uma chave pública versionada; a chave privada não fica na VPS |
| Onde | Backblaze B2, região Estados Unidos (US East), bucket com Object Lock em modo compliance |
| Prazo | Até 31 dias por versão: imutável por 30 dias (nem a conta dona apaga), ocultada nesse prazo e apagada 1 dia depois |
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
- 31 dias de retenção, no máximo: o tempo de descobrir uma corrupção silenciosa ou uma invasão. Menos
  que isso arrisca não ter uma cópia boa; mais aumentaria o tempo em que dados de contas
  excluídas sobrevivem.
- Cifra antes de sair da VPS: o provedor do armazenamento nunca vê o conteúdo.

## 3. Balanceamento

- **Expectativa legítima:** backup é esperado de qualquer serviço que guarde contas; o Aviso
  declara o backup cifrado, o prazo de até 31 dias e o país (itens 5, 6 e 7).
- **Impacto:** baixo enquanto a chave privada estiver protegida. O ponto sensível é o direito
  de eliminação: uma conta excluída continua nas cópias por até 31 dias e voltaria numa
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
- Depois de uma restauração, a data da cópia é publicada e o titular pode repetir o pedido de
  exclusão: não há diário de exclusões fora do dump (J-27).

## 5. Conclusão

A base do backup passou a ser **execução de contrato (inciso V)**, não legítimo interesse: a
cópia existe para manter a conta, os presets e o histórico que a pessoa usa. O balanceamento
acima fica como registro de proporcionalidade, porque o dump também leva eventos de segurança.
Pontos fixados na revisão:

1. **Art. 16.** A permanência por até 31 dias em cópia imutável e cifrada é declarada nos itens 5
   e 8 do Aviso; a cópia não é usada para outra finalidade e expira sozinha.
2. **GitHub Actions.** O runner que decifra o dump é operador e entra no item 7 do Aviso, com a
   transferência apoiada no art. 33, IX, combinado com o art. 7º, V.
3. **Restauração (J-27).** O diário de exclusões proposto não existe no código, e a auditoria
   restaurada não traz o que aconteceu depois da cópia. Vale o aviso do item 8 do Aviso.
