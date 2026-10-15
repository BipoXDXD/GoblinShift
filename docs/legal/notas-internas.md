# Notas internas dos textos legais

Material de revisão dos [Termos de Uso](termos-de-uso.md) e do
[Aviso de Privacidade](aviso-de-privacidade.md). Nada daqui entra no build da SPA: as páginas
`/termos` e `/privacidade` importam só os dois textos públicos (`web/src/legal/TermsPage.tsx` e
`PrivacyPage.tsx`), e `web/src/legal/LegalPage.test.tsx` e `web/e2e/specs/legal.spec.ts` falham se
algo deste arquivo voltar para eles.

Convenção: nos textos públicos, o que depende de decisão fica marcado como `[[A DEFINIR: ...]]`,
visível na página enquanto o texto for rascunho. O id do levantamento da fatia 8 (J-n) de cada
marcação, as perguntas ao revisor e as fontes de cada afirmação técnica ficam aqui.

## Termos de Uso

### Cabeçalho do rascunho

Texto que abria o arquivo público até sair do build:

> **RASCUNHO — não publicar sem revisão jurídica humana.**
>
> Este texto foi redigido a partir do que o sistema faz (código e plano técnico), não por
> advogado(a). O plano exige revisão jurídica antes da abertura pública
> (`docs/GoblinShiftPlan.md`, 1.8; ADR 012). Trechos entre `[[A DEFINIR: ...]]` dependem de
> decisão do dono do serviço. Notas marcadas como **Nota para a revisão** são perguntas ao
> revisor e saem do texto final. As fontes internas de cada afirmação técnica estão no fim.

### Pendências antes de publicar

> Esta seção sai do texto final. Cada marcação `[[A DEFINIR (J-n) ...]]` traz o id do
> levantamento da fatia 8. Nenhuma delas depende de código: os pontos que se resolviam lendo a
> implementação já foram preenchidos.

**Decide o dono do serviço** (o advogado só confere a redação):

- **J-4**, versão e vigência (topo). Sugestão: `v1` e a data da publicação, com a versão gravada
  em `terms_version`.
- **J-5**, domínio definitivo (topo). Depende da compra do domínio.
- **J-6**, e-mails de contato e de denúncia (itens 1 e 7). Sugestão: `contato@` e `abuso@` no
  domínio, apontando para a mesma caixa.
- **J-7**, política de divulgação de vulnerabilidades (item 6). Sugestão: `SECURITY.md` e
  `/.well-known/security.txt`, com o e-mail de J-6.
- **J-8**, outras condutas proibidas (item 6). Sugestão: mineração, uso como CDN ou hospedagem
  de arquivos, automação fora da API documentada, várias contas para contornar cotas.
- **J-9**, prazo de resposta às denúncias (item 7). Sugestão: 72 h para a triagem; conteúdo
  ilegal grave, na hora.
- **J-10**, aviso prévio ou recurso no bloqueio (item 8). Sugestão: recurso por e-mail depois do
  bloqueio, sem aviso prévio em casos de segurança.
- **J-11**, aviso prévio no encerramento do serviço (item 8). Sugestão: 30 dias de aviso no site.
- **J-12**, licença do código; o repositório ainda não tem `LICENSE` (item 9). Opções: MIT,
  Apache-2.0 ou AGPL-3.0.
- **J-13**, marca e ilustrações (item 9). Depende das licenças dos geradores dos assets.
- **J-14**, antecedência de aviso de mudança e onde fica o histórico (item 12). Sugestão: 15 dias
  e `docs/legal/` no Git.

**Exige advogado(a):**

- **J-16**, identificação de quem oferece o serviço (item 1).
- **J-18**, idade mínima e contas de menores (item 2).
- **J-20**, procedimento para abuso sexual infantil e conteúdo ilegal grave: autoridades,
  preservação, prazos e a expiração de 48 h (item 7). Este procedimento não é escrito por processamento externo.
- **J-21**, foro em relação de consumo (item 13).

### Marcações a definir: id e texto original

O texto público mostra cada marcação sem o id e sem as notas ao revisor. Esta é a
redação original, na ordem do arquivo.

