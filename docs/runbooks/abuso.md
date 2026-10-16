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
# O hash vai pela entrada padrão (`--user-hash -`), não pela linha de comando: o sudo registra
# a linha de comando no log do sistema, e o hash ali ligaria a conta à telemetria.
owner_id="$(printf '%s' "$user_hash" | gs_admin find-user --user-hash -)" && echo "$owner_id"
```

Saída 1 com "nenhuma conta tem esse user.hash" tem duas causas:

- a conta já foi purgada (`DELETE /me` do titular); a auditoria dela continua na tabela `audit_event` por 90 dias;
- o hash veio de outro ambiente (um evento de teste ou de outra instalação) ou o segredo `GS_USER_HASH_SECRET` foi trocado depois do evento: o mesmo id passa a gerar outro hash, e os eventos antigos deixam de ser traduzíveis.

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

**Código da tela de login recusado** (recurso contra o bloqueio, [termos, item 8](../legal/termos-de-uso.md)). A pessoa cita o `Código do pedido para suporte: <uuid>` que a tela mostrou depois de um login recusado. Ele é o `request_id` do callback, o mesmo UUID do header `X-Request-ID` daquela resposta, e leva ao evento `LOGIN_FAILED` e, quando a recusa veio da conta, à conta. O procedimento completo, com as respostas possíveis, está no [passo 8](#8-recurso-contra-o-bloqueio):

```bash
ref='<uuid citado no e-mail>'
psql_gs -v ref="$ref" <<'SQL'
SELECT user_id, event_type, outcome, occurred_at, ip_prefix
FROM audit_event WHERE request_id = :'ref'::uuid;
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
SELECT event_type, outcome, ip_prefix, occurred_at FROM audit_event
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

O `owner_id` aparece na linha de comando do `block` e, portanto, no log do sudo. Isso é aceitável: o `owner_id` sozinho não liga a conta à telemetria, porque os eventos só trazem o `user.hash`, e o hash depende do segredo do servidor, que não está no log. Quem lê o log do sudo descobre que uma conta foi bloqueada, mas não quais requisições ela fez. O hash, que faz essa ligação, nunca vai para a linha de comando (passo 1).

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
-- bloqueio_us: o horário do bloqueio em microssegundos, para a consulta no OpenObserve abaixo.
SELECT event_type, occurred_at,
       (extract(epoch FROM occurred_at) * 1000000)::bigint AS bloqueio_us
FROM audit_event
  WHERE user_id = :'id' AND event_type = 'ACCOUNT_BLOCKED';          -- uma linha
SQL
```

Uma execução em `running` passa por `stopping` antes de `canceled`: se `execucoes_ativas` for 1, espere um minuto e confira de novo. Se continuar, rode o `block` outra vez.

No OpenObserve, o critério é que o `user_hash` **some** dos eventos: zero linhas com esse hash depois do horário do bloqueio. As requisições recusadas (401) aparecem **sem** `user_hash`, porque o hash só é gravado depois que a credencial autentica, e a credencial de uma conta bloqueada não autentica mais. Stream de traces `default`, com o `bloqueio_us` da consulta acima:

```sql
SELECT COUNT(*) AS eventos_depois_do_bloqueio
FROM "default"
WHERE main = 'true'
  AND user_hash = '<user.hash do passo 1>'
  AND _timestamp >= <bloqueio_us>
