# Aviso de Privacidade do GoblinShift

- **Versão:** v5
- **Vigência:** 2026-11-05
- **Endereço do serviço:** https://goblinshift.dev

Este aviso explica quais dados pessoais o GoblinShift trata, para quê, com base em quê, com quem
e por quanto tempo, e como você exerce seus direitos, conforme a Lei Geral de Proteção de Dados
Pessoais (Lei nº 13.709/2018, "LGPD").

## 1. Controlador e contato

- **Controlador:** Return to Monkey Corporation (CNPJ em registro), de Curitiba (PR).
- **Canal para assuntos de privacidade e exercício de direitos:** privacidade@goblinshift.dev.
- **Encarregado (DPO):** não indicamos encarregado. Como agente de tratamento de pequeno porte,
  o controlador está dispensado dessa indicação (Resolução CD/ANPD nº 2/2022, art. 11), e o canal
  acima é o canal de comunicação com os titulares.

## 2. Resumo

- Você entra com Google ou GitHub. **Não temos senha sua** e **não coletamos seu e-mail** na
  conta; só o recebemos se você nos escrever (item 3.6).
- Da sua conta, guardamos só o identificador que o provedor nos dá e, no GitHub, o seu nome de
  usuário público.
- Seus arquivos e resultados são **privados** e **apagados em até 48 horas** depois de cada
  execução. Não há backup de arquivos.
- Você vê o que guardamos sobre você na própria conta e pode **excluir a conta** a qualquer
  momento.
- Usamos só **cookies estritamente necessários**. Não há publicidade, rastreadores nem
  analytics de terceiros.
- O servidor fica no Brasil, mas o site passa pela **Cloudflare**, que recebe as conexões antes
  dele. Ficam fora do país o **backup cifrado do banco de dados**, guardado nos Estados Unidos
  por até 31 dias, e, se você nos escrever, a **mensagem de e-mail**, guardada na Zoho, em local que
  não controlamos.
- O serviço é só para maiores de 18 anos.

## 3. Que dados tratamos

### 3.1. Dados da conta

| Dado | De onde vem | Observação |
|---|---|---|
| Identificador da conta no provedor (`issuer` + `subject`) | Google: o `sub` do login OpenID Connect. GitHub: o `id` numérico da conta | É a sua identidade no GoblinShift |
| Nome de exibição | GitHub: o seu nome de usuário público (`login`). Google: o texto fixo "Conta Google" | Para contas Google, nenhum nome é recebido, porque pedimos só o escopo `openid`; por isso todas aparecem como "Conta Google" |
| Estado da conta (`active` ou `blocked`) e data de criação | Gerado pelo serviço | Enquanto o cadastro estiver fechado, a conta nova fica como `blocked`, à espera de liberação |
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
  resposta do servidor web e o da aplicação, o tamanho e os tempos da resposta; não guarda
  Referer, User-Agent nem outros cabeçalhos. A aplicação não mantém registro de acesso próprio,
  e o proxy de borda não grava registro de acesso. Nas requisições, o registro de erro do
  servidor web só anota falhas graves do próprio servidor, como falta de espaço em disco, de
  memória ou de arquivos abertos. Nesses casos raros, a linha pode trazer o seu **IP completo** e
  a linha da requisição, com a query string.
- **Visitantes sem conta:** quem só abre o site ou tenta entrar também gera eventos de telemetria
  e registros de acesso, sempre com o IP truncado. Um evento de segurança de login recusado pode
  ficar sem conta associada.
- **Cloudflare:** antes de chegar ao nosso servidor, cada conexão passa pela Cloudflare (item 6),
  que abre a cifra HTTPS e recebe o **IP completo**, os cabeçalhos e o conteúdo da requisição. O
  truncamento descrito neste aviso vale para o que nós guardamos, não para o que a Cloudflare
  trata no trânsito.

### 3.5. O que **não** coletamos

- Senha: o login é sempre feito no Google ou no GitHub.
- E-mail, telefone, data de nascimento, documento ou endereço; o e-mail só chega se você nos
  escrever (item 3.6).