| Seção | Marcação original |
|---|---|
| Topo (versão, vigência e endereço) | [[A DEFINIR (J-4): número da versão, gravado como `terms_version` no aceite]] |
| Topo (versão, vigência e endereço) | [[A DEFINIR (J-4): data de publicação]] |
| Topo (versão, vigência e endereço) | [[A DEFINIR (J-5): domínio definitivo]] |
| 1. Quem oferece o serviço | [[A DEFINIR (J-16): nome completo ou razão social do controlador, CPF ou CNPJ, e endereço]] |
| 1. Quem oferece o serviço | [[A DEFINIR (J-6): e-mail de contato]] |
| 2. Aceite destes termos | [[A DEFINIR (J-18): idade mínima para usar o serviço e o que fazer com contas de menores. Google e GitHub têm idade mínima própria, mas o GoblinShift não recebe a data de nascimento e não tem como verificar idade.]] |
| 6. Uso aceitável | [[A DEFINIR (J-7): haverá política de divulgação responsável de vulnerabilidades? Se sim, indicar o canal e a referência aqui]] |
| 6. Uso aceitável | [[A DEFINIR (J-8): outras condutas proibidas que o dono queira listar.]] |
| 7. Abuso sexual infantil e outros conteúdos ilegais | [[A DEFINIR (J-20) com orientação jurídica: quais autoridades e canais (por exemplo, Polícia Federal e Ministério Público), o que preservar, por quanto tempo, e como conciliar a preservação com a expiração automática de 48 horas; o plano registra que esse procedimento exige orientação jurídica e não pode ser escrito por processamento externo]] |
| 7. Abuso sexual infantil e outros conteúdos ilegais | [[A DEFINIR (J-6): e-mail de denúncias]] |
| 7. Abuso sexual infantil e outros conteúdos ilegais | [[A DEFINIR (J-9): prazo de resposta às denúncias.]] |
| 8. Suspensão e encerramento | [[A DEFINIR (J-10): haverá aviso prévio ou canal de recurso para o bloqueio, salvo nos casos do item 7?]] |
| 8. Suspensão e encerramento | [[A DEFINIR (J-11): aviso prévio mínimo em caso de encerramento.]] |
| 9. Conteúdo e propriedade | [[A DEFINIR (J-12): licença do código-fonte; o repositório ainda não tem arquivo de licença]] |
| 9. Conteúdo e propriedade | [[A DEFINIR (J-13): condições de uso da marca e das ilustrações]] |
| 12. Mudanças nestes Termos | [[A DEFINIR (J-14): antecedência mínima de aviso para mudanças relevantes e onde o histórico de versões fica publicado.]] |
| 13. Lei aplicável e foro | [[A DEFINIR (J-21): foro. Nota para a revisão: em relação de consumo, o consumidor pode demandar no foro do próprio domicílio (CDC, art. 101, I).]] |

### Notas para a revisão

Item 10, antes da lista:

> **Nota para a revisão:** o serviço é gratuito, mas a relação pode ser considerada de consumo
> (CDC). Cláusulas que exonerem ou atenuem a responsabilidade do fornecedor podem ser nulas
> (CDC, art. 51, I). Ajustar a redação abaixo ao que for válido.

### Fontes internas

Cada afirmação técnica dos Termos corresponde a um trecho do plano ou do código. "Plano" é
`docs/GoblinShiftPlan.md`. Onde a fonte é só o plano, o comportamento é o especificado para a
abertura pública e ainda depende de fatia não concluída (indicada entre parênteses).

