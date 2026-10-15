# ADR 014. Proxy de borda do host em Caddy, compartilhado pelos projetos

- **Estado:** aceito (2026-10-13); domínio do GoblinShift acrescentado em 2026-10-14; acesso pelo IP retirado em 2026-10-15
- **Plano:** [3.8](../GoblinShiftPlan.md#s3-8)
- **Relacionado:** [ADR 011](011-vps-kvm2-compartilhada.md); no outro projeto, a spec `2026-10-13-vps-compartilhada` do StillOn

## Contexto

A VPS é compartilhada com outro projeto do mesmo dono, o StillOn ([ADR 011](011-vps-kvm2-compartilhada.md)), e só um processo pode ouvir nas portas 80 e 443. A borda foi instalada em `/srv/edge` com Nginx, só na porta 80 e sem TLS, deixando a tecnologia do TLS para quando houvesse o primeiro domínio. O primeiro domínio chegou: `stillon.tech`, do outro projeto. O do GoblinShift, `goblinshift.dev`, veio depois (2026-10-14), no mesmo IP da VPS e com CAA só para o Let's Encrypt. Cada projeto entra na rede Docker compartilhada `edge` com o seu ponto de entrada, e os projetos confiam nos cabeçalhos `X-Forwarded-*` só quando vêm do IP fixo da borda.

## Decisão

A borda é um Caddy (`caddy:2` por digest) em `/srv/edge`, infraestrutura do host e não de um projeto, com TLS automático (Let's Encrypt) por domínio. O contrato de que os projetos dependem:

| Item | Valor |
|------|-------|
| Rede | `edge`, `172.31.0.0/24`; IPs dinâmicos só em `172.31.0.128/25` |
| IPs reservados | `.2` a borda, `.3` o Caddy do StillOn, `.4` o Nginx do GoblinShift |
| Confiança | a borda **não** tem `trusted_proxies`: troca o `X-Forwarded-For` do cliente pelo IP de quem conectou |
| Protocolos | HTTP/1.1 e HTTP/2; a 443/udp (HTTP/3) não é publicada |
| Configuração | `Caddyfile` só com opções globais e `import /etc/caddy/conf.d/*.caddy`; cada projeto entrega `conf.d/<projeto>.caddy` e nunca sobrescreve o de outro |
| Destino | o trecho de cada projeto aponta para o IP reservado dele, nunca para um alias |
| Domínios | `stillon.tech` no trecho do StillOn; `goblinshift.dev` no do GoblinShift, com TLS e redirect http → https automáticos |
| Acesso pelo IP | nenhum: o trecho do GoblinShift não tem `:80`, então `http://<IP>` não chega a ele e a chave de API não trafega em texto claro. A aceitação e o caos usam `https://goblinshift.dev` |

IP, e não alias, porque um segundo container com o mesmo alias dividiria o tráfego em silêncio (logins do outro projeto poderiam chegar a ele); com o mesmo IP, o Docker recusa. Mudar o contrato é decisão do dono nos dois repositórios.

## Alternativas descartadas

| Alternativa | Motivo |
|-------------|--------|
| Nginx com certbot | Renovação por cron e reload externos, com falha de renovação silenciosa |
| Cada projeto publicar a própria porta | Só um processo ouve na 80/443 |
| O Nginx do GoblinShift atender também o outro projeto | Um deploy de um projeto derrubaria o outro |
| Destino por alias (`goblinshift-web`) | Alias duplicado reparte o tráfego sem aviso |
| TLS mútuo entre a borda e os projetos | Fecharia o risco abaixo, ao custo de uma CA própria e rotação de certificados |

## Consequências

- Os certificados ficam no volume `caddy_data`; perder o volume é reemitir, com o limite semanal do Let's Encrypt.
- Um trecho inválido impede o `caddy reload`, mas a configuração anterior continua no ar; todo reload é precedido de `caddy validate`.
- Um projeto parado não impede a borda de subir; o domínio dele recebe 502.
- **Risco aceito:** um container na rede `edge` que assumisse `172.31.0.2` (com a borda parada, ou por ARP spoofing) forjaria o IP do cliente para os projetos. Exige controlar um container na `edge`. Mitigação: o Nginx do GoblinShift roda sem `NET_RAW`.

## Como verificar

- `tests/test_edge.py` trava o contrato: imagem por digest, só 80 e 443/tcp, IP fixo da borda e faixa dinâmica, nenhum `trusted_proxies`, `import` dos trechos, o trecho do GoblinShift só com `goblinshift.dev`, no IP reservado, o Nginx sem alias e sem `NET_RAW`.
- Na VPS: `cd /srv/edge && sudo docker compose exec proxy caddy validate --config /etc/caddy/Caddyfile` e, do lado de fora, `curl -sS -o /dev/null -w '%{http_code} %{ssl_verify_result}\n' https://goblinshift.dev/livez` (`200 0`) e `curl -sSI http://goblinshift.dev/livez` (308 para https).
