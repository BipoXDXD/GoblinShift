# ADR 041. IP do cliente atrás da Cloudflare, resolvido no site do gateway

- **Estado:** aceito e publicado no gateway em 2026-11-05, por decisão do dono do serviço. A [extensão ao `stillon.tech`](#extensão-ao-stillontech) está no repositório e aguarda a janela de publicação
- **Plano:** [3.8](../GoblinShiftPlan.md#s3-8)
- **Relacionado:** [ADR 014](014-proxy-de-borda-caddy.md) (contrato de confiança entre a borda e os projetos)

## Contexto

Em produção o caminho é Cloudflare → Traefik do Coolify (TCP com TLS passthrough e PROXY protocol v2) → gateway Caddy (`edge-proxy-1`) → Nginx → uvicorn. O Caddy recebe do PROXY protocol o IP de quem falou com o Traefik, que é a borda da Cloudflare, e o `trusted_proxies` da 443 ainda aponta para o IP da VPS aposentada (`179.197.70.102`). Por isso `{client_ip}` vale o IP da borda, o Nginx registrou `172.69.39.0` para um cliente de outra rede, e o `limit_req` de `/auth/*`, a auditoria e o antiabuso contam por borda da Cloudflare, não por cliente.

O conserto tem de cumprir três condições:

1. O IP informado só vale vindo das faixas oficiais da Cloudflare: quem chega direto na origem não forja IP.
2. Cada site do gateway adota a regra por decisão própria: mudar um site não altera o outro. O gateway atende `stillon.tech`, e o Caddy do StillOn confia no que recebe dele.
3. Sem tocar na VPS neste PR.

## Alternativas

| Alternativa | Prós | Contras |
|-------------|------|---------|
| **A. `trusted_proxies` global** com as faixas e `client_ip_headers CF-Connecting-IP` no servidor da 443 | É o recurso próprio do Caddy; `{client_ip}` e os logs do gateway passam a ter o cliente | `trusted_proxies` é opção de servidor, não de site: vale para todos os sites da 443 e muda o que o StillOn recebe. Exige mudar o `Caddyfile` e decidir nos dois repositórios (ADR 014). O arquivo é um bind de arquivo único, então uma troca por renomeação não chega ao processo em execução; aplicar exige [copiar para dentro do container](#mudança-no-caddyfile) ou recriá-lo, com todos os sites juntos |
| **B. Matcher `remote_ip` no site do GoblinShift** (escolhida) | Escopo por site: só quem importa a lista a lê. `Caddyfile` e `gateway.json` ficam intactos. Publica com `caddy reload`, sem recriar o container. O site deixa de depender do `trusted_proxies` global, inclusive do IP aposentado | Mais linhas no site. `{client_ip}` e o log do próprio Caddy continuam com a borda. Um segundo site que queira o mesmo repete o bloco |
| **C. Resolver no Nginx** (`set_real_ip_from` com as faixas e `real_ip_header CF-Connecting-IP`) | Fica no código do projeto e publica com a imagem | Não funciona sozinho: o par do Nginx é sempre o gateway, nunca a Cloudflare. Quem valida a origem precisa ver a conexão de origem, e só o gateway vê. Sem isso, qualquer um forjaria o cabeçalho |
| **D. Módulo `caddy-cloudflare-ip`** | Faixas atualizadas sozinhas | Exige build próprio do Caddy em lugar da imagem oficial por digest, atualiza a confiança sem revisão e continua global como A |

O Traefik não entra na comparação: o roteador é TCP com passthrough, então ele não lê o HTTP.

## Decisão

Alternativa B. Em `docker/gateway/sites/goblinshift.dev.caddy`, o matcher `@cloudflare` exige as duas coisas: conexão vinda de uma faixa da lista e `CF-Connecting-IP` com exatamente um endereço IPv4 ou IPv6. Só nesse caso o `X-Forwarded-For` enviado ao Nginx é o `CF-Connecting-IP`. Em todos os outros, é `{remote_host}`, o IP da conexão depois do PROXY protocol.

- **`CF-Connecting-IP`, não `X-Forwarded-For`:** é um valor só, escrito pela Cloudflare. O `X-Forwarded-For` é uma cadeia que começa no que o cliente mandou.
- **`vars_regexp` sobre o placeholder, não `header_regexp`:** o `header_regexp` aceita o pedido se qualquer linha do cabeçalho casar, e o valor repetido chegaria inteiro ao Nginx. O placeholder junta as linhas com vírgula, e a regex recusa esse valor.
- **Regex estrita de endereço, não classe de caracteres:** `....` ou `deadbeef` passariam por uma classe, o Nginx não os entenderia e o `$remote_addr` viraria o gateway, juntando quem os enviasse num só balde do `limit_req`. Formas que a Cloudflare não envia, como `::ffff:192.0.2.1`, também são recusadas e caem no caso de quem conectou.
- **`{remote_host}`, não `{client_ip}`:** `{client_ip}` obedece ao `trusted_proxies` global, que na data desta decisão ainda tinha o IP aposentado, de onde um `X-Forwarded-For` forjado seria aceito.
- **O `CF-Connecting-IP` para no gateway:** ele é removido do pedido ao Nginx, porque ali já não há como saber quem o escreveu.
- **Lista em `docker/gateway/sites/cloudflare-ips.conf`:** a extensão `.conf` fica fora do glob `*.caddy` do `Caddyfile`, e só os sites `goblinshift.dev` e `stillon.tech` a importam. Como o diretório `sites/` é um bind de diretório, o arquivo novo não exige mudar o `gateway.json`.

O Nginx, o uvicorn e a auditoria não mudam: continuam confiando só no IP fixo do gateway, recebem um IP só e já tratam IPv6.

### Extensão ao `stillon.tech`

O `stillon.tech` tinha o mesmo defeito: com `X-Forwarded-For {client_ip}`, o StillOn recebia a borda da Cloudflare e contava por borda. Em 2026-11-06, `docker/gateway/sites/stillon.tech.caddy` passou a aplicar a regra acima com o mesmo bloco, e só o domínio e o destino (`172.31.0.3:80`) diferem. `lb_try_duration`, `lb_try_interval` e o redirect de `www` continuam.

Fora as linhas `tls` do [ADR 043](043-confianca-no-gateway.md), o arquivo é o mesmo que o StillOn prova na cadeia completa dele, em `deploy/ci/checar-borda-cloudflare.sh`. Nenhuma automação compara os dois repositórios: uma mudança neste site precisa ser levada à cópia de lá.

## Publicação

Nada no repositório trava o `Caddyfile` nem `sites/` por hash. O sha256 do `gateway.json` é o digest da imagem; `preserved-assets.json` cobre só mídias; `receiver-release.json` cobre só o `receiver.py`. As travas `compose_sha256`/`caddy_sha256` do Duora são de arquivos dele, fora do gateway. Se existe outra trava na VPS, este repositório não mostra, e isso se confere no passo 1 de [Depois do merge](#depois-do-merge).

A release da CI também não publica isto: o workflow `coolify.yml` e o receptor só trocam imagens do recurso da aplicação. O gateway é um recurso próprio do Coolify, e nenhum workflow chama o Deploy Webhook dele. O `caddy reload` não interrompe requisições em andamento nem pede janela de manutenção, mas o gateway é compartilhado, então evite-o durante publicações dos outros projetos.

Os comandos usam a API local do Coolify, a mesma do receptor. O UUID do recurso aparece na URL dele no painel. Os campos foram conferidos na especificação OpenAPI atual do Coolify; confira também na versão instalada. O painel é a referência se a API responder diferente ou `null`:

```sh
TOKEN=$(cat /etc/coolify-release/api-token)
API=http://127.0.0.1:8000/api/v1/applications/$UUID
```

**Caminho novo.** O `Caddyfile` e `sites/` saíram de `deploy/coolify/` para `docker/gateway/`, e o `gateway.json` do repositório já aponta para lá. Os binds de um container em execução continuam no caminho da publicação dele, até o recurso ser publicado de novo. Por isso os passos abaixo leem o caminho do host por `docker inspect`, e os arquivos novos vêm do clone em `docker/gateway/`. Enquanto o recurso não for publicado com o caminho novo, o passo 7 de [Depois do merge](#depois-do-merge) e o bloco de [Mudança no `Caddyfile`](#mudança-no-caddyfile) não gravam `git_commit_sha`: mover o commit só faz parte de uma janela que recria o container.

### Antes do merge

Se o recurso do gateway publicar a cada push, o merge recria o container e derruba todos os sites por alguns segundos. O padrão do Coolify para auto-deploy é ligado, então isto **bloqueia o merge** até ser respondido:

1. Ler a configuração em Advanced > Deployment > Auto deploy, ou por `curl -fsS -H "Authorization: Bearer $TOKEN" "$API" | jq '{git_branch, git_commit_sha, watch_paths, settings}'`.
2. Comparar `docker inspect -f '{{.State.StartedAt}}' edge-proxy-1` com a data do último merge na `main`. Um container que reiniciou logo depois de um merge é sinal de auto-deploy, mesmo que a leitura acima diga outra coisa.
3. Decidir:

| O que apareceu | O que fazer |
|----------------|-------------|
| "Manual deployments only" (`is_auto_deploy_enabled` falso) | Seguir para o merge |
| "Deploy on push" (`is_auto_deploy_enabled` verdadeiro) | Mudar para manual **antes** do merge, repetir os passos 1 e 2 e só então fazer o merge. Pela API: `curl -fsS -X PATCH -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d '{"is_auto_deploy_enabled":false}' "$API"` |
| Não deu para saber | Não fazer o merge |

Watch Paths não é saída: esta mudança toca justamente `docker/gateway/sites/`. Depois de desligado, o auto-deploy fica desligado, porque a configuração do gateway é publicada à mão (veja [`deploy/coolify/release/README.md`](../../deploy/coolify/release/README.md)).

### Depois do merge

Os passos rodam na VPS. `$COMMIT` é o commit de merge, copiado da página do PR no GitHub.

1. Descobrir a origem do bind e procurar travas do gateway. Se o `grep` achar algo, atualizar a trava junto com os arquivos:
   ```sh
   SITES=$(docker inspect edge-proxy-1 --format '{{range .Mounts}}{{if eq .Destination "/etc/caddy/conf.d"}}{{.Source}}{{end}}{{end}}')
   echo "$SITES"
   grep -rlE 'goblinshift\.dev\.caddy|stillon\.tech\.caddy|deploy/coolify/sites|docker/gateway/sites' /etc/coolify-release /var/lib/coolify-release /etc/goblinshift 2>/dev/null
   ```
2. Obter os bytes aprovados de um clone novo do repositório público, fora do checkout do Coolify, e confirmar que o commit está na `main`:
   ```sh
   CLONE=$(mktemp -d /root/goblinshift-041.XXXXXX)
   git clone --quiet https://github.com/BipoXDXD/GoblinShift.git "$CLONE"
   git -C "$CLONE" merge-base --is-ancestor "$COMMIT" origin/main
   git -C "$CLONE" checkout --quiet "$COMMIT"
   ```
3. Guardar a configuração atual **fora do checkout**. `$SITES` fica dentro do checkout do Coolify, e um Redeploy ou um `git clean` apagaria uma cópia guardada ao lado:
   ```sh
   BACKUP=/root/gateway-backup-pre-041
   install -d -m 700 "$BACKUP"
   cp -a "$SITES/." "$BACKUP/"
   ```
4. Confira antes os arquivos do [ADR 043](043-confianca-no-gateway.md) no volume. Instalar os dois arquivos e conferir os bytes, sem editar no host:
   ```sh
   for arquivo in cloudflare-ips.conf goblinshift.dev.caddy; do
     install -m 644 "$CLONE/docker/gateway/sites/$arquivo" "$SITES/$arquivo"
     cmp "$CLONE/docker/gateway/sites/$arquivo" "$SITES/$arquivo"
   done
   ```
5. Validar a partir do `Caddyfile` do host, nunca o de `/etc/caddy`: o container pode estar preso a uma versão antiga, e recarregá-la desfaria a última [mudança no `Caddyfile`](#mudança-no-caddyfile):
   ```sh
   ORIGEM=$(docker inspect edge-proxy-1 --format '{{range .Mounts}}{{if eq .Destination "/etc/caddy/Caddyfile"}}{{.Source}}{{end}}{{end}}')
   docker cp "$ORIGEM" edge-proxy-1:/tmp/Caddyfile.novo
   docker exec edge-proxy-1 caddy validate --config /tmp/Caddyfile.novo --adapter caddyfile
   ```
6. Aplicar: `docker exec edge-proxy-1 caddy reload --config /tmp/Caddyfile.novo --adapter caddyfile`, depois `docker exec edge-proxy-1 rm -f /tmp/Caddyfile.novo`.
7. Confira antes os arquivos do [ADR 043](043-confianca-no-gateway.md) no volume. Alinhar o recurso do gateway ao commit, para um Redeploy futuro não devolver o bug. Alinhar é só gravar o campo; quem recria o container é o Redeploy ou o `instant_deploy`, e nenhum dos dois entra aqui:
   - **Fora de janela, não mova `git_commit_sha`** enquanto o recurso não tiver sido publicado com `docker/gateway/`: o campo fica como está, e até a janela um Redeploy devolve a configuração antiga. O restante deste passo vale só dentro de uma janela ou depois dela.
   - Se `git_commit_sha` for `HEAD`, não há o que gravar: o próximo Redeploy clona a `main`, com os mesmos bytes.
   - Se for um commit fixo, gravar o novo:
     ```sh
     curl -fsS -X PATCH -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
       -d "{\"git_commit_sha\":\"$COMMIT\"}" "$API"
     ```
   - Conferir que `docker inspect -f '{{.State.StartedAt}}' edge-proxy-1` não mudou. Se o painel mostrar um Redeploy pendente, não clicar.
8. Conferir: de uma rede conhecida, `curl -sS -o /dev/null -w '%{http_code}\n' https://goblinshift.dev/livez` deve dar `200`, e o log do Nginx deve trazer o /24 daquela rede em `client`, não `172.69.x.0`. `https://stillon.tech` deve responder como antes.
9. Remover o clone: `rm -rf "$CLONE"`. O `$BACKUP` fica até a correção ser dada como estável.

**Reversão:** `cp -a "$BACKUP/." "$SITES/"` e repetir os passos 5 e 6. O `cloudflare-ips.conf` que sobrar não faz efeito, porque nada mais o importa. Se o `git_commit_sha` foi alterado, gravar de volta o valor lido antes do merge. No `reload`, trecho inválido não derruba nada: ele recusa e a configuração anterior fica no ar. Isso não vale para recriar ou reiniciar o container, que não tem configuração anterior a manter e não sobe com a inválida (ADR 014).

### Publicar o site do StillOn

O merge da [extensão](#extensão-ao-stillontech) não publica nada: o gateway segue no commit fixo até a janela de publicação. Confira antes os arquivos do [ADR 043](043-confianca-no-gateway.md) no volume. Nela, repita [Depois do merge](#depois-do-merge) só com o `stillon.tech.caddy`, porque o `cloudflare-ips.conf` já está no gateway, e com um `BACKUP` próprio no passo 3. No passo 8, `https://stillon.tech/healthz` deve dar `200`, e `https://goblinshift.dev/livez` deve responder como antes.

### Mudança no `Caddyfile`

Os passos acima só cobrem `sites/`. O `Caddyfile` é um bind de arquivo único e somente leitura, e o Git do Coolify troca o inode dele: em 2026-11-05 o `mountinfo` do container mostrava `Caddyfile//deleted`, com o gateway preso desde 21/10 a um arquivo apagado no host. Sobrescrever o host, portanto, não chega ao processo. Recriar o container resolveria, mas derruba os sites do gateway.

Por isso o bloco valida e aplica uma cópia dentro do container e só depois atualiza o host, que é o que o Docker monta num restart ou numa recriação. Assim, um arquivo inválido não muda nada, e o `/etc/caddy/Caddyfile` visto dentro do container pode continuar antigo até o próximo restart: a conferência é a configuração em execução, na API de administração, salva pelo próprio bloco. O `reload` não interrompe requisições em andamento; WebSocket e SSE não foram medidos.

Salve o bloco num arquivo na VPS e rode `COMMIT=<commit de merge> API=$API TOKEN=$TOKEN bash arquivo`, nunca colado na sessão: o `set -e` fecharia o SSH. `$API` e `$TOKEN` são os do passo 7 e do Antes do merge. Se o hash do arquivo do host aparecer numa trava, o bloco para: atualize a trava e rode de novo.

```bash
set -euo pipefail
: "${COMMIT:?}" "${API:?}" "${TOKEN:?}"
ORIGEM=$(docker inspect edge-proxy-1 --format '{{range .Mounts}}{{if eq .Destination "/etc/caddy/Caddyfile"}}{{.Source}}{{end}}{{end}}')
test -s "$ORIGEM"
if grep -rlsq "$(sha256sum "$ORIGEM" | cut -d' ' -f1)" \
    /etc/coolify-release /var/lib/coolify-release /etc/goblinshift /etc/duora; then
  echo "trava do Caddyfile encontrada" >&2
  exit 1
fi

CLONE=$(mktemp -d /root/goblinshift-caddyfile.XXXXXX)
echo "CLONE=$CLONE"
git clone --quiet https://github.com/BipoXDXD/GoblinShift.git "$CLONE"
git -C "$CLONE" merge-base --is-ancestor "$COMMIT" origin/main || { echo "commit fora da main" >&2; exit 1; }
git -C "$CLONE" checkout --quiet "$COMMIT"
NOVO=$CLONE/docker/gateway/Caddyfile
test -s "$NOVO"

BACKUP=/root/gateway-backup-caddyfile-$(date -u +%Y%m%dT%H%M%SZ)
ANTERIOR=$(curl -fsS -H "Authorization: Bearer $TOKEN" "$API" | jq -r .git_commit_sha)
[[ "$ANTERIOR" =~ ^([0-9a-f]{7,40}|HEAD)$ ]] || { echo "git_commit_sha inesperado: $ANTERIOR" >&2; exit 1; }
cp -a "$ORIGEM" "$BACKUP"
echo "$ANTERIOR" > "$BACKUP.commit"
docker exec edge-proxy-1 cat /etc/caddy/Caddyfile > "$BACKUP.em-uso"
echo "ORIGEM=$ORIGEM"
echo "BACKUP=$BACKUP"

docker cp "$NOVO" edge-proxy-1:/tmp/Caddyfile.novo
docker exec edge-proxy-1 caddy validate --config /tmp/Caddyfile.novo --adapter caddyfile
docker exec edge-proxy-1 caddy reload --config /tmp/Caddyfile.novo --adapter caddyfile
cp "$NOVO" "$ORIGEM"
cmp "$NOVO" "$ORIGEM"
docker exec edge-proxy-1 rm -f /tmp/Caddyfile.novo
docker exec edge-proxy-1 wget -qO- http://127.0.0.1:2019/config/ > "$BACKUP.config.json"
echo "configuração em execução salva em $BACKUP.config.json"
```

Se o bloco parar antes do `reload`, a produção não mudou; remova o `CLONE` impresso. Para reverter depois, use os caminhos impressos: copie `$BACKUP.em-uso` para `/tmp/Caddyfile.novo` do container, repita o `validate` e o `reload`, e rode `cp "$BACKUP" "$ORIGEM"`. Com o bloco terminado, alinhe o `git_commit_sha` e confira como nos passos 7 e 8, depois remova o `CLONE`. Reverter depois do alinhamento inclui gravar de volta o commit de `$BACKUP.commit`.

**Fora de janela, não mova `git_commit_sha`** enquanto o recurso não tiver sido publicado com `docker/gateway/`. O bloco acima muda o `Caddyfile` do host e a configuração em execução, mas o commit fixo do recurso continua o antigo: até a janela, um Redeploy devolve a configuração antiga.

## Atualizar a lista

A Cloudflare publica as faixas em <https://www.cloudflare.com/ips-v4> e <https://www.cloudflare.com/ips-v6>. O workflow `cloudflare-ips` as compara com a lista todo dia, com as mesmas validações do script, e abre uma issue se divergirem. Para atualizar:

1. `uv run python -m scripts.cloudflare_ips` regrava `cloudflare-ips.conf`. O script recusa lista vazia, família errada, faixa não pública ou mais larga que /8 (IPv4) ou /16 (IPv6), e nesse caso não grava nada.
2. Revisar o diff e abrir PR: a lista é decisão de confiança, por isso não se atualiza sozinha.
3. Repetir [Antes do merge](#antes-do-merge) e, depois do merge, [Depois do merge](#depois-do-merge), só com o `cloudflare-ips.conf`.

Enquanto a lista estiver atrasada, uma faixa nova da Cloudflare cai no caso de quem conectou: o cliente aparece como a borda, como antes deste ADR. É perda de precisão, não abertura.

## Consequências

- `limit_req`, auditoria e antiabuso passam a contar por cliente real, e o mesmo vale para os limites do StillOn quando a extensão for publicada. Quem hoje divide uma borda com muitos clientes deixa de dividir a cota; clientes atrás do mesmo NAT continuam dividindo.
- O `default.conf.template` conta o `limit_req` por endereço em IPv4 e por /64 em IPv6, regra que só vale na produção depois da publicação da imagem do Nginx que a contém: um cliente com um /64 inteiro tem muitos endereços e, contado por endereço, escaparia do limite trocando de endereço. O IPv4 mapeado em IPv6 conta como o próprio IPv4. A chave sai dos bytes do endereço (`map` sobre `$binary_remote_addr` em `default.conf.template`), então a grafia do IPv6 não muda o balde, e `tests/test_nginx_auth_limit.py` cobre isso no Nginx real. Clientes do mesmo /64 dividem a cota, como atrás de um NAT, e quem controla um prefixo maior que /64 ainda tem vários baldes.
- **Risco aceito:** as faixas são da Cloudflare, não desta conta. Outro cliente da Cloudflare pode apontar uma zona dele para a origem, mas o `CF-Connecting-IP` continua escrito pela Cloudflare, então ele só informa o próprio IP. Fechar esse caminho exige Authenticated Origin Pulls, que fica fora deste ADR.
- **Resolvido em 2026-11-05:** o `trusted_proxies` global tinha o IP da VPS aposentada, cancelada e sujeita a reatribuição, e um `X-Forwarded-For` forjado vindo dele valia para quem usava `{client_ip}`, como o `stillon.tech` antes da extensão. O dono autorizou a remoção: no `Caddyfile`, o servidor da 443 ficou sem proxy confiável, e o das demais portas, só com o Traefik. Em produção isso só vale depois da [publicação do `Caddyfile`](#mudança-no-caddyfile). O `{remote_host}` dos sites continua como está.
- `deploy/edge/` é o caminho anterior ao Coolify e fica como está: não está no fluxo de produção descrito em [`deploy/coolify/README.md`](../../deploy/coolify/README.md).

## Compliance

- `tests/test_gateway_client_ip.py` sobe a imagem do `gateway.json` com a configuração versionada e simula cada origem por PROXY protocol v2, como o Traefik: IP válido da faixa em IPv4 e IPv6, IP forjado de fora da faixa, fronteira da faixa, VPS aposentada, cabeçalho ausente, repetido ou inválido e toda forma comprimida de IPv6. As provas de origem e de cabeçalho rodam em `goblinshift.dev` e `stillon.tech`, o contrato exige a mesma regra nos dois sites, e o redirect de `www.stillon.tech` é conferido no gateway real.
- `tests/test_cloudflare_ips_script.py` cobre a validação da lista e o formato do arquivo versionado.
- `tests/test_nginx_auth_limit.py` confere que um IPv6 informado pela borda chega à API pelo Nginx real.