| Afirmação | Fonte |
|---|---|
| Projeto de portfólio, gratuito, código público, serviço aberto | Plano, introdução da seção 1 e 1.8; ADR 012 |
| Revisão jurídica obrigatória antes da abertura | Plano 1.8 (último item de Privacidade); ADR 012, Consequências e Como verificar; plano das fatias 7 e 8, 2.1 |
| Aceite no primeiro login com versão e data; reaceite quando a versão muda | Plano 1.8 (Base legal e transparência) e 3.3 (`User`: versão e data dos termos); plano das fatias 7 e 8, 2.1 (fatia 6/8, não implementado) |
| Receitas fixas, resultados privados com manifesto e relatório; catálogo mostra só o implementado | Plano, introdução da seção 1 e 1.1 |
| Sem rede no processamento, com processamento determinístico, sem scripts do cliente | Plano, introdução da seção 1; 3.6 (Sandbox: `--network=none`) |
| Login só Google (OIDC) ou GitHub (OAuth 2.0), sem senha própria, sem convite | Plano 1.8 (Contas) e 3.6 (Identidade web); ADR 008; ADR 012 (fatia 6) |
| Duas contas separadas para a mesma pessoa; sem vínculo por e-mail | Plano 1.8; ADR 008, Decisão e Consequências |
| Sessão: 30 min de inatividade ou 8 h absolutas; `logout-all` revoga sessões e chaves | Plano 3.6 (Sessão) (fatia 6) |
| Até 5 chaves; exibição única; 30 dias padrão, 90 máximo; exige login recente | Plano 1.7 (Configurações), 3.6 (Chaves de API e Autenticação recente); `src/goblinshift/identity/keys.py` (validade) |
| Cotas: 1 execução não terminal, 30 execuções e 2 GiB/24 h, 4 GiB por conta, 100 presets | Plano 1.7 (tabela de limites); ADR 012 |
| Recusa imediata ao atingir limite; fila com prazo de 2 h | Plano 1.7 (Fila) e 1.8 (Recusar cedo); `src/goblinshift/runs/lifecycle.py` (`QUEUE_WINDOW = 2 h`) |
| Arquivos e resultados por 48 h após o término; upload não usado em 24 h | Plano 1.7 (Retenção); `src/goblinshift/files/limits.py` (`ARTIFACT_TTL`, `USED_INPUT_TTL`, `READY_UPLOAD_TTL`) |
| Arquivos sem backup | Plano 3.8 (Backups: "Arquivos: sem backup, por decisão") |
| Sem links públicos de compartilhamento | Plano 1.8 (Abuso e custo); ADR 012 |
| Bloqueio revoga sessões e chaves, cancela a execução e impede novo login | Plano 1.8 (Estado da conta); ADR 012; plano das fatias 7 e 8, 2.6 (fatia 6) |
| CSAM exige orientação jurídica, não runbook de processamento externo | Plano das fatias 7 e 8, 2.6 |
| Sem varredura automatizada de conteúdo nesta versão | Plano 1.8 (Abuso e custo): as medidas listadas são limites, privacidade, expiração, bloqueio e termos; nenhuma é varredura |
| Canal de contato para denúncias | Plano 1.8 (Abuso e custo); ADR 012 |
| `DELETE /me`: login recente; revoga, cancela e remove em até 24 h; backups expiram em até 30 dias | Plano 1.8 (Exclusão), 3.6 (Autenticação recente) e 3.8 (Backups) (fatia 6, não implementado) |
| Remoção de metadados não é anonimização | Plano 1.2 (Image Forge, perfis de cor e metadados) |
| Repositório sem arquivo de licença | Raiz de `origin/main` em 2026-10-14 (não há `LICENSE`) |

## Aviso de Privacidade

### Cabeçalho do rascunho

Texto que abria o arquivo público até sair do build:

> **RASCUNHO — não publicar sem revisão jurídica humana.**
>
> Este texto foi redigido a partir do que o sistema faz (código e plano técnico), não por
> advogado(a). O plano exige revisão jurídica antes da abertura pública
> (`docs/GoblinShiftPlan.md`, 1.8; ADR 012). Trechos entre `[[A DEFINIR: ...]]` dependem de
> decisão do dono do serviço. Notas marcadas como **Nota para a revisão** são perguntas ao
> revisor e saem do texto final. As fontes internas de cada afirmação técnica estão no fim,
> junto com as divergências entre plano e código encontradas ao redigir.

### Pendências antes de publicar

> Esta seção sai do texto final. Cada marcação `[[A DEFINIR (J-n) ...]]` traz o id do
> levantamento da fatia 8. Nenhuma delas depende de código: os pontos que se resolviam lendo a
> implementação já foram preenchidos.

