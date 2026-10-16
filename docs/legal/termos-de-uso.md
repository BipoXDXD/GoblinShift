# Termos de Uso do GoblinShift

- **Versão:** v3-rascunho
- **Vigência:** 2026-10-15
- **Endereço do serviço:** https://goblinshift.dev

## 1. Quem oferece o serviço

O GoblinShift é oferecido por Luca Sebastian Pimentel Perez, pessoa física, de Curitiba (PR)
("nós"), que é o controlador dos dados pessoais tratados no serviço. Canal do controlador:
privacidade@goblinshift.dev.

Contato geral: contato@goblinshift.dev. Denúncias: abuso@goblinshift.dev.
Pedidos sobre dados pessoais: privacidade@goblinshift.dev.

O GoblinShift é um projeto de portfólio, gratuito, com código-fonte público. Não há plano pago
nem cobrança de nenhum tipo.

## 2. Aceite destes termos

Ao entrar pela primeira vez, você lê e aceita estes Termos e o
[Aviso de Privacidade](aviso-de-privacidade.md). Registramos a versão aceita e a data do
aceite. Quando os Termos mudarem de versão, pediremos um novo aceite no login seguinte; sem
ele, o uso não continua.

Se você não concorda com estes Termos, não use o serviço.

[[A DEFINIR: idade mínima para usar o serviço e o que fazer com contas de menores. Google e
GitHub têm idade mínima própria, mas o GoblinShift não recebe a data de nascimento e não tem
como verificar idade.]]

## 3. O que o serviço faz

O GoblinShift processa lotes de arquivos por **receitas** fixas (por exemplo, preparar imagens,
limpar tabelas CSV e organizar downloads) e entrega os resultados de forma privada, com
manifesto e relatório. O catálogo publicado mostra só as receitas disponíveis, com formatos,
parâmetros e limites em vigor.

