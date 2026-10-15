# Termos de Uso do GoblinShift

- **Versão:** [[A DEFINIR: número da versão]]
- **Vigência:** [[A DEFINIR: data de publicação]]
- **Endereço do serviço:** [[A DEFINIR: domínio definitivo]]

## 1. Quem oferece o serviço

O GoblinShift é oferecido por [[A DEFINIR: nome completo ou razão social do controlador, CPF ou
CNPJ, e endereço]] ("nós"). Contato geral, denúncias e pedidos sobre dados pessoais:
[[A DEFINIR: e-mail de contato]].

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

O processamento roda num ambiente isolado, sem acesso à rede. Não há inteligência artificial no
processamento, nem scripts enviados por você.

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
   vulnerabilidades sem autorização prévia por escrito
   [[A DEFINIR: haverá política de divulgação responsável de vulnerabilidades? Se sim, indicar
   o canal e a referência aqui]];
8. distribuir malware ou usar o serviço como intermediário de qualquer ataque.

[[A DEFINIR: outras condutas proibidas que o dono queira listar.]]

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

**Como denunciar:** escreva para [[A DEFINIR: e-mail de denúncias]] com o máximo de informação
que puder (o que, quando, e qualquer identificador de execução ou de arquivo que você tenha).
Não anexe nem reenvie conteúdo de abuso sexual infantil: descreva-o.
[[A DEFINIR: prazo de resposta às denúncias.]]

## 8. Suspensão e encerramento

- **Por nós.** Podemos bloquear a conta que violar estes Termos, que puser em risco o serviço
  ou outros usuários, ou quando a lei exigir. [[A DEFINIR: haverá aviso prévio ou canal de
  recurso para o bloqueio, salvo nos casos do item 7?]]
- **Por você.** Você pode excluir sua conta a qualquer momento pela própria conta (operação
  `DELETE /me`, que exige login recente). A exclusão revoga sessões e chaves, cancela a
  execução em andamento e remove arquivos, resultados, presets e histórico em até 24 horas.
  Cópias no backup do banco de dados deixam de existir quando o backup expira, em até 30 dias
  (detalhes no Aviso de Privacidade).
- **Fim do serviço.** Por ser um projeto de portfólio gratuito, o serviço pode ser alterado,
  suspenso ou encerrado. [[A DEFINIR: aviso prévio mínimo em caso de encerramento.]]

## 9. Conteúdo e propriedade

- Os arquivos que você envia continuam seus. Você nos autoriza apenas a armazená-los e
  processá-los pelo tempo e para a finalidade necessários para executar as receitas que você
  pedir e entregar os resultados a você.
- Você declara ter o direito de enviar e processar os arquivos que envia.
- O código do GoblinShift é público. [[A DEFINIR: licença do código-fonte; o repositório ainda
  não tem arquivo de licença]]. Nome, marca e ilustrações do GoblinShift
  [[A DEFINIR: condições de uso da marca e das ilustrações]].

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
aceite no login seguinte. [[A DEFINIR: antecedência mínima de aviso para mudanças relevantes e
onde o histórico de versões fica publicado.]]

## 13. Lei aplicável e foro

Estes Termos são regidos pelas leis da República Federativa do Brasil.
[[A DEFINIR: foro.]]