**Decide o dono do serviço** (o advogado só confere a redação):

- **J-4**, versão e vigência (topo). Sugestão: `v1` e a data da publicação; a versão é o valor
  gravado em `terms_version` no aceite.
- **J-5**, domínio definitivo (topo). Depende da compra do domínio.
- **J-6**, e-mail do canal de privacidade (itens 1 e 8). Sugestão: `privacidade@<domínio>`.
- **J-14**, onde fica o histórico de versões (item 12). Sugestão: `docs/legal/` no Git, com link
  nas páginas.
- **J-15**, como avisar os titulares num incidente, já que não temos e-mail (item 10). Sugestão:
  banner no site e aviso na conta no login seguinte; o advogado confere se basta perante a ANPD.

**Exige advogado(a):**

- **J-16**, identificação do controlador: pessoa física ou CNPJ, e o que precisa ser publicado
  (item 1).
- **J-17**, encarregado ou declaração de dispensa como agente de pequeno porte (item 1).
- **J-18**, idade mínima e LGPD art. 14, sem como verificar idade (item 11).
- **J-19**, transferência internacional: localização do GitHub Actions, região do B2 e mecanismo
  do art. 33 (itens 6 e 7). Responder antes de criar a conta B2, porque a região é fixa por conta.
- **J-22**, base legal do registro do aceite: execução de contrato ou exercício regular de
  direitos (item 4).
- **J-23 a J-25**, avaliações de legítimo interesse (LIA) da auditoria e do antiabuso, da
  telemetria e do backup (item 4).
- **J-26**, manter o identificador do provedor depois de bloqueio seguido de exclusão, e por
  quanto tempo (item 5). Proposta técnica para levar: guardar só um HMAC de `issuer` + `subject`,
  por prazo fixo.
- **J-27**, restauração que traz de volta uma conta excluída (item 8). Proposta técnica: um
  diário de exclusões pseudonimizado, fora do dump, reaplicado depois de toda restauração.
- **J-28**, verificação de identidade e prazo de resposta do art. 19 (item 8). Proposta: a
  verificação é o próprio login no provedor, com resposta em até 15 dias.

### Marcações a definir: id e texto original

O texto público mostra cada marcação sem o id e sem as notas ao revisor. Esta é a
redação original, na ordem do arquivo.

| Seção | Marcação original |
|---|---|
| Topo (versão, vigência e endereço) | [[A DEFINIR (J-4): número da versão]] |
| Topo (versão, vigência e endereço) | [[A DEFINIR (J-4): data de publicação]] |
| Topo (versão, vigência e endereço) | [[A DEFINIR (J-5): domínio definitivo]] |
| 1. Controlador e contato | [[A DEFINIR (J-16): nome completo ou razão social, CPF ou CNPJ, endereço]] |
| 1. Controlador e contato | [[A DEFINIR (J-6): e-mail]] |
| 1. Controlador e contato | [[A DEFINIR (J-17): nome e contato do encarregado, ou a declaração de dispensa. Nota para a revisão: a ANPD dispensa agentes de tratamento de pequeno porte de indicar encarregado, mas exige um canal de comunicação com o titular; confirmar o enquadramento e a redação.]] |
| 4. Para que usamos e com que base legal | [[A DEFINIR (J-22) na revisão: ou exercício regular de direitos (inciso VI)]] |
| 4. Para que usamos e com que base legal | [[A DEFINIR (J-23) na revisão: registrar a avaliação de legítimo interesse]] |
| 4. Para que usamos e com que base legal | [[A DEFINIR (J-24) na revisão]] |
| 4. Para que usamos e com que base legal | [[A DEFINIR (J-25) na revisão]] |
| 5. Por quanto tempo guardamos | [[A DEFINIR (J-26): se a conta for bloqueada e depois excluída, o identificador do provedor é mantido para que o bloqueio continue valendo? Por quanto tempo? O plano diz que o bloqueio "impede novos logins da mesma identidade", mas não diz o que acontece depois de `DELETE /me`.]] |
| 6. Com quem compartilhamos | [[A DEFINIR (J-19): confirmar localização e mencionar como transferência internacional]] |
| 6. Com quem compartilhamos | [[A DEFINIR (J-19): confirmar a região do bucket]] |
| 7. Transferência internacional | [[A DEFINIR (J-19) com a revisão jurídica: mecanismo de transferência do art. 33 da LGPD (por exemplo, cláusulas-padrão contratuais aprovadas pela ANPD, ou outro) para o Backblaze e para o GitHub Actions.]] |
| 8. Seus direitos e como exercê-los | [[A DEFINIR (J-27) na revisão: o que fazer se uma restauração trouxer de volta uma conta excluída]] |
| 8. Seus direitos e como exercê-los | [[A DEFINIR (J-6): e-mail]] |
| 8. Seus direitos e como exercê-los | [[A DEFINIR (J-28): procedimento de verificação de identidade e prazo de resposta; nota para a revisão: art. 19 da LGPD.]] |
| 10. Segurança | [[A DEFINIR (J-15): como os titulares serão avisados, já que não temos e-mail; por exemplo, aviso na própria conta e no site]] |
| 11. Crianças e adolescentes | [[A DEFINIR (J-18): idade mínima e tratamento de dados de crianças e adolescentes (LGPD, art. 14). O serviço não coleta idade.]] |
| 12. Mudanças neste aviso | [[A DEFINIR (J-14): onde fica o histórico de versões.]] |

