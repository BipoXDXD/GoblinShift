# ADR 041. IP do cliente atrás da Cloudflare, resolvido no site do gateway

- **Estado:** proposto (2026-11-05)
- **Plano:** [3.8](../GoblinShiftPlan.md#s3-8)
- **Relacionado:** [ADR 014](014-proxy-de-borda-caddy.md) (contrato de confiança entre a borda e os projetos)

## Contexto

Em produção o caminho é Cloudflare → Traefik do Coolify (TCP com TLS passthrough e PROXY protocol v2) → gateway Caddy (`edge-proxy-1`) → Nginx → uvicorn. O Caddy recebe do PROXY protocol o IP de quem falou com o Traefik, que é a borda da Cloudflare, e o `trusted_proxies` da 443 ainda aponta para o IP da VPS aposentada (`179.197.70.102`). Por isso `{client_ip}` vale o IP da borda, o Nginx registrou `172.69.39.0` para um cliente de outra rede, e o `limit_req` de `/auth/*`, a auditoria e o antiabuso contam por borda da Cloudflare, não por cliente.

O conserto tem de cumprir três condições:

1. O IP informado só vale vindo das faixas oficiais da Cloudflare: quem chega direto na origem não forja IP.
2. Os outros sites do gateway não mudam. O gateway atende `stillon.tech`, e o Caddy do StillOn confia no que recebe dele.
3. Sem tocar na VPS neste PR.

## Alternativas

| Alternativa | Prós | Contras |
|-------------|------|---------|
| **A. `trusted_proxies` global** com as faixas e `client_ip_headers CF-Connecting-IP` no servidor da 443 | É o recurso próprio do Caddy; `{client_ip}` e os logs do gateway passam a ter o cliente | `trusted_proxies` é opção de servidor, não de site: vale para todos os sites da 443 e muda o que o StillOn recebe. Exige mudar o `Caddyfile` e decidir nos dois repositórios (ADR 014). O arquivo é um bind de arquivo único, então uma troca por renomeação só chega ao container recriando-o, com todos os sites juntos |
| **B. Matcher `remote_ip` no site do GoblinShift** (escolhida) | Escopo por site: só `goblinshift.dev` lê a lista. `Caddyfile` e `gateway.json` ficam intactos. Publica com `caddy reload`, sem recriar o container. O site deixa de depender do `trusted_proxies` global, inclusive do IP aposentado | Mais linhas no site. `{client_ip}` e o log do próprio Caddy continuam com a borda. Um segundo site que queira o mesmo repete o bloco |
| **C. Resolver no Nginx** (`set_real_ip_from` com as faixas e `real_ip_header CF-Connecting-IP`) | Fica no código do projeto e publica com a imagem | Não funciona sozinho: o par do Nginx é sempre o gateway, nunca a Cloudflare. Quem valida a origem precisa ver a conexão de origem, e só o gateway vê. Sem isso, qualquer um forjaria o cabeçalho |
| **D. Módulo `caddy-cloudflare-ip`** | Faixas atualizadas sozinhas | Exige build próprio do Caddy em lugar da imagem oficial por digest, atualiza a confiança sem revisão e continua global como A |

O Traefik não entra na comparação: o roteador é TCP com passthrough, então ele não lê o HTTP.

## Decisão

Alternativa B. Em `deploy/coolify/sites/goblinshift.dev.caddy`, o matcher `@cloudflare` exige as duas coisas: conexão vinda de uma faixa da lista e `CF-Connecting-IP` com formato de IP. Só nesse caso o `X-Forwarded-For` enviado ao Nginx é o `CF-Connecting-IP`. Em todos os outros, é `{remote_host}`, o IP da conexão depois do PROXY protocol.

- **`CF-Connecting-IP`, não `X-Forwarded-For`:** é um valor só, escrito pela Cloudflare. O `X-Forwarded-For` é uma cadeia que começa no que o cliente mandou.
- **`{remote_host}`, não `{client_ip}`:** `{client_ip}` obedece ao `trusted_proxies` global, e nele ainda está o IP aposentado, de onde um `X-Forwarded-For` forjado seria aceito.
- **O `CF-Connecting-IP` para no gateway:** ele é removido do pedido ao Nginx, porque ali já não há como saber quem o escreveu.
- **Lista em `deploy/coolify/sites/cloudflare-ips.conf`:** a extensão `.conf` fica fora do glob `*.caddy` do `Caddyfile`, e só o site do GoblinShift a importa. Como o diretório `sites/` é um bind de diretório, o arquivo novo não exige mudar o `gateway.json`.

O Nginx, o uvicorn e a auditoria não mudam: continuam confiando só no IP fixo do gateway, recebem um IP só e já tratam IPv6.

## Publicação

Nada no repositório trava o `Caddyfile` nem `sites/` por hash. O sha256 do `gateway.json` é o digest da imagem; `preserved-assets.json` cobre só mídias; `receiver-release.json` cobre só o `receiver.py`. As travas `compose_sha256`/`caddy_sha256` do Duora são de arquivos dele, fora do gateway. Se existe outra trava na VPS, este repositório não mostra, e isso se confere no passo 1 abaixo.

A release da CI também não publica isto: o workflow `coolify.yml` e o receptor só trocam imagens do recurso da aplicação. O gateway é um recurso próprio do Coolify e a configuração dele fica no commit de infraestrutura revisado. A publicação é administrativa, depois do merge. O `caddy reload` não derruba conexões nem pede janela de manutenção, mas o gateway é compartilhado, então evite-o durante publicações dos outros projetos.

Na VPS, com `$COMMIT` igual ao commit da `main` com esta mudança:

1. Descobrir a origem do bind e procurar travas do gateway. Se o `grep` achar algo, atualizar a trava junto com os arquivos:
   ```sh
   SITES=$(docker inspect edge-proxy-1 --format '{{range .Mounts}}{{if eq .Destination "/etc/caddy/conf.d"}}{{.Source}}{{end}}{{end}}')
   echo "$SITES"
   grep -rlE 'goblinshift\.dev\.caddy|deploy/coolify/sites' /etc/coolify-release /var/lib/coolify-release /etc/goblinshift 2>/dev/null
   ```
2. Guardar a configuração atual: `cp -a "$SITES" "$SITES.pre-041"`.
3. Instalar os dois arquivos do commit e conferir os bytes com o `git show` do commit, sem editar no host:
   `deploy/coolify/sites/cloudflare-ips.conf` e `deploy/coolify/sites/goblinshift.dev.caddy`.
4. Validar: `docker exec edge-proxy-1 caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile`.
5. Aplicar: `docker exec edge-proxy-1 caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile`.
6. Alinhar o recurso do gateway no Coolify ao mesmo commit, **sem** Redeploy agora. Se ele ficar no commit antigo, um Redeploy futuro devolve o bug. Um Redeploy recria o container e derruba todos os sites por alguns segundos.
7. Conferir: de uma rede conhecida, um `curl -sS -o /dev/null -w '%{http_code}\n' https://goblinshift.dev/livez` deve dar `200`, e o log do Nginx deve trazer o /24 daquela rede em `client`, não `172.69.x.0`. `https://stillon.tech` deve responder como antes.

**Reversão:** devolver os arquivos de `"$SITES.pre-041"` e repetir os passos 4 e 5. O `cloudflare-ips.conf` que sobrar não faz efeito, porque nada mais o importa. Trecho inválido não derruba nada: o `reload` recusa e a configuração anterior fica no ar (ADR 014).

## Atualizar a lista

A Cloudflare publica as faixas em <https://www.cloudflare.com/ips-v4> e <https://www.cloudflare.com/ips-v6>. Para atualizar:

1. `uv run python -m scripts.cloudflare_ips` regrava `cloudflare-ips.conf`. O script recusa lista vazia, família errada, faixa não pública ou mais larga que /8 (IPv4) ou /16 (IPv6), e nesse caso não grava nada.
2. Revisar o diff e abrir PR: a lista é decisão de confiança, por isso não se atualiza sozinha.
3. Depois do merge, repetir os passos 2 a 7 de [Publicação](#publicação), só com o `cloudflare-ips.conf`.

Enquanto a lista estiver atrasada, uma faixa nova da Cloudflare cai no caso de quem conectou: o cliente aparece como a borda, como antes deste ADR. É perda de precisão, não abertura.

## Consequências

- `limit_req`, auditoria e antiabuso passam a contar por cliente real. Quem hoje divide uma borda com muitos clientes deixa de dividir a cota; clientes atrás do mesmo NAT continuam dividindo.
- O `limit_req` conta por endereço. Em IPv6, um cliente com um /64 inteiro tem muitos endereços, então o limite é mais fraco que em IPv4. Já era assim para quem chegava direto; agora vale também para quem vem pela Cloudflare. Agrupar por prefixo fica para outra decisão.
- **Risco aceito:** as faixas são da Cloudflare, não desta conta. Outro cliente da Cloudflare pode apontar uma zona dele para a origem, mas o `CF-Connecting-IP` continua escrito pela Cloudflare, então ele só informa o próprio IP. Fechar esse caminho exige Authenticated Origin Pulls, que fica fora deste ADR.
- **Pendência fora do escopo:** o `trusted_proxies` global continua com o IP da VPS aposentada, e um `X-Forwarded-For` forjado vindo desse IP ainda vale para quem usa `{client_ip}`, como o `stillon.tech`. Removê-lo muda o `Caddyfile` compartilhado e precisa de decisão conjunta (ADR 014).
- `deploy/edge/` é o caminho anterior ao Coolify e fica como está: não está no fluxo de produção descrito em [`deploy/coolify/README.md`](../../deploy/coolify/README.md).

## Compliance

- `tests/test_gateway_client_ip.py` sobe a imagem do `gateway.json` com a configuração versionada e simula cada origem por PROXY protocol v2, como o Traefik: IP válido da faixa em IPv4 e IPv6, IP forjado de fora da faixa, fronteira da faixa, VPS aposentada, cabeçalho ausente ou inválido, e o `stillon.tech` sem mudança.
- `tests/test_cloudflare_ips_script.py` cobre a validação da lista e o formato do arquivo versionado.
- `tests/test_nginx_auth_limit.py` confere que um IPv6 informado pela borda chega à API pelo Nginx real.
