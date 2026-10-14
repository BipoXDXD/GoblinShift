# Aviso de Privacidade do GoblinShift

> **RASCUNHO — não publicar sem revisão jurídica humana.**
>
> Este texto foi redigido a partir do que o sistema faz (código e plano técnico), não por
> advogado(a). O plano exige revisão jurídica antes da abertura pública
> (`docs/GoblinShiftPlan.md`, 1.8; ADR 012). Trechos entre `[[A DEFINIR: ...]]` dependem de
> decisão do dono do serviço. Notas marcadas como **Nota para a revisão** são perguntas ao
> revisor e saem do texto final. As fontes internas de cada afirmação técnica estão no fim,
> junto com as divergências entre plano e código encontradas ao redigir.

## Pendências antes de publicar

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

- **Versão:** [[A DEFINIR (J-4): número da versão]]
- **Vigência:** [[A DEFINIR (J-4): data de publicação]]
- **Endereço do serviço:** [[A DEFINIR (J-5): domínio definitivo]]

Este aviso explica quais dados pessoais o GoblinShift trata, para quê, com base em quê, com quem
e por quanto tempo, e como você exerce seus direitos, conforme a Lei Geral de Proteção de Dados
Pessoais (Lei nº 13.709/2018, "LGPD").

## 1. Controlador e contato

- **Controlador:** [[A DEFINIR (J-16): nome completo ou razão social, CPF ou CNPJ, endereço]].
- **Canal para assuntos de privacidade e exercício de direitos:** [[A DEFINIR (J-6): e-mail]].
- **Encarregado (DPO):** [[A DEFINIR (J-17): nome e contato do encarregado, ou a declaração de
  dispensa. Nota para a revisão: a ANPD dispensa agentes de tratamento de pequeno porte de
  indicar encarregado, mas exige um canal de comunicação com o titular; confirmar o
  enquadramento e a redação.]]

## 2. Resumo

- Você entra com Google ou GitHub. **Não temos senha sua** e **não coletamos seu e-mail**.
- Da sua conta, guardamos só o identificador que o provedor nos dá e, no GitHub, o seu nome de
  usuário público.
- Seus arquivos e resultados são **privados** e **apagados em até 48 horas** depois de cada
  execução. Não há backup de arquivos.
- Você vê o que guardamos sobre você na própria conta e pode **excluir a conta** a qualquer
  momento.
- Usamos só **cookies estritamente necessários**. Não há publicidade, rastreadores nem
  analytics de terceiros.
- O servidor fica no Brasil. A única cópia que sai do país é o **backup cifrado do banco de
  dados**, guardado nos Estados Unidos por 30 dias.

## 3. Que dados tratamos

### 3.1. Dados da conta

| Dado | De onde vem | Observação |
|---|---|---|
| Identificador da conta no provedor (`issuer` + `subject`) | Google: o `sub` do login OpenID Connect. GitHub: o `id` numérico da conta | É a sua identidade no GoblinShift |
| Nome de exibição | GitHub: o seu nome de usuário público (`login`). Google: o texto fixo "Conta Google" | Para contas Google, nenhum nome é recebido, porque pedimos só o escopo `openid`; por isso todas aparecem como "Conta Google" |
| Estado da conta (`active` ou `blocked`) e data de criação | Gerado pelo serviço | |
| Versão dos Termos e do Aviso aceitos e data do aceite | Gerado no aceite | |

**Pedimos o mínimo aos provedores:** ao Google, só o escopo `openid`; ao GitHub, nenhum escopo.
O acesso concedido pelo GitHub serve para uma única consulta ao seu perfil público e é
descartado na hora, sem ser gravado. Os provedores de login não recebem acesso aos seus arquivos
no GoblinShift.

### 3.2. Dados de acesso e segurança

- **Sessão do navegador:** um identificador aleatório, guardado no banco só como hash, com
  datas de criação, do login no provedor, do último uso, de expiração e de revogação.
- **Chaves de API:** identificador público, hash do segredo (o segredo em si não é guardado),
  escopos, datas de criação, expiração, último uso e revogação.
- **Eventos de segurança (auditoria):** login e falha de login, logout, emissão e revogação de
  chave, recusas de segurança, negações de autorização, exclusão e bloqueio de conta. Cada
  evento guarda a conta, o tipo, o momento, o resultado e o **endereço IP truncado** (os
  últimos 8 bits no IPv4, ficando o /24; no IPv6, só o prefixo /48). Sem nomes de arquivo nem
  conteúdo.
- **Estado transitório do login:** dados técnicos do fluxo de login (`state`, `nonce`,
  verificador PKCE), guardados por poucos minutos.

### 3.3. Arquivos e execuções