### Notas para a revisão

Item 4, antes da tabela de bases legais:

> **Nota para a revisão:** o plano adota "execução do contrato" como base principal. As demais
> bases abaixo são proposta deste rascunho e precisam de validação.

Item 6, antes da tabela de fornecedores:

> **Nota para a revisão:** confirmar a qualificação de cada fornecedor (operador, suboperador
> ou controlador independente). Google e GitHub, como provedores de login, tratam seus dados
> também sob as políticas próprias, na relação direta que você tem com eles.

### Fontes internas

"Plano" é `docs/GoblinShiftPlan.md`; "plano 7-8" é o levantamento das fatias 7 e 8
(`planos/fatias7-8-plano.md`, fora do repositório). Onde a fonte é só o plano, o comportamento é
o especificado para a abertura pública e ainda depende de fatia não concluída (indicada entre
parênteses). Caminhos sem branch indicada estão na `main` (conferida em `6c1c41f`); os marcados
`origin/fatia-6` foram conferidos em `51c2707` e entram com o PR #20.

| Afirmação | Fonte |
|---|---|
| Sem senha própria; login Google (OIDC, escopo `openid`) ou GitHub (OAuth 2.0, sem escopo); token do GitHub descartado após uma chamada a `GET /user` | Plano 1.8 (Contas, Minimização) e 3.6 (Identidade web); ADR 008 (fatia 6) |
| E-mail não coletado; só `issuer`, `subject` e nome de exibição | Plano 1.8 (Minimização); ADR 008, Consequências; `src/goblinshift/identity/tables.py` (`app_user` sem coluna de e-mail) |
| Identidade GitHub pelo `id` numérico; nome de exibição = `login` | Plano 1.8 (Contas, Minimização); ADR 008 |
| Estado `active`/`blocked`, data de criação | `src/goblinshift/identity/tables.py` (`status`, `created_at`) |
| Versão e data dos termos no `User` | Plano 3.3 (`User`); plano 7-8, 2.1 (fatia 6/8) |
| Provedor de login não recebe acesso aos arquivos | Plano 3.6 (Identidade web); ADR 008, Consequências |
| Sessão: segredo aleatório com hash no banco, datas, expiração 30 min / 8 h, `Clear-Site-Data` no logout, `logout-all` | Plano 3.6 (Sessão) e 3.3 (`WebSession`) (fatia 6) |
| Chaves: identificador público, hash, escopos, datas, último uso | Plano 3.6 (Chaves de API); `src/goblinshift/identity/tables.py` (`api_key`) |
| Auditoria: eventos listados, IP truncado /24 e /48, sem nomes nem conteúdo, 90 dias | Plano 3.6 (Auditoria), 3.3 (`AuditEvent`), 1.7 (Retenção) e 1.8 (Privacidade) (fatia 6) |
| Estado transitório do login no PostgreSQL por poucos minutos; cookie leva só a chave | Plano 3.6 (Sessão, último parágrafo) e 1.7 (Retenção) (fatia 6) |
| Arquivos com tamanho e SHA-256; nomes, parâmetros, resultado por arquivo; histórico; presets | Plano 3.3 (`Upload`, `Run`, `RunInput`, `StoredObject`, `Preset`) e 1.1 |
| Consumo calculado do histórico | Plano 3.3 (último parágrafo: cotas a partir de `Run` e `RunInput`) e 1.8 (Abuso e custo) |
| Conteúdo lido só pela receita, em contêiner isolado e sem rede | Plano 3.6 (Sandbox) |
| Metadados GPS em fotos como exemplo; remoção de metadados não é anonimização | Plano 1.2 (Image Forge: orientar por EXIF, remover metadados suportados) |
| Telemetria: eventos por requisição, tentativa, término; `user.hash` HMAC; o que nunca entra | Plano 3.7 (Eventos amplos); `src/goblinshift/entrypoints/api/wide_event.py` (docstring) |
| IP truncado /24 e /48 nos eventos; query string removida | `src/goblinshift/platform/telemetry.py` (`IPV4_PREFIX_BITS`, `IPV6_PREFIX_BITS`, `truncate_client_address`) |
| Telemetria na VPS, sem terceiros, 30 dias | Plano 3.7 (Backend de telemetria) e 1.8; ADR 013; `docker/otel-collector/config.yaml` (só exporta para o OpenObserve local) |
| Sem scripts de terceiros | Plano 3.6 (Cabeçalhos da aplicação: CSP `script-src 'self'; connect-src 'self'`) |
| Base legal execução de contrato | Plano 1.8 (Base legal e transparência) |
| Segurança como finalidade do IP truncado | Plano 1.8 ("por segurança do próprio serviço") |
| Tabela de retenção (1 h, 24 h, 48 h, 30 dias, 90 dias, 7 dias, 24 h, 30 dias) | Plano 1.7 (Retenção); `src/goblinshift/files/limits.py` (`INCOMPLETE_UPLOAD_TTL`, `READY_UPLOAD_TTL`, `ARTIFACT_TTL`, `USED_INPUT_TTL`); `src/goblinshift/runs/acceptance.py` (`IDEMPOTENCY_TTL`) |
| Arquivos sem backup | Plano 3.8 (Backups) |
| Hostinger, KVM 2, Campinas (SP) | Plano 3.8 (Servidor); ADR 011 |
| Backup: dump diário, cifrado com `age` antes do envio, chave só de escrita, 30 dias por ciclo de vida do bucket, chave privada fora da VPS | `docs/adr/022-backup-externo.md`, `deploy/backup/pg-backup.sh`, `deploy/backup/goblinshift-pg-backup.timer`; plano 3.8; plano 7-8, 2.2 |
| Backblaze B2 nos EUA | Plano 7-8, 2.2 (B2 recomendado; datacenters nos EUA ou UE); ADR 022 (alvo B2). A região é configuração (`BACKUP_S3_REGION`), fora do repositório |
| Restauração mensal decifrada em runner do GitHub Actions | `.github/workflows/restore-check.yml` (`runs-on: ubuntu-24.04`, `cron: "23 10 1 * *"`, serviço `postgres` efêmero) |
| healthchecks.io recebe só pings | ADR 022, Decisão 4 |
| Exclusão adiada de arquivos | Plano 1.1 (Exclusão) e 3.6 (Exclusão sempre adiada) |
| `DELETE /me`: login recente, revoga, cancela, remove em até 24 h; backup em até 30 dias | Plano 1.8 (Exclusão), 3.6 (Autenticação recente), 3.8 (Backups) (fatia 6) |
| Acesso por `GET /me`, histórico e presets | Plano 1.8 (Acesso); `src/goblinshift/entrypoints/api/me.py` (hoje só id, nome e escopos; consumo entra na fatia 6) |
| Cookies `__Host-gs_session` e `__Host-gs_csrf` e seus atributos | Plano 3.6 (Sessão, CSRF) (fatia 6) |
| HTTPS, banco sem porta exposta, segredos em hash | Plano 3.8 (Proxy de borda, Rede) e 3.6 |
| Nome "Conta Google" para contas Google; nome do GitHub (`login`) regravado a cada login; nome sem edição | `origin/fatia-6`: `src/goblinshift/identity/providers.py:34-35` (`GOOGLE_SCOPE`, `GOOGLE_DISPLAY_NAME`), `:78` e `:90`; `src/goblinshift/identity/users.py:62` (`ON CONFLICT ... set_ display_name`); `src/goblinshift/entrypoints/api/me.py:68` (só `GET /me`, sem rota de edição) |
| Cookie `__Secure-gs_login`: chave assinada, `HttpOnly`, `Secure`, `SameSite=Lax`, `Path=/auth`, 10 minutos; estado do login expira em 10 minutos | `origin/fatia-6`: `src/goblinshift/entrypoints/api/cookies.py:18-22`; `src/goblinshift/entrypoints/api/app.py:86-96` (`SessionMiddleware` do Starlette, que sempre marca `httponly`); `src/goblinshift/identity/login_state.py:21` (`LOGIN_STATE_TTL`) |
| Log de acesso do Nginx: IP truncado /24 e /48, só o caminho, sem Referer nem User-Agent; Uvicorn sem log de acesso | `docker/nginx/templates/access-log.conf.template` (`log_format privacy`); `docker/nginx/templates/default.conf.template:25`; `docker/app.Dockerfile:34` (`--no-access-log`); `tests/test_nginx_logs.py`; PR #12 |
| Log de erro do Nginx com a linha da requisição e o IP da borda | PR #12 ("Fora do escopo") |
| Proxy de borda sem log de acesso | `deploy/edge/Caddyfile` e `deploy/edge/conf.d/goblinshift.caddy` (sem diretiva `log`) |
| Logs: 5 × 10 MiB por serviço, cerca de 14 dias no volume previsto, somem ao recriar o container | `compose.yaml:11-15` (`x-logging`); plano 1.7 (Retenção e "Logs dos containers"); `tests/test_container_logs.py` |