```

O resultado esperado é 0. Uma requisição que já tinha autenticado quando o `block` rodou pode aparecer com milissegundos de diferença; se a contagem for maior que zero, repita a consulta com `<bloqueio_us> + 60000000` (um minuto depois): aí ela tem de ser 0. Se não for, alguma credencial ainda autentica: rode o `block` outra vez e confira as contagens do SQL acima.

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

## 8. Recurso contra o bloqueio

Os termos (item 8) pedem que quem acha o bloqueio um engano escreva para `contato@goblinshift.dev` com o código da tela de login e o provedor (Google ou GitHub). Não há e-mail na conta, então o código é a única ligação entre a mensagem e a conta. Ele é o `request_id` do callback, gravado em `audit_event.request_id` (migração 0009; coluna `uuid` anulável, sem índice, porque só o operador consulta por ela, à mão, numa tabela de 90 dias).

**Sem código válido, não confirme nada.** Se a mensagem não traz o código, ou ele não tem o formato de um UUID, peça o código que a tela mostrou e não diga se existe ou não conta, bloqueio ou evento para aquele nome, provedor ou remetente. Quem escreve pode não ser o titular, e confirmar a existência da conta seria enumerá-la.

**1. Achar o evento.** A consulta é a do passo 1 (código da tela de login). Com o `ref` colado do e-mail, tire espaços e quebras de linha: um texto que não é UUID faz o `psql` parar com `invalid input syntax for type uuid`, e isso é o esperado.

- **Nenhuma linha:** o código está errado, é de outro ambiente ou o evento já expirou (a auditoria guarda 90 dias). Peça para a pessoa tentar entrar de novo e mandar o código novo.
- **`user_id` vazio:** a recusa não veio da conta (falha no provedor, `state` inválido ou provedor desligado). Não há bloqueio a rever: a pessoa deve tentar de novo.
- **`event_type` = `LOGIN_SUCCEEDED`:** o login foi aceito. O código não é de uma recusa; peça o código da tela de recusa.
- **`user_id` preenchido num `LOGIN_FAILED`:** a conta foi recusada. Siga para o estado da conta.

**2. Conferir o estado da conta.** O `issuer` confirma o provedor que a pessoa informou; o `subject` nunca é consultado nem copiado para a ficha.

```bash
owner_id='<user_id da consulta acima>'
psql_gs -v id="$owner_id" <<'SQL'
SELECT status, issuer, deletion_requested_at, created_at,
       EXISTS (SELECT 1 FROM web_session WHERE user_id = :'id') AS ja_teve_sessao
FROM app_user WHERE id = :'id';
SELECT event_type, occurred_at FROM audit_event
  WHERE user_id = :'id' AND event_type IN ('ACCOUNT_BLOCKED', 'ACCOUNT_UNBLOCKED')
  ORDER BY occurred_at DESC LIMIT 10;
SQL
```

| Estado | Significado | O que fazer |
|---|---|---|
| Nenhuma linha em `app_user` | A conta já foi purgada (`DELETE /me`); a auditoria sobrevive 90 dias | Não há bloqueio a rever. Responda de forma neutra |
| `deletion_requested_at` preenchido | O titular pediu a exclusão, e o pedido é irrevogável | O `unblock` sai com 1. Explique que a conta está em exclusão, sem dar detalhes de conteúdo |
| `status` = `blocked` e `ja_teve_sessao` falso | Conta pendente do cadastro fechado ([ADR 023](../adr/023-cadastro-fechado-ate-a-abertura.md)), não um bloqueio por abuso | Liberar é aprovar o cadastro: decida pela fila de pendentes (`gs_admin list-users --pending`), não pelo passo 6 por reflexo |
| `status` = `blocked` e `ja_teve_sessao` verdadeiro | Bloqueio de verdade | Revise a ficha do caso (passo 2) e o `ACCOUNT_BLOCKED` acima. Se foi engano, passo 6 |
| `status` = `active` | A conta já foi liberada desde a recusa | Peça para a pessoa entrar de novo |

**3. Responder.** Por `contato@goblinshift.dev`, sem dizer o que a conta fez: "Recebemos o seu pedido e analisamos o caso." Se liberar, diga só que a pessoa pode entrar de novo, e que as sessões e chaves anteriores continuam revogadas. Anote na ficha do caso o `ref`, o `owner_id` e a decisão, nunca o `subject`.

O código também está no evento amplo da telemetria (`request_id` no OpenObserve), mas a telemetria guarda 30 dias e a auditoria, 90.

## Abuso sexual infantil (CSAM)

O procedimento (autoridades e canais, o que preservar, por quanto tempo e como conciliar a preservação com a expiração de 48 horas) **é jurídico e não está escrito aqui**. Ele é o ponto **J-20** dos termos de uso ([`docs/legal/termos-de-uso.md`](../legal/termos-de-uso.md), item 7) e depende de orientação de advogado(a).

Até esse procedimento existir, consulte o(a) advogado(a) antes de qualquer passo deste roteiro.
