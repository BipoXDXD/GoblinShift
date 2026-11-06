# ADR 044. Espera no gateway durante a troca de containers

- **Estado:** aceito; ainda não publicado no gateway ([Publicação](#publicação))
- **Plano:** [3.8](../GoblinShiftPlan.md#s3-8)
- **Relacionado:** [ADR 014](014-proxy-de-borda-caddy.md) (proxy de borda), [ADR 041](041-ip-do-cliente-atras-da-cloudflare.md) (publicação do gateway)

## Contexto

A publicação da aplicação remove os containers antes de subir os novos. Até o Nginx voltar, o gateway não tem para onde mandar o pedido: repetia a conexão por pouco tempo e devolvia 502 com corpo vazio. Quem usava o site nesse intervalo via um erro, inclusive em operações que dariam certo se esperassem um pouco.

## Alternativas

| Alternativa | Prós | Contras |
|-------------|------|---------|
| **A. Só trocar o 502 por 503** | Resposta imediata em queda real | O pedido continua falhando em toda publicação |
| **B. Esperar mais e responder 503 no fim** (escolhida) | A troca vira espera, em qualquer método, sem peça nova | Queda real responde devagar |
| **C. Repetir também pedido que chegou ao Nginx** (`lb_retry_match`) | Cobre pedido em voo | Executa duas vezes um POST não idempotente |
| **D. Subir a versão nova antes de parar a antiga** | Sem espera nenhuma | Exige dois conjuntos de containers e muda o receptor compartilhado e o contrato do ADR 014 |

## Decisão

Alternativa B, só em `docker/gateway/sites/goblinshift.dev.caddy`. O site do StillOn não muda.

- **Espera:** `lb_try_duration 25s`, abaixo do limite de leitura da Cloudflare. Como a última tentativa começa antes do fim do prazo, o tempo de conexão do Caddy pode se somar a ele, e a soma precisa caber no prazo de 30 s da SPA (`web/src/api/client.ts`). Sem essa folga, o front veria o próprio timeout, nunca o 503.
- **Fim da espera:** `handle_errors` responde 503 com texto fixo, `Retry-After: 30` e `Cache-Control: no-store`, sem o cabeçalho `Server`. O texto não diz causa nem previsão. O `Retry-After` fica maior que o prazo de propósito: repetir antes só poria outro pedido em espera.
- **Só o erro do proxy:** `handle_errors` trata o que o próprio gateway não conseguiu entregar. Uma resposta 4xx ou 5xx do Nginx ou da API passa como veio, com o problem+json da API.

### Como um pedido não é reenviado

A garantia vem de três pontos do código do proxy, e `tests/test_gateway_upstream_wait.py` a confere contra o gateway real:

1. **Caddy:** depois de uma falha, um método diferente de GET só é repetido se o erro for de conexão (`DialError`), ou seja, se o TCP não se estabeleceu e nada foi enviado. Qualquer erro depois da conexão conta como pedido recebido. Por isso o site não usa `lb_retry_match` nem `lb_retries`: o primeiro amplia essa regra.
2. **Transporte do Go, usado pelo Caddy:** ele repete sozinho só o que não escreveu na conexão, ou o pedido sem corpo que se declara repetível, como GET ou POST vazio com `Idempotency-Key`. Um POST com corpo não entra nesse caso, com ou sem a chave.
3. **Conexão aceita pelo sistema operacional e perdida antes do Nginx ler:** para o Caddy, a conexão se estabeleceu. Vale o ponto 1: o pedido não é repetido e o cliente recebe 503.

O GET pode ser repetido mesmo depois de chegar ao Nginx, porque é o padrão do Caddy para um método idempotente.

## Consequências

- Uma queda real demora o prazo inteiro para responder, em vez de falhar logo. A troca de `fail fast` por espera é aceita com o volume atual e deve ser revista com carga.
- Um pedido em voo no instante da parada ainda falha, agora com 503. Se ele alterava estado, pode ter sido executado: repetir com segurança depende do `Idempotency-Key`.
- A espera só cobre troca menor que o prazo. Acima dele, o cliente recebe o 503.
- Cliente com timeout menor que a espera vê o próprio timeout. Um POST abandonado assim ainda pode ser entregue quando o Nginx volta, e o reenvio sem `Idempotency-Key` pode executar duas vezes. Isso vale se a conexão de origem for HTTP/1.1, o que não foi confirmado.
- Não há limite de pedidos em espera. O teto real é o `mem_limit` do gateway, compartilhado com o outro site, e o consumo não foi medido.
- A Cloudflare tem limite próprio de escrita de 30 s, separado do de leitura, e ele continua valendo para o que passa pela espera.
- Nginx de pé com a API fora continua respondendo o erro do próprio Nginx. Esse caso não passa pela espera.
- `deploy/edge/` é o caminho anterior ao Coolify e fica como está (ADR 041).

## Publicação

O merge não muda a produção. A release da CI só troca imagens, e o gateway é um recurso próprio do Coolify, publicado à mão. Vale o processo do [ADR 041](041-ip-do-cliente-atras-da-cloudflare.md#depois-do-merge), só com o arquivo `goblinshift.dev.caddy`. O `reload` não recria o container nem depende do caminho do bind, que os passos leem do próprio container.

Enquanto isso não acontecer, o site continua com a espera anterior e o 502. Depois, um Redeploy com o commit antigo devolve a configuração anterior, como descreve o ADR 041.

## Compliance

`tests/test_gateway_upstream_wait.py` sobe a imagem do `gateway.json` com a configuração versionada e um destino descartável com IP fixo, e confere:

- pedidos que chegam com o destino fora terminam em 200 no container novo, com o corpo inteiro e entregues uma vez;
- esgotado o prazo, GET e POST recebem o 503, com o conjunto exato de cabeçalhos e o texto fixo;
- POST, PUT, PATCH e DELETE que chegaram ao destino não são reenviados, numa conexão reaproveitada, com ou sem `Idempotency-Key`;
- o erro da aplicação passa sem troca, e o `stillon.tech` mantém o comportamento dele.