### Divergências entre plano e código encontradas ao redigir

Todas as três encontradas no primeiro rascunho estão resolvidas na `main` (conferida em 2026-10-14,
`6c1c41f`), e o texto acima já descreve o comportamento atual.

1. **Logs de acesso com IP completo: resolvida pelo PR #12.** O Nginx usa o formato `privacy`
   (IP truncado em /24 no IPv4 e /48 no IPv6, só o caminho, sem query string, Referer nem
   User-Agent), e o Uvicorn roda com `--no-access-log`. Ressalva mantida no item 3.2: o log de
   **erro** do Nginx ainda inclui a linha da requisição, com a query string, quando a aplicação
   não responde; o `client:` dessa linha é o IP do proxy de borda, não o do usuário.
2. **Retenção dos logs: resolvida pelo PR #12.** A rotação passou para cada serviço do Compose
   (driver `local`, 5 arquivos de 10 MiB), e o `deploy/provision.sh` não escreve mais o
   `/etc/docker/daemon.json`, que é global na VPS compartilhada. O plano 1.7 trocou "14 dias" por
   "teto de 50 MiB por serviço, dimensionado para cerca de 14 dias no volume previsto", e o
   item 5 usa esse texto, sem prometer prazo fixo.
3. **IP na telemetria: resolvida pelo PR #16.** A truncagem do IP e a remoção da query string nos
   eventos estão na `main` (`src/goblinshift/platform/telemetry.py`, `truncate_client_address`).

Fica fora deste aviso, como nota operacional: o `deploy/edge/compose.yaml` (infraestrutura
compartilhada) não declara rotação própria de logs. O Caddy da borda não grava log de acesso,
então isso não muda o que o aviso declara.