- Dados de pagamento: o serviço é gratuito.
- Cookies de publicidade ou de analytics; o site não carrega scripts de terceiros. O único cookie
  de terceiro possível é o de segurança da Cloudflare (item 9).
- Conteúdo, nomes de arquivo e parâmetros em telemetria.

### 3.6. Dados que você nos envia por e-mail

Se você escrever para um dos nossos endereços (contato@, abuso@ ou privacidade@goblinshift.dev),
recebemos o seu endereço de e-mail, o que você escrever e o que anexar. Usamos esses dados só
para responder e tratar o seu pedido, e não os juntamos à sua conta: como não guardamos e-mail
na conta, não temos como ligar uma mensagem a uma conta, a não ser pelo que você mesmo
informar. Guardamos a conversa por 12 meses depois do fim dela.

Esses endereços ficam numa caixa de e-mail da Zoho, contratada pelo controlador, onde as
mensagens são recebidas e guardadas (item 6).

## 4. Para que usamos e com que base legal

| Finalidade | Dados | Base legal (LGPD, art. 7º) |
|---|---|---|
| Criar a conta, autenticar e manter a sessão | Identificador do provedor, nome de exibição, sessão, chaves | Execução de contrato (inciso V) |
| Receber, processar e entregar seus arquivos; histórico e presets | Arquivos, nomes, parâmetros, histórico, presets | Execução de contrato (inciso V) |
| Aplicar cotas e limites justos entre usuários | Consumo da conta | Execução de contrato (inciso V) |
| Entregar o site com proteção contra ataques | IP, requisição e conteúdo em trânsito, tratados pela Cloudflare (item 6) | Execução de contrato (inciso V) |
| Registrar o aceite dos Termos e do Aviso | Versão e data do aceite | Execução de contrato (inciso V) e exercício regular de direitos (inciso VI), para provar o aceite |
| Proteger o serviço e as contas: auditoria de segurança, investigação de abuso, bloqueio | Eventos de segurança com IP truncado, estado da conta | Legítimo interesse (inciso IX), na segurança do próprio serviço e dos usuários; avaliação registrada, com resumo disponível a pedido |
| Medir disponibilidade, diagnosticar falhas e alertar | Telemetria pseudonimizada, logs técnicos | Legítimo interesse (inciso IX); avaliação registrada, com resumo disponível a pedido |
| Recuperar o banco de dados em caso de desastre e testar essa recuperação | Cópia cifrada do banco (item 7) | Execução de contrato (inciso V), para manter a conta, os presets e o histórico que você usa |
| Responder a quem nos escreve sobre o serviço | Endereço de e-mail, mensagem e anexos (item 3.6) | Execução de contrato ou procedimentos preliminares a pedido do titular (inciso V) |
| Atender pedidos sobre dados pessoais | Endereço de e-mail, mensagem e anexos (item 3.6) | Cumprimento de obrigação legal (inciso II) |
| Receber e apurar denúncias | Endereço de e-mail, mensagem e anexos (item 3.6) | Exercício regular de direitos (inciso VI) e, quando houver, obrigação legal (inciso II) |
| Manter o bloqueio de uma conta | Identificador do provedor, nome de exibição e estado da conta | Exercício regular de direitos (inciso VI) |
| Atender denúncias, ordens e obrigações legais | O que for estritamente necessário ao caso | Cumprimento de obrigação legal (inciso II) e exercício regular de direitos (inciso VI) |