- **Arquivos que você envia** e os **resultados** gerados, com tamanho e hash SHA-256.
- **Nomes originais dos arquivos**, os **parâmetros** que você escolheu e o resultado por
  arquivo (aceito, rejeitado e por quê).
- **Histórico resumido** de cada execução: receita, estado, datas e contagens.
- **Presets:** configurações que você salva, sem arquivos.
- **Consumo** da conta (execuções e bytes), calculado a partir do histórico para aplicar as
  cotas.

Os arquivos podem conter dados pessoais seus ou de terceiros (por exemplo, uma planilha com
nomes, ou fotos com localização GPS nos metadados). **Não olhamos o conteúdo**: ele é lido só
pelo processamento automático da receita que você escolheu, num ambiente isolado e sem rede.
Quanto aos dados de terceiros que você enviar, veja o item 6 dos Termos de Uso.

### 3.4. Telemetria e registros técnicos

- **Eventos de telemetria:** para medir disponibilidade e diagnosticar falhas, cada requisição,
  tentativa de processamento e término de execução gera um evento técnico com rota, método,
  código de resposta, tempos, bytes, códigos de erro e identificadores internos de execução.
  A conta aparece só como um **código pseudonimizado** (`user.hash`, um HMAC do identificador
  interno feito com um segredo do servidor), e o IP aparece **truncado** como na auditoria.
  **Nunca entram** nesses eventos: conteúdo, nomes de arquivo, valores de parâmetros, query
  string, cabeçalhos, tokens nem o IP completo.
- **Registros (logs) dos serviços:** mensagens técnicas da aplicação e dos componentes do
  servidor. O registro de acesso do servidor web guarda, por requisição, a hora, o IP
  **truncado** como na auditoria, o método, o caminho **sem a query string**, o código de
  resposta, o tamanho e os tempos da resposta; não guarda Referer, User-Agent nem outros
  cabeçalhos. A aplicação não mantém registro de acesso próprio, e o proxy de borda não grava
  registro de acesso. Quando o servidor web não consegue falar com a aplicação, o registro de
  erro dele inclui a linha da requisição, com a query string, mas o endereço anotado ali é o do
  proxy de borda, e não o seu.

### 3.5. O que **não** coletamos

- Senha: o login é sempre feito no Google ou no GitHub.
- E-mail, telefone, data de nascimento, documento ou endereço.
- Dados de pagamento: o serviço é gratuito.
- Cookies de publicidade, de analytics ou de terceiros; o site não carrega scripts de terceiros.
- Conteúdo, nomes de arquivo e parâmetros em telemetria.

## 4. Para que usamos e com que base legal

> **Nota para a revisão:** o plano adota "execução do contrato" como base principal. As demais
> bases abaixo são proposta deste rascunho e precisam de validação.

| Finalidade | Dados | Base legal (LGPD, art. 7º) |
|---|---|---|
| Criar a conta, autenticar e manter a sessão | Identificador do provedor, nome de exibição, sessão, chaves | Execução de contrato (inciso V) |
| Receber, processar e entregar seus arquivos; histórico e presets | Arquivos, nomes, parâmetros, histórico, presets | Execução de contrato (inciso V) |
| Aplicar cotas e limites justos entre usuários | Consumo da conta | Execução de contrato (inciso V) |
| Registrar o aceite dos Termos e do Aviso | Versão e data do aceite | Execução de contrato (inciso V) [[A DEFINIR (J-22) na revisão: ou exercício regular de direitos (inciso VI)]] |
| Proteger o serviço e as contas: auditoria de segurança, investigação de abuso, bloqueio | Eventos de segurança com IP truncado, estado da conta | Legítimo interesse (inciso IX), na segurança do próprio serviço e dos usuários [[A DEFINIR (J-23) na revisão: registrar a avaliação de legítimo interesse]] |
| Medir disponibilidade, diagnosticar falhas e alertar | Telemetria pseudonimizada, logs técnicos | Legítimo interesse (inciso IX) [[A DEFINIR (J-24) na revisão]] |
| Recuperar o banco de dados em caso de desastre | Cópia cifrada do banco (item 7) | Legítimo interesse (inciso IX) [[A DEFINIR (J-25) na revisão]] |
| Atender denúncias, ordens e obrigações legais | O que for estritamente necessário ao caso | Cumprimento de obrigação legal (inciso II) e exercício regular de direitos (inciso VI) |

Não usamos seus dados para publicidade, para traçar perfil, para treinar inteligência
artificial nem para decisões automatizadas que afetem seus interesses. Os bloqueios de cota e
de taxa são regras fixas e públicas, aplicadas igualmente a todas as contas.

## 5. Por quanto tempo guardamos

