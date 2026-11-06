# ADR 043. Confiança no gateway e certificado de origem

- **Estado:** aceito em 2026-11-06, por decisão do dono do serviço. Está no repositório e aguarda a publicação do gateway
- **Plano:** [3.8](../GoblinShiftPlan.md#s3-8)
- **Relacionado:** [ADR 014](014-proxy-de-borda-caddy.md) (contrato entre a borda e os projetos); [ADR 041](041-ip-do-cliente-atras-da-cloudflare.md) (IP do cliente)

## Contexto

O caminho em produção é Cloudflare → Traefik do Coolify → gateway Caddy → projetos. Cada salto confia no anterior por um motivo próprio, e essa regra estava dividida entre os ADRs 014 e 041. Falta um deles: o certificado que o gateway apresenta à Cloudflare, que os sites passam a declarar um a um.

## Alternativas

| Alternativa | Prós | Contras |
|-------------|------|---------|
| **A. Certificado de origem da Cloudflare** (escolhida) | Feito para este trecho; arquivo simples, carregado por `caddy reload`; sem imagem nova e sem mudar `gateway.json` nem `Caddyfile` | Só a Cloudflare o reconhece, então vale para o tráfego que passa por ela; é mais uma chave para guardar |
| **B. Certificado público por validação de DNS** | Reconhecido por qualquer cliente | Exige imagem própria do Caddy, em lugar da oficial por digest, e um token que edita o DNS guardado no servidor |
| **C. Encerrar o TLS no Traefik** | Um só lugar cuida dos certificados | Refaz o passthrough e o PROXY protocol, de que depende o IP do cliente do ADR 041 |

## Decisão

Alternativa A. Em `docker/gateway/sites/`, o domínio e o `www` de cada zona declaram `tls /data/origin-ca/<zona>.pem /data/origin-ca/<zona>.key`. Os dois arquivos ficam no volume de dados do gateway e a chave privada nunca entra no repositório.

Com isso, a confiança no gateway fica assim:

| Trecho | O que vale como prova | Onde está |
|--------|-----------------------|-----------|
| Cloudflare → gateway | Certificado de origem da zona | `tls` em cada site |
| Traefik → gateway | IP de quem conecta, único aceito no PROXY protocol; o resto é recusado | `proxy_protocol` no `Caddyfile` |
| Quem é o cliente | `CF-Connecting-IP`, só com um endereço e vindo das faixas da Cloudflare | `@cloudflare` em cada site ([ADR 041](041-ip-do-cliente-atras-da-cloudflare.md)) |
| Gateway → projetos | IP fixo do gateway na rede `edge` | Contrato do [ADR 014](014-proxy-de-borda-caddy.md) |

## Publicação

O merge não publica nada: como no [ADR 041](041-ip-do-cliente-atras-da-cloudflare.md#publicação), o gateway segue no commit fixo até a publicação dele. Os arquivos do certificado entram no volume antes de os sites serem carregados, e o `caddy validate` recusa a configuração enquanto faltar algum.

## Consequências

- Os sites dependem de dois arquivos que não vêm do repositório. Quem sobe a configuração noutro ambiente precisa fornecê-los ou retirar as linhas `tls`, como fazem os testes.
- O `stillon.tech.caddy` ganha duas linhas em relação à cópia do StillOn, que é levada junto como manda o ADR 041.

## Compliance

- `tests/test_gateway_client_ip.py` confere que o domínio e o `www` de cada zona apontam para os arquivos da zona, sobe a imagem do `gateway.json` com os sites intactos e certificados descartáveis, confere o certificado entregue em cada nome e que a validação recusa a configuração sem os arquivos.
- O `gitleaks` do CI barra chave privada no repositório.