Não usamos seus dados para publicidade, para traçar perfil, para treinar modelos de aprendizado
de máquina nem para decisões automatizadas que afetem seus interesses. Os bloqueios de cota e
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
| Eventos de segurança (auditoria) | 90 dias, inclusive depois da exclusão da conta | Registros apagados |
| Registros de idempotência (proteção contra pedido repetido) | 24 horas | Registro apagado |
| Telemetria | 30 dias | Expiração automática no servidor de telemetria |
| Logs técnicos dos serviços da aplicação | Até 50 MiB por serviço (5 arquivos de 10 MiB), o que cobre cerca de 14 dias no volume previsto; com pouco tráfego, uma linha pode durar mais. Os logs também somem sempre que o serviço é recriado, o que acontece a cada atualização | Rotação por tamanho |
| Mensagens técnicas do proxy de borda do nosso servidor | Sem teto de tamanho nem prazo fixo: ficam até o proxy ser recriado. Ele não grava registro de acesso, mas uma mensagem de erro pode trazer o endereço e o caminho da requisição | Recriação do proxy |
| Backup cifrado do banco de dados | Até 31 dias: cada cópia fica imutável por 30 dias, é ocultada nesse prazo e apagada 1 dia depois | Expiração automática no provedor do backup |
| Mensagens que você nos envia por e-mail (item 3.6) | 12 meses depois do fim da conversa | Mensagens apagadas da caixa de e-mail |
| Registros preservados em caso de abuso sexual infantil (item 7 dos Termos de Uso) | O prazo que a autoridade indicar; sem indicação, 6 meses a partir da comunicação | Registros apagados |
| Conta bloqueada ou à espera de liberação: identificador do provedor, nome de exibição e estado | Enquanto o bloqueio ou a espera durar, com revisão ao menos uma vez por ano | Liberação da conta ou exclusão |
| Dados da conta | Enquanto a conta existir | Exclusão da conta (item 8) |

Os prazos acima são aplicados por rotinas automáticas de manutenção. Arquivos não têm backup:
depois de apagados, não podem ser recuperados.

**Conta bloqueada.** Uma conta bloqueada não entra mais e, por isso, não consegue usar a
exclusão pela própria conta. O identificador do provedor fica guardado para que o bloqueio
continue valendo, e os demais dados seguem os prazos acima. Você pode pedir a revisão do bloqueio
ou a eliminação pelo canal de privacidade. Nesse caso, nós pedimos a exclusão da conta, que segue
o mesmo caminho do item 8. Atendemos, salvo se a conservação for necessária para
cumprir obrigação legal ou para exercer direitos em processo (LGPD, arts. 7º, II e VI, e 16, I).
Depois da exclusão de uma conta, não guardamos nenhum identificador do provedor; se a mesma
pessoa voltar a entrar, será uma conta nova, sujeita a novo bloqueio.

## 6. Com quem compartilhamos

Não vendemos nem cedemos dados pessoais. Os dados passam pelos fornecedores abaixo, cada um
apenas no necessário para a sua função. **Operador** trata dados em nosso nome. **Controlador
independente** decide por conta própria como trata os dados, na relação direta que você tem com
ele, sob a política dele.

| Fornecedor | Papel | Que dados | Onde |
|---|---|---|---|
| **Hostinger** (operador) | Hospedagem do servidor (VPS) onde rodam a aplicação, o banco de dados, os arquivos e a telemetria | Todos os dados descritos no item 3 | Datacenter em Campinas (SP), Brasil |
| **Cloudflare** (operador) | DNS, proxy reverso e proteção do site: recebe as conexões, abre a cifra HTTPS e repassa ao servidor | IP completo, cabeçalhos e conteúdo das requisições e respostas em trânsito, inclusive arquivos enviados e baixados, e cookies. O navegador também pode enviar à Cloudflare relatórios de falha de rede, com o endereço do site e o IP | Rede global da Cloudflare, fora do controle de localização do GoblinShift |
| **Google** (controlador independente) | Provedor de login (se você entra com Google) | O Google sabe que você entrou no GoblinShift; nos devolve o identificador `sub` | Conforme a política do Google |
| **GitHub** (controlador independente) | Provedor de login (se você entra com GitHub) | O GitHub sabe que você entrou no GoblinShift; nos devolve o `id` e o nome de usuário públicos | Conforme a política do GitHub |
| **GitHub (Actions)** (operador) | Teste mensal de restauração do backup | Uma vez por mês, a cópia mais recente do banco é decifrada e restaurada num servidor temporário do GitHub Actions, para provar que o backup funciona; o servidor é descartado ao fim | Infraestrutura do GitHub, fora do Brasil, em região que não controlamos |
| **Backblaze (B2)** (operador) | Armazenamento do backup do banco de dados | Cópia diária do banco, **cifrada antes de sair do servidor**; o Backblaze não tem a chave para decifrá-la | Estados Unidos |
| **Zoho (Zoho Mail)** (operador) | Recebimento e guarda dos e-mails enviados a contato@, abuso@ e privacidade@goblinshift.dev | O que você nos escrever (item 3.6): seu endereço de e-mail, a mensagem e os anexos | Conforme a política da Zoho; fora do controle de localização do GoblinShift |

