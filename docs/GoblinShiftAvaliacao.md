# Avaliação do GoblinShiftPlan

> Revisão de 13 de outubro de 2026, feita sobre a especificação de 12 de outubro. Base: os 30 livros de referência (capítulos pertinentes lidos por inteiro), a documentação oficial atual e uma leitura crítica da proposta como produto. **Atualizada no mesmo dia:** todos os problemas críticos e altos e o checklist de médios e baixos foram incorporados ao plano. Esta página agora registra onde cada um foi resolvido, o que mudou em relação à recomendação original e o que continua em aberto.

## Sumário

- [1. Estado atual](#estado)
- [2. Veredito da revisão](#veredito)
- [3. Decisões tomadas](#decisoes)
- [4. Rastreamento dos problemas](#rastreamento)
- [5. Onde o plano divergiu da recomendação](#divergencias)
- [6. Onde vários revisores chegaram à mesma conclusão](#convergencias)
- [7. Fontes lidas](#fontes)

---

<a id="estado"></a>
## 1. Estado atual

O plano incorpora todos os defeitos, os itens altos de cada área e o checklist de médios e baixos. A rastreabilidade completa está na [seção 4](#rastreamento).

**Continua em aberto:**
1. **Medição do OpenObserve na fatia 0:** consumo real de RAM e disco, e se SQL e alertas agendados funcionam sobre traces. O resultado fecha o ADR 013 ([3.7](GoblinShiftPlan.md#s3-7)).
2. **Fora do escopo da spec:** revisão jurídica dos termos e do aviso de privacidade antes da abertura pública (fatia 8).

---

<a id="veredito"></a>
## 2. Veredito da revisão

O plano original já era **tecnicamente muito acima da média**: entendia entrega duplicada, processo zumbi, disputa entre publicação e cancelamento, nomes de arquivo como dados e CSV sem inferência, e dizia com honestidade o que não prometia. Os problemas eram de outra natureza:

1. **Complexidade acidental muito maior que a essencial:** três transformações de arquivo rodeadas de outbox, broker, container por tentativa, OIDC, S3, OpenTelemetry e 13 tabelas, sem ordem de construção.
2. **Defeitos concretos de projeto:** o sandbox dava root do host ao worker, os prazos eram incompatíveis com a fila e o fencing estava incompleto, entre outros.
3. **Faltavam as partes que tornam um plano executável:** objetivo, ordem de construção, alvo de produção, estratégia de testes e registro de decisões.

Os três pontos foram tratados: a fila foi para o PostgreSQL, os defeitos foram corrigidos, e o plano ganhou objetivo, implantação, ordem em fatias, glossário e ADRs.

---

<a id="decisoes"></a>
## 3. Decisões tomadas

| Pergunta                             | Recomendação da revisão | Decisão                                                                               | No plano                                                       |
|--------------------------------------|-------------------------|---------------------------------------------------------------------------------------|----------------------------------------------------------------|
| Para que serve o projeto?            | — (pergunta aberta)     | Portfólio e serviço aberto                                                            | [1.8](GoblinShiftPlan.md#s1-8), objetivo na seção 1            |
| Fila                                 | PostgreSQL              | "O que for melhor": PostgreSQL                                                        | [3.1](GoblinShiftPlan.md#s3-1), [3.4](GoblinShiftPlan.md#s3-4) |
| Quem pode criar conta?               | Allowlist ou convite    | Qualquer conta Google ou GitHub                                                       | [1.8](GoblinShiftPlan.md#s1-8), [3.6](GoblinShiftPlan.md#s3-6) |
| Onde roda em produção?               | Uma VM própria          | VPS KVM da Hostinger (KVM 4 recomendado)                                              | [3.8](GoblinShiftPlan.md#s3-8)                                 |
| Redis?                               | Não                     | Não                                                                                   | ADR 003 em [3.10](GoblinShiftPlan.md#s3-10)                    |
| Fotos de celular (MPO, P3)           | Aceitar e converter     | Aceitar MPO pelo quadro principal; converter Display P3 para sRGB, de forma declarada | [1.2](GoblinShiftPlan.md#s1-2)                                 |
| `DELETE /runs/{id}/artifacts` na v1  | Tirar                   | Manter: privacidade num serviço aberto, custo baixo com a exclusão adiada             | [1.6](GoblinShiftPlan.md#s1-6)                                 |
| `attempt` e `retry_wait` no contrato | Não expor como contrato | Expor como campos informativos; decisões só por `status`                              | [1.6](GoblinShiftPlan.md#s1-6)                                 |
| Backend de telemetria                | OpenObserve na VPS      | OpenObserve na VPS, confirmado após medição na fatia 0                                | [3.7](GoblinShiftPlan.md#s3-7), ADR 013                        |

---

<a id="rastreamento"></a>
## 4. Rastreamento dos problemas

### 4.1. Defeitos de projeto

| Problema                                                       | Gravidade | Resolução                                                                                                   | No plano                                                       |
|----------------------------------------------------------------|-----------|-------------------------------------------------------------------------------------------------------------|----------------------------------------------------------------|
| Worker com o socket do Docker, ou seja, root sobre a VPS       | Crítico   | `executor-ctl` no host, único com acesso ao Docker, com quatro operações; um container gVisor por tentativa | [3.6](GoblinShiftPlan.md#s3-6)                                 |
| Cota de 4 GiB de temporário sem efeito real                    | Alto      | Filesystem ext4 de 6 GiB montado por loop; com uma tentativa por vez, o filesystem é a cota                 | [3.6](GoblinShiftPlan.md#s3-6)                                 |
| Prazo de 30 min incompatível com fila de 10                    | Alto      | Fila cheia → `503`; espera com prazo próprio (`QUEUE_EXPIRED`); 30 min contados da primeira tentativa       | [1.7](GoblinShiftPlan.md#s1-7)                                 |
| Fencing incompleto                                             | Alto      | `Run.generation` no claim; toda escrita condicional; candidatos por geração; prazo pelo relógio do banco    | [3.4](GoblinShiftPlan.md#s3-4)                                 |
| Dependência com `yield` segurando a transação no download      | Alto      | `scope="function"`, transações antes e depois da transferência, `idle_in_transaction_session_timeout`       | [3.2](GoblinShiftPlan.md#s3-2)                                 |
| `MAX_IMAGE_PIXELS` só bloqueia acima de 2×                     | Alto      | Validação própria antes de `load()`; aviso de bomba vira erro                                               | [1.2](GoblinShiftPlan.md#s1-2)                                 |
| JSON sem proteção contra duplicatas, `NaN` e tamanho           | Alto      | Leitura limitada, `object_pairs_hook`, `parse_constant`, profundidade e só então Pydantic                   | [3.6](GoblinShiftPlan.md#s3-6)                                 |
| Python 3.13 em fase só de segurança                            | Alto      | Python 3.14                                                                                                 | [2.1](GoblinShiftPlan.md#s2-1)                                 |
| Lote de 100 arquivos estourava o limite de 120 req/min         | Alto      | `POST /uploads` em lote e limites por classe de rota                                                        | [1.6](GoblinShiftPlan.md#s1-6), [1.7](GoblinShiftPlan.md#s1-7) |
| `consumer_timeout` do RabbitMQ contra tentativas de 2 × 10 min | Alto      | Eliminado com a fila no PostgreSQL                                                                          | [3.1](GoblinShiftPlan.md#s3-1)                                 |

### 4.2. Arquitetura e dados

| Problema                                                 | Gravidade | Resolução                                                                                               | No plano                                                       |
|----------------------------------------------------------|-----------|---------------------------------------------------------------------------------------------------------|----------------------------------------------------------------|
| Subdomínios não classificados                            | Alto      | Core, supporting e generic, com o padrão de cada um                                                     | [3.2](GoblinShiftPlan.md#s3-2)                                 |
| Agregados não declarados; Artifact sem dono              | Alto      | Run como raiz; `StoredObject`; transação multiagregado da publicação registrada no ADR 006              | [3.2](GoblinShiftPlan.md#s3-2), [3.3](GoblinShiftPlan.md#s3-3) |
| Módulos por tecnologia; ciclo `runs ↔ files`             | Alto      | Tabela de segredo, interface e dependências; `hold`/`release`; contratos do import-linter no CI         | [3.2](GoblinShiftPlan.md#s3-2)                                 |
| Contrato de receita em uma linha                         | Alto      | Contrato em código executável; classe do erro definida pelo tipo da exceção                             | [3.2](GoblinShiftPlan.md#s3-2)                                 |
| Associação polimórfica, contadores, `integer`, FKs vagas | Alto      | `StoredObject`, `CapacityPool` + livro-razão, índices únicos parciais, `bigint`, FKs compostas com dono | [3.3](GoblinShiftPlan.md#s3-3)                                 |
| Ordem de locks violada pelos próprios fluxos             | Alto      | Matriz fluxo × linhas travadas; reservas liberadas numa transação seguinte                              | [3.4](GoblinShiftPlan.md#s3-4)                                 |
| Máquina de estados ambígua                               | Alto      | Tabela de transições com invariantes; `phase` só em `running`                                           | [3.5](GoblinShiftPlan.md#s3-5)                                 |

### 4.3. Contrato da API

| Problema                                                   | Gravidade | Resolução                                                                                     | No plano                                                       |
|------------------------------------------------------------|-----------|-----------------------------------------------------------------------------------------------|----------------------------------------------------------------|
| Idempotência sem os casos definidos                        | Alto      | Tabela com os quatro casos do draft; formato fixo da chave; replay com `Idempotency-Replayed` | [1.6](GoblinShiftPlan.md#s1-6)                                 |
| Evolução de `recipe_version` sem política                  | Alto      | Nada muda dentro de uma versão; no máximo duas versões; `Sunset`; `RECIPE_VERSION_RETIRED`    | [1.6](GoblinShiftPlan.md#s1-6)                                 |
| Schema por receita com duas fontes de verdade              | Alto      | `oneOf` com `discriminator`; o catálogo referencia em vez de copiar                           | [1.6](GoblinShiftPlan.md#s1-6)                                 |
| Resultados não distinguiam "não publicado" de "sem saídas" | Médio     | Campo `published` e `expires_at`                                                              | [1.6](GoblinShiftPlan.md#s1-6)                                 |
| `404` para ID citado no corpo                              | Médio     | `422 INPUT_NOT_AVAILABLE`, um código só, sem permitir enumeração                              | [1.6](GoblinShiftPlan.md#s1-6)                                 |
| `DELETE /uploads` → `409` contradizia a exclusão adiada    | Médio     | Exclusão sempre adiada até o último uso (`ObjectUse`)                                         | [1.6](GoblinShiftPlan.md#s1-6), [3.6](GoblinShiftPlan.md#s3-6) |
| Formato de `errors` e códigos espalhados                   | Médio     | Catálogo único de códigos, com classe, retry, resultado e status HTTP                         | [1.6](GoblinShiftPlan.md#s1-6)                                 |

### 4.4. Segurança

| Problema                                          | Gravidade | Resolução                                                                                          | No plano                       |
|---------------------------------------------------|-----------|----------------------------------------------------------------------------------------------------|--------------------------------|
| Contas gratuitas num serviço que gasta CPU        | Alto      | Decisão foi serviço aberto; contido por cotas diárias, fila limitada, bloqueio por CLI e auditoria | [1.8](GoblinShiftPlan.md#s1-8) |
| CSRF declarado, sem mecanismo                     | Alto      | Fetch Metadata, `Origin` e token ligado à sessão sempre exigido; sem CORS; nenhum `GET` com efeito | [3.6](GoblinShiftPlan.md#s3-6) |
| Sessão sequestrada vira chave de 90 dias          | Alto      | Autenticação recente para emitir chave; validade padrão de 30 dias; prefixo `gsk_`; `logout-all`   | [3.6](GoblinShiftPlan.md#s3-6) |
| Downloads de conteúdo do usuário na mesma origem  | Alto      | `attachment`, `octet-stream`, `nosniff`, CSP `sandbox`; CSP própria da SPA                         | [3.6](GoblinShiftPlan.md#s3-6) |
| Estado do login num cookie assinado e não cifrado | Alto      | `OAuth(cache=…)` no PostgreSQL, com expiração curta                                                | [3.6](GoblinShiftPlan.md#s3-6) |
| Sessão de 12 h sem inatividade                    | Alto      | 30 min de inatividade e teto de 8 h; sessão nova a cada login; `Clear-Site-Data`                   | [3.6](GoblinShiftPlan.md#s3-6) |

Limitação registrada no plano: nem o Google nem o GitHub deixam o cliente exigir a senha de novo, então a reautenticação prova um login recente no provedor, não a digitação da senha.

### 4.5. Python e processamento

| Problema                                                       | Gravidade | Resolução                                                                                                                            | No plano                       |
|----------------------------------------------------------------|-----------|--------------------------------------------------------------------------------------------------------------------------------------|--------------------------------|
| `ImageOps.contain` amplia e arredonda; metadados variam        | Alto      | `resize` com dimensões calculadas pelo código; `exif`, `icc_profile` e XMP explícitos no `save`                                      | [1.2](GoblinShiftPlan.md#s1-2) |
| Modos `LA`, `I;16`, `P` com transparência sem regra            | Médio     | Regra para cada modo; 16 bits rejeitado com `UNSUPPORTED_BIT_DEPTH`                                                                  | [1.2](GoblinShiftPlan.md#s1-2) |
| MPO e Display P3 de celular                                    | Alto      | MPO pelo quadro principal; Display P3 convertido para sRGB com intenção colorimétrica relativa, registrado; outros perfis rejeitados | [1.2](GoblinShiftPlan.md#s1-2) |
| Módulo `csv` não detecta tudo o que a spec chamava de inválido | Alto      | O que o módulo faz e o que é feito à mão; `ab"c` aceito como literal, de forma declarada                                             | [1.3](GoblinShiftPlan.md#s1-3) |
| "ZIP opaco" contra "não aceitar executáveis"                   | Médio     | Limite declarado; a proteção real é o download não executável                                                                        | [1.4](GoblinShiftPlan.md#s1-4) |

### 4.6. Testes, entrega e observabilidade

| Problema                                           | Gravidade | Resolução                                                                                                                                                  | No plano                       |
|----------------------------------------------------|-----------|------------------------------------------------------------------------------------------------------------------------------------------------------------|--------------------------------|
| Sem alvo de produção                               | Alto      | VPS KVM da Hostinger, com teste de aceitação do ambiente                                                                                                   | [3.8](GoblinShiftPlan.md#s3-8) |
| Sem regra de merge                                 | Alto      | Testes no mesmo PR e CI verde; pronto significa passar na VPS                                                                                              | [3.9](GoblinShiftPlan.md#s3-9) |
| Protocolo e estados sem testes específicos         | Alto      | Concorrência com PostgreSQL real, SIGKILL e SIGSTOP, máquina de estados com Hypothesis, Schemathesis                                                       | [3.7](GoblinShiftPlan.md#s3-7) |
| Trace perdido entre a requisição e a tentativa     | Alto      | `traceparent` gravado na Run e extraído no claim, com span link                                                                                            | [3.7](GoblinShiftPlan.md#s3-7) |
| Backups, segredos e health checks ausentes         | Alto      | Dump diário externo com restauração testada; Compose `secrets`; `/livez` e `/readyz`                                                                       | [3.8](GoblinShiftPlan.md#s3-8) |
| Migrações durante o deploy                         | Médio     | Expand/contract e prazo de parada do worker maior que uma tentativa                                                                                        | [3.8](GoblinShiftPlan.md#s3-8) |
| Sem ordem de construção                            | Alto      | Dez fatias com critério de pronto verificável; protótipo do sandbox na fatia 1                                                                             | [3.9](GoblinShiftPlan.md#s3-9) |
| Observabilidade como logs e métricas pré-agregadas | Médio     | Eventos amplos com `main = true` por requisição, tentativa, término de Run e ciclo de manutenção; sem dados pessoais                                       | [3.7](GoblinShiftPlan.md#s3-7) |
| Sem SLOs nem política de alertas                   | Médio     | Três SLOs baseados em eventos (aceitação, execução, download) em janela deslizante de 30 dias, política de orçamento de erro e alertas de queima relativos | [3.7](GoblinShiftPlan.md#s3-7) |

### 4.7. Frontend

| Problema                                     | Gravidade | Resolução                                                                              | No plano                       |
|----------------------------------------------|-----------|----------------------------------------------------------------------------------------|--------------------------------|
| `fetch` sem progresso de upload              | Alto      | `PUT` por `XMLHttpRequest`, com progresso real, cancelamento e estado "confirmando"    | [2.2](GoblinShiftPlan.md#s2-2) |
| Download de até 512 MiB por `fetch` → `blob` | Alto      | Sempre por navegação, com `attachment`                                                 | [2.2](GoblinShiftPlan.md#s2-2) |
| Formulários por receita ambíguos             | Alto      | Escritos à mão, tipados pela união gerada do OpenAPI; CI falha se os tipos divergirem  | [2.2](GoblinShiftPlan.md#s2-2) |
| Acessibilidade sem critério verificável      | Médio     | Alternativa ao arrasto, `aria-live` só em mudanças, pausa de animação, nada só por cor | [2.2](GoblinShiftPlan.md#s2-2) |

### 4.8. O documento

| Problema                                    | Gravidade | Resolução                                                                                                                | No plano                                                       |
|---------------------------------------------|-----------|--------------------------------------------------------------------------------------------------------------------------|----------------------------------------------------------------|
| Faltava objetivo e público                  | Alto      | Objetivo na abertura da seção 1                                                                                          | [1.8](GoblinShiftPlan.md#s1-8)                                 |
| Decisões sem alternativas registradas       | Alto      | Doze ADRs com alternativas descartadas                                                                                   | [3.10](GoblinShiftPlan.md#s3-10)                               |
| Termos sem definição                        | Médio     | Glossário                                                                                                                | [1.9](GoblinShiftPlan.md#s1-9)                                 |
| Requisitos misturados com implementação     | Médio     | A seção 1 ficou só com o comportamento exigido; a mecânica de Pillow, `csv`, FFmpeg e FastAPI foi para uma seção própria | [2.3](GoblinShiftPlan.md#s2-3)                                 |
| Regras repetidas (limites, manifesto)       | Baixo     | Limites numéricos só em 1.7, com as demais seções apontando para lá; o manifesto só em 3.6                               | [1.7](GoblinShiftPlan.md#s1-7), [3.6](GoblinShiftPlan.md#s3-6) |
| Seções 4 e 5 mais pesadas que a arquitetura | Baixo     | Movidas para um arquivo próprio, com os IDs B01–B30 e D01–D30 clicáveis a partir do plano                                | [Referências](GoblinShiftReferencias.md)                       |

### 4.9. Checklist de médios e baixos

Todos os 38 itens foram aplicados, com duas ressalvas: `engine.dispose` no fork ficou obsoleto com a saída do Celery, e o limite de entregas do broker virou o limite de claims (`INFRA_POISON`).

| Área                | Onde foi parar no plano                                                                                                                                                            |
|---------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Arquitetura e dados | [3.2](GoblinShiftPlan.md#s3-2) (repositório, unidade de trabalho, timeouts, threads × conexões) e [3.3](GoblinShiftPlan.md#s3-3) (tabelas e índices)                               |
| Execução            | [3.4](GoblinShiftPlan.md#s3-4) (claims, limitador de exclusões, matriz de locks) e [1.7](GoblinShiftPlan.md#s1-7) (tabela de retenção)                                             |
| API                 | [1.6](GoblinShiftPlan.md#s1-6) (paginação, problem+json, `validate_only`, `If-Match`, `{method, url, headers}`)                                                                    |
| Segurança           | [1.5](GoblinShiftPlan.md#s1-5) (FFmpeg), [1.3](GoblinShiftPlan.md#s1-3) (fórmulas de planilha), [3.6](GoblinShiftPlan.md#s3-6) (auditoria), [3.8](GoblinShiftPlan.md#s3-8) (Nginx) |
| Python              | [1.3](GoblinShiftPlan.md#s1-3) (saída CSV, caixa), [3.6](GoblinShiftPlan.md#s3-6) (`casefold`, ZIP determinístico)                                                                 |
| Frontend            | [2.2](GoblinShiftPlan.md#s2-2) (polling, ETag, Idempotency-Key, sem atualização otimista, estados vazios)                                                                          |

---

<a id="divergencias"></a>
## 5. Onde o plano divergiu da recomendação

- **Contas abertas em vez de allowlist.** A revisão recomendava convite ou allowlist. A decisão foi abrir a qualquer conta Google ou GitHub, então o risco de abuso passou a ser contido por cotas diárias, fila limitada, bloqueio por CLI, auditoria e limites do executor ([1.8](GoblinShiftPlan.md#s1-8)).
- **Container por tentativa em vez de serviço executor fixo.** A revisão sugeria um serviço executor fixo com subprocessos, a opção mais simples sem socket no worker. Como a orientação foi "o melhor, mesmo que mais complexo", o plano adotou um container gVisor novo por tentativa, criado pelo `executor-ctl`: estado limpo a cada tentativa, kernel em espaço de usuário e nenhum acesso do worker ao Docker ([3.6](GoblinShiftPlan.md#s3-6), ADR 004).
- **Sandbox antes do núcleo.** A primeira proposta de ordem punha o CSV Janitor primeiro e o sandbox na quarta fatia. O plano final faz um protótipo descartável do sandbox na fatia 1, porque é o maior risco técnico e depende da VPS escolhida ([3.9](GoblinShiftPlan.md#s3-9)).
- **`DELETE /runs/{id}/artifacts` mantido.** A revisão sugeria cortá-lo da v1. Depois que a exclusão adiada foi especificada, o custo caiu, e num serviço aberto apagar resultados antes das 48 h vale como privacidade.
- **Validade de chave.** A revisão pedia autenticação recente; o plano também baixou a validade padrão de 90 para 30 dias, mantendo 90 como máximo.

---

<a id="convergencias"></a>
## 6. Onde vários revisores chegaram à mesma conclusão

Estes pontos apareceram de forma independente em frentes diferentes, cada uma lendo livros diferentes. São os mais confiáveis desta avaliação.

| Conclusão | Quem chegou a ela |
|-----------|-------------------|

Todas essas conclusões foram incorporadas ao plano.

---

<a id="fontes"></a>
## 7. Fontes lidas

**Livros (capítulos lidos por inteiro pelos revisores)**

- *The Pragmatic Programmer*: Topics 5, 8, 10, 11, 12, 24, 25, 27, 45.
- *Code Complete*: caps. 5, 8, 29.
- *A Philosophy of Software Design*: caps. 2, 3, 4, 5, 8, 9, 10, 11, 19.
- *Refactoring*: caps. 2, 3.
- *Dive Into Design Patterns*: princípios, Adapter, Strategy, Template Method.
- *Software Design for Python Programmers*: caps. 1, 8, 10.
- *Fluent Python*: caps. 18, 19.
- *Effective Python*: Items 43–45, 68, 77–79, 82, 83, 89.
- *Python Concurrency with asyncio*: caps. 7, 9.
- *High Performance Python*: caps. 5, 11.
- *The Design of Web APIs*: caps. 9, 10, 14, 15 (15.1–15.6), seções 13.4, 13.6, 13.7.
- *API Design Patterns*: caps. 9, 10, 11, 18, 21, 24–29.
- *Secure APIs*: caps. 3 (3.2–3.4), 4, 5, 6, 7.
- *Learning Domain-Driven Design*: caps. 1, 5, 6, 8, 9, 10.
- *Fundamentals of Software Architecture*: caps. 4, 5, 6, 7, 11, 21.
- *Architecture Patterns with Python*: caps. 2, 6, 7, 8, 12, epílogo.
- *Database Design for Mere Mortals*: caps. 8, 10.
- *SQL Antipatterns*: caps. 4, 5, 6, 7, 11, 12, 13, 26.
- *The Art of PostgreSQL*: caps. 8, 14, 28, 30, 36, 40; partes dos caps. 22 e 23.
- *Designing Data-Intensive Applications*: seções dos caps. 5, 8, 9 e 12 (exactly-once, fencing, process pauses, messaging, dual writes).
- *Designing Distributed Systems*: caps. 10, 11, 12.
- *Release It!*: cap. 5 inteiro (stability patterns).
- *Threat Modeling*: caps. 3, 8, 13, 14.
- *Effective Software Testing*: caps. 1, 5, 8, 9.
- *Python Testing with pytest*: caps. 3, 7, 11.
- *Fundamentals of DevOps and Software Delivery*: cap. 10; seções dos caps. 1, 4, 5, 9; key takeaways do cap. 8.
- *Observability Engineering*: caps. 5, 6, 7, 9, 11.
- *Effective TypeScript*: cap. 4, Items 58, 59, 74.
- *React in Depth*: cap. 9, seções 2.2, 2.3, 8.8.
- *Refactoring UI*: inteiro.

**Documentação oficial consultada**

- FastAPI (dependências com `yield` e `scope`, threadpool, `Request.stream`, `openapi_extra`), Pydantic, Python 3.13 (`json`, `csv`, `zipfile`), devguide de versões, Pillow, SQLAlchemy 2.0, Alembic autogenerate, PostgreSQL (constraints, locking, isolation, `SKIP LOCKED`).
- Celery, RabbitMQ (consumers, confirms), Authlib, Google OIDC, RFC 9110, 9457, 6585, 9700, draft Idempotency-Key.
- OWASP (CSRF, Session, File Upload, API Top 10, CSV Injection), Docker (security, rootless, resource constraints, `docker run`), gVisor.
- TanStack Query v5, openapi-fetch, React Router, shadcn/ui, MDN `XMLHttpRequest.upload`, WCAG 2.2.
- OpenTelemetry Python, Hypothesis, Testcontainers, Schemathesis, uv, Docker Compose.
