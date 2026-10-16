# LIA J-23: auditoria de segurança e antiabuso

> **RASCUNHO para o advogado, não publicar.** Avaliação de legítimo interesse (LGPD, art. 7º,
> IX, e art. 10) redigida a partir do que o sistema faz (código e plano técnico), não por
> advogado(a). Nada daqui entra no build da SPA: só os dois textos públicos são importados
> (`tests/test_legal_texts.py` falha se outro arquivo de `docs/legal/` for importado pela
> interface). Estrutura em quatro fases, a do guia orientativo da ANPD sobre legítimo
> interesse (2024): finalidade, necessidade, balanceamento e salvaguardas. As **perguntas ao
> revisor** ficam no fim. Id do levantamento: J-23 (`notas-internas.md`, item 4 do Aviso).

## 0. O tratamento avaliado

| | |
|---|---|
| Controlador | O controlador identificado no item 1 do Aviso de Privacidade |
| Titulares | Pessoas com conta no GoblinShift e quem tenta entrar nela (login) |
| Dados | Tipo do evento, resultado, momento, identificador interno da conta (`user_id`), IP truncado (/24 no IPv4, /48 no IPv6, garantido por CHECK no banco) e, no login recusado, um código de referência aleatório. Estado da conta (`active` ou `blocked`) |
| Eventos | Login e falha de login, logout, emissão e revogação de chave de API, recusas de CSRF, reautenticação exigida, negações de autorização, pedido de exclusão, bloqueio e liberação de conta, aceite dos termos |
| Onde | Tabela `audit_event` no PostgreSQL da VPS (Brasil) e, cifrada, no backup (J-25) |
| Prazo | 90 dias, inclusive depois da exclusão da conta (ADR 026); a manutenção apaga em lotes |
| Quem acessa | Só o administrador, por SQL no servidor e pelo `gs-admin` |
| Fontes técnicas | `docs/GoblinShiftPlan.md` 1.8 e 3.6; ADR 026; `src/goblinshift/identity/audit.py` e `purge.py` |

O antiabuso usa também o consumo da conta (execuções, bytes e tempo de executor nas últimas
24 h), mas esse dado já tem base em execução de contrato (cotas, item 4 do Aviso); aqui entra
só o uso dele para decidir um bloqueio.

## 1. Finalidade: o interesse é legítimo e concreto?

- **Interesse:** manter o serviço seguro e disponível para todas as contas: detectar
  tentativa de invasão de conta (falhas de login em série, CSRF recusado), investigar abuso
  (uso do serviço para hospedar ou processar conteúdo ilegal, automação abusiva que esgota a
  capacidade compartilhada) e sustentar a decisão de bloquear uma conta.
- **Situação concreta, não hipotética:** o serviço é aberto, gratuito, com cadastro por Google
  ou GitHub e custo fixo; os Termos proíbem conteúdo ilegal e uso automatizado abusivo e
  publicam um canal de denúncia (`abuso@`). Sem registro, uma denúncia não teria como ser
  verificada nem o bloqueio ser explicado.
- **Licitude:** o interesse não contraria lei; o art. 10, II, cita a proteção do titular em
  relação aos serviços que o beneficiem, o que inclui proteger a conta dele de invasão.

## 2. Necessidade: o dado é o mínimo para isso?

- Nenhum conteúdo, nome de arquivo, token, nome de exibição ou e-mail (o serviço não coleta
  e-mail).
- IP truncado em vez de completo: permite ver rede ou provedor de uma série de tentativas,
  não a máquina da pessoa. Alternativa descartada: guardar o IP completo por menos tempo
  (identifica mais, por um ganho pequeno).
- Só são auditados eventos de quem já tem credencial válida, além do login: um atacante sem
  credencial cai no 401 e não gera registro (ADR 026).
- Prazo de 90 dias: uma investigação de abuso costuma começar dias ou semanas depois do fato
  (denúncia de terceiro). Alternativa descartada: apagar junto com a conta, que perderia o
  rastro justamente de quem exclui a conta para sumir.

## 3. Balanceamento: o interesse prevalece sobre os direitos do titular?

- **Expectativa legítima:** registros de segurança são prática comum em serviços online, e o
  Aviso descreve os eventos, o IP truncado e os 90 dias (itens 3.2 e 5).
- **Impacto:** baixo. O registro não é usado para perfil, publicidade, decisão automatizada
  ou compartilhamento; o bloqueio é decisão humana do administrador. O pior efeito é um
  bloqueio indevido, que tem canal de contestação.
- **Titulares vulneráveis:** o serviço não coleta idade (J-18); nada no registro distingue
  crianças, e nada nele é usado contra elas de forma específica.
- **Depois da exclusão:** o registro fica até 90 dias só com o identificador interno, que,
  sem a linha da conta, já não liga a pessoa a nome de exibição ou provedor.

## 4. Salvaguardas

- Transparência: itens 3.2, 4 e 5 do Aviso.
- Segurança: banco na rede privada da VPS, sem porta pública; acesso só do administrador.
- Minimização imposta pelo banco (CHECK do IP truncado) e por testes (`tests/test_schema_fatia_6.py`,
  `tests/identity/test_audit.py`).
- Expiração automática testada (`identity.purge.delete_old_audit_events`).
- Oposição (art. 18, § 2º): o titular pode pedir pelo `privacidade@`; a resposta proposta é
  avaliar caso a caso, porque atender por padrão anularia a finalidade de segurança.

## Perguntas ao revisor

1. O legítimo interesse é a base adequada, ou a segurança do serviço cabe melhor em outra
   hipótese (por exemplo, exercício regular de direitos, inciso VI, para guardar prova num
   bloqueio contestado)?
2. 90 dias depois da exclusão da conta é proporcional? O plano admite reduzir.
3. Os eventos de quem só tentou entrar (falha de login, sem conta) também precisam de menção
   específica no Aviso?
4. É preciso documento formal separado (relatório de impacto, art. 38) ou este registro basta
   para um agente de pequeno porte?