O processamento roda num ambiente isolado, sem acesso à rede. Não há processamento externo no
processamento, nem scripts enviados por você, e nenhum arquivo seu é enviado a serviço de
processamento externo. As ilustrações do site foram geradas com ajuda de inteligência
artificial (ver [https://goblinshift.dev/creditos](https://goblinshift.dev/creditos)).

## 4. Conta e acesso

- **Login só por Google ou GitHub.** O GoblinShift não tem senha própria. Você entra com uma
  conta Google ou GitHub, e a primeira entrada cria sua conta, sem convite.
- **Uma conta por provedor.** Entrar por Google e por GitHub cria **duas contas separadas**,
  cada uma com seus arquivos, histórico e cotas. Não juntamos contas, nem por e-mail.
- **Sessão.** A sessão no navegador expira depois de 30 minutos sem uso ou 8 horas depois do
  login, o que vier primeiro. Você pode encerrar todas as suas sessões e chaves de uma vez.
- **Chaves de API.** Você pode criar até 5 chaves para usar a API por programa. A chave aparece
  uma única vez, vale 30 dias por padrão e no máximo 90, e só pode ser criada logo depois de um
  login recente. Guarde-a como uma senha: quem tiver a chave age em nome da sua conta, dentro
  dos escopos dela.
- **Sua responsabilidade.** Você responde pelo uso feito com sua conta e suas chaves. Se
  suspeitar de uso indevido, revogue as chaves, encerre as sessões e nos avise.

## 5. Limites, cotas e disponibilidade

O serviço roda num único servidor, com capacidade finita, e nenhuma conta pode monopolizá-lo.
Por isso há limites, publicados no catálogo e sujeitos a ajuste, entre eles:

- uma execução em andamento por conta;
- por conta, 30 execuções e 2 GiB de entrada processada a cada 24 horas corridas;
- 4 GiB de armazenamento por conta;
- limites de tamanho por arquivo e por lote, de requisições por minuto e de transferências
  simultâneas;
- até 100 presets por conta.

Quando um limite é atingido, o pedido é recusado na hora, com o motivo. Uma execução aceita que
não começar em até 2 horas termina sem ser processada.

**Seus arquivos são temporários.** Arquivos enviados e resultados ficam disponíveis por, no
máximo, **48 horas** depois do término da execução; upload não usado expira em 24 horas. Depois
disso são apagados, e **não há backup de arquivos**. Guarde os originais e baixe os resultados
de que precisar dentro do prazo.

Não há links públicos de compartilhamento: só a sua conta acessa seus arquivos.

## 6. Uso aceitável

Você concorda em **não** usar o GoblinShift para:

1. enviar, processar ou armazenar conteúdo ilegal, incluindo, sem limitação, **qualquer conteúdo
   de abuso ou exploração sexual de crianças e adolescentes** (ver item 7);
2. enviar conteúdo que viole direitos de terceiros (autorais, de imagem, de privacidade, de
   proteção de dados) sem ter autorização para isso;
3. tratar dados pessoais de terceiros sem base legal para isso; se você envia arquivos com dados
   pessoais de outras pessoas, você é o responsável por esse tratamento perante elas;
4. contornar cotas, limites ou bloqueios, inclusive criando ou usando contas adicionais para
   esse fim;
5. fazer uso automatizado abusivo, como volume de requisições pensado para degradar o serviço
   para os outros usuários;
6. tentar acessar contas, arquivos ou dados de outras pessoas, ou contornar a autenticação e a
   autorização;
7. tentar escapar do ambiente isolado de processamento, atacar o servidor ou a rede, ou testar
   vulnerabilidades sem autorização prévia por escrito. Quem encontrar uma falha e a comunicar
   de boa-fé pelo canal da nossa política de divulgação
   ([https://goblinshift.dev/.well-known/security.txt](https://goblinshift.dev/.well-known/security.txt)), sem acessar dados de outras pessoas nem
   degradar o serviço, tem autorização para testá-la nos limites dessa política;
8. distribuir malware ou usar o serviço como intermediário de qualquer ataque;
9. emprestar, vender ou publicar chaves de API, ou oferecer o serviço a terceiros como se fosse
   seu.

## 7. Abuso sexual infantil e outros conteúdos ilegais

Conteúdo de abuso ou exploração sexual de crianças e adolescentes é proibido sem exceção.
Ao tomarmos conhecimento desse conteúdo, por denúncia ou de outro modo:

- a conta é **bloqueada**: o bloqueio revoga sessões e chaves, cancela a execução em andamento e
  impede novos logins da mesma identidade;
- o acesso ao conteúdo é **bloqueado imediatamente**;
- preservamos os registros e o material necessários e comunicamos às autoridades competentes,
  na forma da lei [[A DEFINIR: quais autoridades e canais (por exemplo,
  Polícia Federal e Ministério Público), o que preservar, por quanto tempo, e como conciliar a
  preservação com a expiração automática de 48 horas]].

Para os demais conteúdos ilegais, adotamos as mesmas medidas que forem cabíveis. O GoblinShift
não faz, nesta versão, varredura automatizada do conteúdo dos arquivos para moderação: a
contenção de abuso se apoia em limites, privacidade dos arquivos, expiração curta e denúncias.

**Como denunciar:** escreva para abuso@goblinshift.dev com o máximo de informação que puder (o
que, quando, e qualquer identificador de execução ou de arquivo que você tenha). Não anexe nem
reenvie conteúdo de abuso sexual infantil: descreva-o.

Respondemos a denúncias em até 3 dias úteis. Denúncias de abuso sexual infantil têm prioridade
e são tratadas assim que forem lidas.

## 8. Suspensão e encerramento

- **Por nós.** Podemos bloquear a conta que violar estes Termos, que puser em risco o serviço
  ou outros usuários, ou quando a lei exigir. Não temos o seu e-mail e por isso não avisamos
  antes de bloquear. Se você acha que o bloqueio foi um engano, escreva para
  contato@goblinshift.dev com o código da tela de login e o provedor (Google ou GitHub), e
  analisamos o caso. Essa regra não vale para o item 7.
- **Por você.** Você pode excluir sua conta a qualquer momento pela própria conta (operação
  `DELETE /me`, que exige login recente). A exclusão revoga sessões e chaves, cancela a
  execução em andamento e remove arquivos, resultados, presets e histórico em até 24 horas.
  Cópias no backup do banco de dados deixam de existir quando o backup expira, em até 30 dias
  (detalhes no Aviso de Privacidade).
- **Fim do serviço.** Por ser um projeto de portfólio gratuito, o serviço pode ser alterado,
  suspenso ou encerrado. Se o serviço for encerrado, avisaremos no site com pelo menos 30 dias
  de antecedência, salvo por motivo de segurança, ordem legal ou caso fortuito.

## 9. Conteúdo e propriedade

- Os arquivos que você envia continuam seus. Você nos autoriza apenas a armazená-los e
  processá-los pelo tempo e para a finalidade necessários para executar as receitas que você
  pedir e entregar os resultados a você.
- Você declara ter o direito de enviar e processar os arquivos que envia.
- O código do GoblinShift é público e licenciado sob a Apache License 2.0
  ([https://github.com/BipoXDXD/GoblinShift/blob/main/LICENSE](https://github.com/BipoXDXD/GoblinShift/blob/main/LICENSE)). A licença do código não cobre as
  ilustrações nem os modelos 3D.
- O nome GoblinShift, o logotipo e os nomes dos personagens não podem ser usados de modo que
  sugira vínculo ou endosso. As ilustrações e os modelos 3D do site são gerados com ajuda de
  processamento externo e editados por nós; a nossa parte do trabalho está sob CC BY 4.0, sem
  garantia de que a saída de produção editorial seja protegível por direito autoral. As goblins Jade e
  Fagulha ficam fora dessa licença. Origem e detalhes em
  [https://github.com/BipoXDXD/GoblinShift/blob/main/docs/art/LICENSE-ART.md](https://github.com/BipoXDXD/GoblinShift/blob/main/docs/art/LICENSE-ART.md).
- Se você acha que algo publicado no site (ilustração, modelo 3D ou texto) viola um direito
  seu, escreva para abuso@goblinshift.dev identificando o material e o direito. Analisamos o
  pedido e retiramos o que for procedente.

## 10. Ausência de garantias e limitação de responsabilidade

- O GoblinShift é oferecido **gratuitamente e no estado em que se encontra**, como projeto de
  portfólio, sem garantia de disponibilidade contínua, de prazo de processamento ou de
  adequação a uma finalidade específica.
- Os resultados são produzidos por regras fixas e documentadas, mas **confira-os antes de usar**
  em qualquer coisa importante. Por exemplo, a remoção de metadados de imagens retira os
  metadados suportados, mas não é anonimização.
- Os arquivos são apagados após o prazo do item 5 e **não têm backup**. Não use o GoblinShift
  como único local de guarda de nenhum arquivo.
- Na extensão permitida pela lei, não respondemos por perda de arquivos, lucros cessantes ou
  danos indiretos decorrentes do uso ou da indisponibilidade do serviço. Nada nestes Termos
  limita responsabilidade que a lei não permita limitar, como a decorrente de dolo ou culpa
  grave.

## 11. Privacidade

O tratamento de dados pessoais está descrito no [Aviso de Privacidade](aviso-de-privacidade.md),
que faz parte destes Termos.

## 12. Mudanças nestes Termos

Podemos atualizar estes Termos. Cada versão tem número e data; a mudança de versão pede novo
aceite no login seguinte.

Mudanças relevantes são avisadas no site com pelo menos 15 dias de antecedência, salvo se a lei
ou a segurança do serviço exigirem algo mais rápido. O histórico das versões está no fim deste
texto e em [https://github.com/BipoXDXD/GoblinShift/commits/main/docs/legal](https://github.com/BipoXDXD/GoblinShift/commits/main/docs/legal).

## 13. Lei aplicável e foro

Estes Termos são regidos pelas leis da República Federativa do Brasil.
[[A DEFINIR: foro.]]

## Histórico de versões

| Versão | Data | Resumo |
|---|---|---|
| `v1-rascunho` | 2026-10-14 | Primeiro rascunho, redigido a partir do que o sistema faz |
| `v2-rascunho` | 2026-10-15 | Identificação de quem oferece o serviço; domínio e e-mails de contato; política de divulgação de vulnerabilidades; proibição de ceder chaves de API; prazo de resposta às denúncias; recurso contra bloqueio; aviso de 30 dias antes do fim do serviço; licença Apache 2.0 do código e CC BY 4.0 da arte; pedido de retirada de material do site; antecedência de 15 dias para mudanças e este histórico |
| `v3-rascunho` | 2026-10-15 | Sem mudança de cláusula: o aceite é um só para os dois textos, e a versão sobe junto com o Aviso de Privacidade |