| Dado | Prazo | Como termina |
|---|---|---|
| Upload incompleto | 1 hora sem atividade | Arquivo e registro apagados |
| Upload enviado e não usado | 24 horas | Arquivo apagado |
| Arquivos usados, resultados, manifesto e relatório | 48 horas após o término da execução | Arquivos apagados |
| Nomes de arquivos, parâmetros e detalhes por arquivo | 30 dias (ou antes, se você excluir) | Campos apagados; fica o resumo da execução |
| Histórico resumido das execuções | 90 dias | Registros apagados |
| Presets | Até você excluir o preset ou a conta | Registro apagado |
| Estado transitório do login | 10 minutos | Apagado |
| Sessões encerradas (expiradas ou revogadas) | 7 dias após o fim | Registro apagado |
| Eventos de segurança (auditoria) | 90 dias | Registros apagados |
| Registros de idempotência (proteção contra pedido repetido) | 24 horas | Registro apagado |
| Telemetria | 30 dias | Expiração automática no servidor de telemetria |
| Logs técnicos dos serviços | Até 50 MiB por serviço (5 arquivos de 10 MiB), o que cobre cerca de 14 dias no volume previsto; com pouco tráfego, uma linha pode durar mais. Os logs também somem sempre que o serviço é recriado, o que acontece a cada atualização | Rotação por tamanho |
| Backup cifrado do banco de dados | 30 dias | Expiração automática no provedor do backup |
| Dados da conta | Enquanto a conta existir | Exclusão da conta (item 8) |

Os prazos acima são aplicados por rotinas automáticas de manutenção. Arquivos não têm backup:
depois de apagados, não podem ser recuperados.

