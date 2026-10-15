# Roteiro de abuso

Como achar a conta, preservar o mínimo, bloquear, conferir e responder a quem denunciou. Vale para uso abusivo (automação que monopoliza a fila, ataque usando o serviço) e para denúncia de conteúdo ilegal. Base: [plano, 1.8](../GoblinShiftPlan.md#s1-8), termos de uso, itens 6 a 8 ([`docs/legal/termos-de-uso.md`](../legal/termos-de-uso.md)), e [ADR 026](../adr/026-auditoria-sobrevive-a-exclusao.md).

> **Abuso sexual infantil (CSAM):** pare aqui e siga só a seção [Abuso sexual infantil](#abuso-sexual-infantil-csam).

O bloqueio é por CLI no servidor (não há painel web na v1). Todos os comandos abaixo rodam **na VPS**, num shell com sudo.

```bash
# Todo comando do Compose de produção passa pelo deploy/compose.sh (README, "Contas e chaves de API").
compose=(sudo -u goblinshift /srv/goblinshift/app/deploy/compose.sh)
gs_admin() { "${compose[@]}" exec -T api gs-admin "$@"; }
# SQL pela entrada padrão, com os ids em variáveis do psql (:'nome'): nada é colado no texto.
psql_gs() { "${compose[@]}" exec -T postgres psql -U goblinshift -d goblinshift -v ON_ERROR_STOP=1 "$@"; }
```

## 1. Achar a conta

O que chega até você é um destes identificadores. Todos levam ao `owner_id` (o id da conta).

**`user.hash` da telemetria** (alerta, consulta no OpenObserve). O hash é um HMAC do id com o segredo do servidor; só o `gs-admin` o traduz:

```bash
user_hash='<64 caracteres hexadecimais do evento>'
owner_id="$(gs_admin find-user --user-hash "$user_hash")" && echo "$owner_id"
# saída 1 com "nenhuma conta tem esse user.hash": a conta já foi purgada (DELETE /me)
```

Para descobrir **quais** contas pesam mais, no OpenObserve (túnel e acesso em [`deploy/openobserve/README.md`](../../deploy/openobserve/README.md), "Provisionar pelo túnel SSH"), stream de traces `default`, na última hora:

```sql
SELECT user_hash, COUNT(*) AS requisicoes,
       SUM(CASE WHEN http_status_code = '429' THEN 1 ELSE 0 END) AS limitadas
FROM "default"
WHERE main = 'true' AND user_hash IS NOT NULL
GROUP BY user_hash
ORDER BY requisicoes DESC
LIMIT 20
```

**Id de execução** (denúncia que cita uma Run, evento de término):

```bash
run_id='<uuid da execução>'
psql_gs -v id="$run_id" <<'SQL'
SELECT owner_id, status, recipe_id, created_at FROM run WHERE id = :'id';
SQL
```

**Chave de API vista num log** (só o identificador público, `gsk_<public_id>_…`; nunca peça o segredo):

```bash
public_id='<16 dígitos hexadecimais>'
psql_gs -v id="$public_id" <<'SQL'
SELECT owner_id, revoked_at FROM api_key WHERE public_id = :'id';
SQL
```

Guarde o id numa variável para os passos seguintes:

```bash
owner_id='<uuid da conta>'
```

## 2. Preservar a evidência mínima (antes de bloquear)

O bloqueio cancela a execução ativa e o conteúdo expira em 48 horas, então registre **antes**. Evidência mínima é metadado, nunca conteúdo:

```bash
psql_gs -v id="$owner_id" <<'SQL'
SELECT id, issuer, status, created_at, deletion_requested_at FROM app_user WHERE id = :'id';
SELECT id, recipe_id, status, error_code, created_at FROM run
  WHERE owner_id = :'id' ORDER BY created_at DESC LIMIT 50;
SELECT event_type, outcome, ip, occurred_at FROM audit_event
  WHERE user_id = :'id' ORDER BY occurred_at DESC LIMIT 50;
SQL
```

- Copie as saídas para a ficha do caso (fora do repositório e fora da VPS), com a data e o `user.hash`.
- Não registre o `subject` da identidade externa, nomes de arquivo nem conteúdo: o mínimo para justificar o bloqueio é o padrão de uso (quantas execuções, quando, com que resultado).
- Os eventos de auditoria ficam 90 dias, mesmo se a conta for excluída ([ADR 026](../adr/026-auditoria-sobrevive-a-exclusao.md)); a telemetria, 30 dias.

## 3. Bloquear

```bash
gs_admin block --user "$owner_id"
# stderr: "conta <id> bloqueada" e, se havia execução ativa, "execução <id>: canceled"
```

O `block` marca a conta como `blocked`, revoga todas as sessões e chaves, grava `ACCOUNT_BLOCKED` na auditoria e cancela a execução não terminal. Novos logins da mesma identidade (Google ou GitHub) são recusados. Se o comando cair no meio, **rode de novo**: repetir completa o que faltou.

## 4. Conferir

```bash
psql_gs -v id="$owner_id" <<'SQL'
SELECT status FROM app_user WHERE id = :'id';                       -- blocked
SELECT
  (SELECT COUNT(*) FROM web_session WHERE user_id = :'id' AND revoked_at IS NULL) AS sessoes_abertas,
  (SELECT COUNT(*) FROM api_key WHERE owner_id = :'id' AND revoked_at IS NULL) AS chaves_abertas,
  (SELECT COUNT(*) FROM run WHERE owner_id = :'id'
     AND status NOT IN ('succeeded', 'succeeded_with_warnings', 'canceled', 'failed'))
    AS execucoes_ativas;                                             -- 0 | 0 | 0
SELECT event_type, occurred_at FROM audit_event
  WHERE user_id = :'id' AND event_type = 'ACCOUNT_BLOCKED';          -- uma linha
SQL
```

Uma execução em `running` passa por `stopping` antes de `canceled`: se `execucoes_ativas` for 1, espere um minuto e confira de novo. Se continuar, rode o `block` outra vez.

No OpenObserve, as requisições seguintes com o mesmo `user_hash` saem com `http_status_code = '401'`.

O teste `tests/api/auth/test_block_end_to_end.py` prova este roteiro contra o PostgreSQL real: sessão e chave respondem 401, a execução fica `canceled`, o login seguinte é recusado, a auditoria registra o bloqueio e nenhuma credencial fica aberta.

## 5. Exclusão adiada

O bloqueio **não apaga nada**: ele só tira o acesso.

- Uploads, artefatos e saídas expiram sozinhos em 48 horas; não há link público, então nada fica servido a terceiros enquanto isso.
- A conta bloqueada continua existindo, com o estado `blocked`, para que a mesma identidade não volte a entrar. Não há comando de administrador que apague a conta: a exclusão completa é o `DELETE /me` do titular, e uma conta bloqueada não consegue fazê-lo.
- Se a lei ou um pedido do titular exigir apagar a conta, trate como pedido de dados pessoais (aviso de privacidade) e registre a decisão na ficha do caso antes de agir.
- A auditoria do caso fica 90 dias em qualquer cenário.

## 6. Liberar (bloqueio por engano)

```bash
gs_admin unblock --user "$owner_id"
```

Sessões e chaves continuam revogadas: a pessoa entra de novo e emite chaves novas. Conta com exclusão pedida não é liberada (saída 1).

## 7. Responder à denúncia

- Responda pelo canal de denúncias dos termos (item 7) sem dizer quem é a conta nem o que ela guarda: "Recebemos a denúncia, analisamos e tomamos as medidas previstas nos Termos de Uso."
- Prazo de resposta: o que os termos fixarem (ponto J-9, ainda em aberto na revisão jurídica).
- Ordem judicial ou pedido de autoridade: não responda sozinho; siga a orientação jurídica (aviso de privacidade, finalidade "Atender denúncias, ordens e obrigações legais").
- Feche a ficha do caso com: data da denúncia, `user.hash`, `owner_id`, o que foi feito e quando.

## Abuso sexual infantil (CSAM)

O procedimento (autoridades e canais, o que preservar, por quanto tempo e como conciliar a preservação com a expiração de 48 horas) **é jurídico e não está escrito aqui**. Ele é o ponto **J-20** dos termos de uso ([`docs/legal/termos-de-uso.md`](../legal/termos-de-uso.md), item 7) e depende de orientação de advogado(a).

Até esse procedimento existir, consulte o(a) advogado(a) antes de qualquer passo deste roteiro.