A telemetria fica no próprio servidor, sem envio a terceiros. O serviço de monitoramento do
backup (healthchecks.io) recebe só um sinal de "backup feito" ou "falhou", sem dados pessoais, e
os alertas técnicos enviados a nós trazem só o nome e a descrição do alerta.

Também podemos fornecer dados a autoridades quando a lei ou uma ordem judicial exigir, e no caso
de conteúdo de abuso sexual infantil (item 7 dos Termos de Uso).

## 7. Transferência internacional

O servidor principal fica no Brasil. Saem do país, ou podem sair:

1. **O tráfego do site**, que passa pela Cloudflare (item 6). O processamento dos seus arquivos
   fica no servidor no Brasil, mas não controlamos por quais pontos da rede da Cloudflare a sua
   conexão passa.
2. **O backup do banco de dados**, guardado no Backblaze B2, nos Estados Unidos, por até 31 dias. Ele
   é cifrado no nosso servidor antes do envio, com uma chave cuja parte privada não fica nem no
   servidor nem no Backblaze; a chave que o servidor usa para enviar só pode gravar, não pode
   ler nem apagar backups. O banco contém os dados das seções 3.1 a 3.3 (não contém arquivos).
3. **A restauração mensal de teste** desse backup, feita no GitHub Actions (item 6).
4. **As mensagens que você nos envia por e-mail** (item 3.6), guardadas numa caixa de e-mail da
   Zoho. Não controlamos em que país esse fornecedor trata a mensagem.

Essas transferências se apoiam no art. 33, IX, da LGPD: são necessárias para executar o contrato
com você, cumprir obrigação legal ou exercer direitos (art. 7º, II, V e VI). Google e GitHub, como
provedores de login, tratam os dados como controladores independentes, na relação direta que
você mantém com eles.

## 8. Seus direitos e como exercê-los

A LGPD (art. 18) garante a você, entre outros, o direito de:

- confirmar se tratamos seus dados e **acessá-los**;
- **corrigir** dados incompletos, inexatos ou desatualizados;
- pedir **anonimização, bloqueio ou eliminação** de dados desnecessários, excessivos ou tratados
  em desconformidade com a lei;
- **portabilidade** dos dados;
- **eliminação** dos dados;
- saber com quem compartilhamos seus dados (item 6);
- **opor-se** a tratamento feito com base em legítimo interesse, se houver descumprimento da lei
  (art. 18, § 2º);
- peticionar à **Autoridade Nacional de Proteção de Dados (ANPD)**.

**Como exercer pela própria conta, sem pedir a ninguém:**

| Direito | Como |
|---|---|
| Acesso | A área da conta (`GET /me`), o histórico de execuções e a lista de presets mostram o que a conta guarda, incluindo o consumo |
| Portabilidade | Baixe os resultados e relatórios das execuções enquanto estiverem disponíveis; presets e histórico podem ser lidos pela API em JSON |
| Eliminação de arquivos | Excluir um arquivo ou resultado o torna indisponível na hora; a remoção física acontece assim que nenhuma execução ou download em andamento o estiver usando |
| Eliminação da conta | Excluir a conta (`DELETE /me`, exige login recente) revoga sessões e chaves, cancela a execução em andamento e remove arquivos, resultados, presets e histórico **em até 24 horas**. Os eventos de segurança (item 3.2) continuam guardados por até 90 dias depois da exclusão, com o identificador interno da conta e o IP truncado, para proteger o serviço. Cópias no backup do banco deixam de existir quando o backup expira, **em até 31 dias**; até lá elas só seriam usadas para recuperar o banco num desastre. Uma cópia não sabe das exclusões feitas depois dela, e o registro delas pode se perder junto com o banco. Por isso, depois de uma restauração, publicamos no site a data da cópia usada, e quem excluiu a conta depois dessa data precisa repetir o pedido pela conta ou pelo canal de privacidade. Se ainda tivermos registro dessas exclusões fora da cópia, nós as refazemos antes de reabrir o serviço |
| Encerrar acessos | "Sair de todas as sessões" revoga todas as sessões e chaves da conta |