[[A DEFINIR (J-26): se a conta for bloqueada e depois excluída, o identificador do provedor é mantido
para que o bloqueio continue valendo? Por quanto tempo? O plano diz que o bloqueio "impede
novos logins da mesma identidade", mas não diz o que acontece depois de `DELETE /me`.]]

## 6. Com quem compartilhamos

Não vendemos nem cedemos dados pessoais. Os dados passam pelos fornecedores abaixo, cada um
apenas no necessário para a sua função.

> **Nota para a revisão:** confirmar a qualificação de cada fornecedor (operador, suboperador
> ou controlador independente). Google e GitHub, como provedores de login, tratam seus dados
> também sob as políticas próprias, na relação direta que você tem com eles.

| Fornecedor | Papel | Que dados | Onde |
|---|---|---|---|
| **Hostinger** | Hospedagem do servidor (VPS) onde rodam a aplicação, o banco de dados, os arquivos e a telemetria | Todos os dados descritos no item 3 | Datacenter em Campinas (SP), Brasil |
| **Google** | Provedor de login (se você entra com Google) | O Google sabe que você entrou no GoblinShift; nos devolve o identificador `sub` | Conforme a política do Google |
| **GitHub** | Provedor de login (se você entra com GitHub) | O GitHub sabe que você entrou no GoblinShift; nos devolve o `id` e o nome de usuário públicos | Conforme a política do GitHub |
| **GitHub (Actions)** | Teste mensal de restauração do backup | Uma vez por mês, a cópia mais recente do banco é decifrada e restaurada num servidor temporário do GitHub Actions, para provar que o backup funciona; o servidor é descartado ao fim | Infraestrutura do GitHub, fora do controle de localização do GoblinShift [[A DEFINIR (J-19): confirmar localização e mencionar como transferência internacional]] |
| **Backblaze (B2)** | Armazenamento do backup do banco de dados | Cópia diária do banco, **cifrada antes de sair do servidor**; o Backblaze não tem a chave para decifrá-la | Estados Unidos [[A DEFINIR (J-19): confirmar a região do bucket]] |

A telemetria fica no próprio servidor, sem envio a terceiros. O serviço de monitoramento do
backup (healthchecks.io) recebe só um sinal de "backup feito" ou "falhou", sem dados pessoais.

Também podemos fornecer dados a autoridades quando a lei ou uma ordem judicial exigir, e no caso
de conteúdo de abuso sexual infantil (item 7 dos Termos de Uso).

## 7. Transferência internacional

O servidor principal fica no Brasil. Saem do país:

1. **O backup do banco de dados**, guardado no Backblaze B2, nos Estados Unidos, por 30 dias. Ele
   é cifrado no nosso servidor antes do envio, com uma chave cuja parte privada não fica nem no
   servidor nem no Backblaze; a chave que o servidor usa para enviar só pode gravar, não pode
   ler nem apagar backups. O banco contém os dados das seções 3.1 a 3.3 (não contém arquivos).
2. **A restauração mensal de teste** desse backup, feita no GitHub Actions (item 6).

[[A DEFINIR (J-19) com a revisão jurídica: mecanismo de transferência do art. 33 da LGPD (por exemplo,
cláusulas-padrão contratuais aprovadas pela ANPD, ou outro) para o Backblaze e para o GitHub
Actions.]]

## 8. Seus direitos e como exercê-los

A LGPD (art. 18) garante a você, entre outros, o direito de:

- confirmar se tratamos seus dados e **acessá-los**;
- **corrigir** dados incompletos, inexatos ou desatualizados;
- pedir **anonimização, bloqueio ou eliminação** de dados desnecessários, excessivos ou tratados
  em desconformidade com a lei;
- **portabilidade** dos dados;
- **eliminação** dos dados;
- saber com quem compartilhamos seus dados (item 6);
- **opor-se** a tratamento feito com base em legítimo interesse, se houver descumprimento da lei;
- peticionar à **Autoridade Nacional de Proteção de Dados (ANPD)**.

**Como exercer pela própria conta, sem pedir a ninguém:**

| Direito | Como |
|---|---|
| Acesso | A área da conta (`GET /me`), o histórico de execuções e a lista de presets mostram o que a conta guarda, incluindo o consumo |
| Portabilidade | Baixe os resultados e relatórios das execuções enquanto estiverem disponíveis; presets e histórico podem ser lidos pela API em JSON |
| Eliminação de arquivos | Excluir um arquivo ou resultado o torna indisponível na hora; a remoção física acontece assim que nenhuma execução ou download em andamento o estiver usando |
| Eliminação da conta | Excluir a conta (`DELETE /me`, exige login recente) revoga sessões e chaves, cancela a execução em andamento e remove arquivos, resultados, presets e histórico **em até 24 horas**. Cópias no backup do banco deixam de existir quando o backup expira, **em até 30 dias**; até lá elas só seriam usadas para recuperar o banco num desastre [[A DEFINIR (J-27) na revisão: o que fazer se uma restauração trouxer de volta uma conta excluída]] |
| Encerrar acessos | "Sair de todas as sessões" revoga todas as sessões e chaves da conta |

**Pelo canal de privacidade** ([[A DEFINIR (J-6): e-mail]]), para os demais pedidos, como correção,
oposição ou informações sobre compartilhamento. Como não temos seu e-mail, poderemos pedir que
você comprove o controle da conta (por exemplo, fazendo login e informando um código exibido na
conta) antes de atender. [[A DEFINIR (J-28): procedimento de verificação de identidade e prazo de
resposta; nota para a revisão: art. 19 da LGPD.]]

**Correção do nome de exibição:** para contas GitHub, ele é o seu nome de usuário no GitHub e é
atualizado a cada login; para corrigi-lo, altere-o no GitHub e entre de novo no GoblinShift. Para
contas Google, ele é sempre "Conta Google". O nome não é editável na conta.

## 9. Cookies

O GoblinShift usa só cookies **estritamente necessários** ao funcionamento e à segurança. Por
isso não pedimos consentimento para eles, e o serviço não funciona sem eles.

| Cookie | Para quê | Duração |
|---|---|---|
| `__Host-gs_session` | Mantém você conectado. Contém um identificador aleatório; é `HttpOnly`, `Secure` e `SameSite=Lax` | Até 30 minutos sem uso ou 8 horas após o login; apagado no logout |
| `__Host-gs_csrf` | Protege contra ações feitas em seu nome por outros sites (CSRF). `Secure` e `SameSite=Strict` | Trocado a cada sessão |
| `__Secure-gs_login` | Existe só durante o login: guarda, assinada, só uma chave que aponta para o estado do login enquanto você está no Google ou no GitHub. É `HttpOnly`, `Secure` e `SameSite=Lax`, e o navegador só o envia às rotas de login (`/auth`) | 10 minutos; o estado a que ele aponta também expira em 10 minutos |

Não usamos cookies de publicidade, de analytics ou de terceiros. Ao sair, o serviço pede ao
navegador que apague os cookies e o armazenamento do site.

## 10. Segurança

Entre as medidas adotadas: conexão sempre cifrada (HTTPS); arquivos privados, sem links
públicos, com acesso conferido a cada pedido; processamento num contêiner descartável e isolado,
sem rede e sem credenciais; segredos de sessões e chaves guardados só como hash; backup cifrado;
banco de dados sem porta exposta à internet. Nenhuma medida é absoluta. Em caso de incidente de
segurança que possa gerar risco ou dano relevante, comunicaremos a ANPD e os titulares
afetados, na forma da lei [[A DEFINIR (J-15): como os titulares serão avisados, já que não temos
e-mail; por exemplo, aviso na própria conta e no site]].

## 11. Crianças e adolescentes

[[A DEFINIR (J-18): idade mínima e tratamento de dados de crianças e adolescentes (LGPD, art. 14). O
serviço não coleta idade.]]

## 12. Mudanças neste aviso

Cada versão tem número e data. Quando o aviso mudar de versão, pediremos novo aceite no login
seguinte. [[A DEFINIR (J-14): onde fica o histórico de versões.]]

---

## Fontes internas

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