**Pelo canal de privacidade** (privacidade@goblinshift.dev), para os demais pedidos, como correção,
oposição ou informações sobre compartilhamento. Como não temos seu e-mail, confirmamos a sua
identidade pelo próprio login: você entra no GoblinShift e nos informa o identificador da conta.
Para quem não consegue entrar (por exemplo, conta bloqueada), vale o código da tela de login
recusado. Confirmação de tratamento e acesso em forma simplificada são respondidos de imediato;
os demais pedidos e a declaração completa, em **até 15 dias** contados do pedido com a identidade
confirmada (art. 19). Quando não pudermos atender, respondemos com o motivo.

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

Não usamos cookies de publicidade nem de analytics. Se a Cloudflare identificar tráfego suspeito,
ela pode gravar um cookie técnico próprio de segurança (como `__cf_bm` ou `cf_clearance`), usado
só para distinguir pessoas de robôs. Ao sair, o serviço pede ao navegador que apague os cookies e
o armazenamento do site.

## 10. Segurança

Entre as medidas adotadas: conexão sempre cifrada (HTTPS); arquivos privados, sem links
públicos, com acesso conferido a cada pedido; processamento num contêiner descartável e isolado,
sem rede e sem credenciais; segredos de sessões e chaves guardados só como hash; backup cifrado;
banco de dados sem porta exposta à internet. Nenhuma medida é absoluta. Em caso de incidente de
segurança que possa gerar risco ou dano relevante, comunicaremos a ANPD e os titulares afetados
em até 3 dias úteis do conhecimento do incidente, por aviso destacado no site, porque não temos
o seu e-mail.

## 11. Crianças e adolescentes

O GoblinShift é só para maiores de 18 anos e não é dirigido a crianças e adolescentes. O serviço
não coleta idade: ao aceitar os Termos, você declara ter 18 anos ou mais. Se soubermos que uma
conta pertence a uma pessoa com menos de 18 anos, ela é bloqueada e excluída em até 15 dias; as
cópias no backup expiram como no item 5. Pai, mãe ou responsável pode avisar pelo canal de privacidade.

## 12. Mudanças neste aviso

Cada versão tem número e data. Quando o aviso mudar de versão, pediremos novo aceite no login
seguinte.

Mudanças relevantes são avisadas no site com pelo menos 15 dias de antecedência, salvo se a lei
ou a segurança do serviço exigirem algo mais rápido. O histórico das versões está no fim deste
texto e em [https://github.com/BipoXDXD/GoblinShift/commits/main/docs/legal](https://github.com/BipoXDXD/GoblinShift/commits/main/docs/legal).

## Histórico de versões

| Versão | Data | Resumo |
|---|---|---|
| `v1-rascunho` | 2026-10-14 | Primeiro rascunho, redigido a partir do que o sistema faz |
| `v2-rascunho` | 2026-10-15 | Identificação do controlador; domínio e canal de privacidade; dados enviados por e-mail (item 3.6) e o prazo de 12 meses; eventos de segurança guardados por 90 dias depois da exclusão da conta; Backblaze nos Estados Unidos; aviso de incidente no site; antecedência de 15 dias para mudanças e este histórico |
| `v3-rascunho` | 2026-10-15 | Provedor dos endereços de e-mail identificado: ImprovMX (encaminhamento) e Google/Gmail (caixa de entrada), nos itens 2, 3.6, 6 e 7 |
| `v4-rascunho` | 2026-10-21 | Redação editorial e referência ao registro de licenças atualizadas; novo aceite conjunto, sem alterar aceites anteriores |
| `v5` | 2026-11-05 | Primeira versão vigente, sem marcações pendentes: Cloudflare incluída como fornecedora; Zoho no lugar de ImprovMX e Gmail; dispensa de encarregado; bases legais completas; papel de cada fornecedor; mecanismo da transferência internacional; conta bloqueada; restauração de backup; verificação de identidade e prazo de 15 dias; serviço só para maiores de 18 anos; identificação do controlador |
