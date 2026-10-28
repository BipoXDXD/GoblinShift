# Executor-ctl: política administrativa do Coolify

A configuração carregada por `executor_ctl` aponta **somente** para
`/var/lib/coolify-release/goblinshift/executor-images.json`. O leitor reabre a
política a cada `start`, percorrendo de `/` até o pai por descritores relativos
com `O_DIRECTORY` e `O_NOFOLLOW`. Cada diretório precisa ter UID/GID 0:0, sem
escrita de grupo/outros. A folha precisa ser regular, UID/GID 0:0, modo **0600**,
um único link, sem symlink e no máximo **64 KiB**. A abertura não bloqueante
recusa FIFO sem prender o processo root.

O único esquema é `{"allowed": ["<imagem>@sha256:<64 hex minúsculos>"]}`:
chaves JSON duplicadas, campos adicionais, tipos incorretos e referências
mutáveis são recusados. A sintaxe de referências imutáveis anterior permanece;
nenhum digest aprovado é descartado pelo leitor. Uma lista vazia recusa todo
`start` e o instalador recusa continuar com ela. Somente o ingresso
administrativo compartilhado pode atualizar essa política; os scripts de deploy
legado não recebem acesso ao novo destino.

`load_config` também usa leitura ancorada, com a mesma cadeia root 0:0 e folha
regular de um link, limitada a 64 KiB. **A configuração pode ser 0644**, se não
tiver escrita de grupo/outros; **a política exige 0600**. O esquema completo da
configuração e as chaves duplicadas são validados. O caminho de política antigo,
um caminho alternativo ou uma grafia com `./` não são aceitos.

Exemplo de configuração administrativa a preparar e revisar pelo operador:

```json
{
  "socket": "/run/goblinshift/executor-ctl.sock",
  "group": "gs-exec",
  "work_area": "/srv/goblinshift/exec-work",
  "runtime": "runsc",
  "images_file": "/var/lib/coolify-release/goblinshift/executor-images.json"
}
```

O instalador executa preflight somente leitura, usando `load_config` completo e
o leitor da política do checkout, antes de `getent`, `groupadd`, `usermod`,
`install`, qualquer escrita ou `systemctl`. Configuração ausente/legada/inválida,
política ausente/insegura/vazia/inválida interrompem com indicação de migração
administrativa. Ele não cria configuração, diretórios de política ou uma lista
vazia, não copia a política antiga e não tenta corrigir donos/permissões.
Depois de preflight válido, mantém o comportamento de atualização do pacote/unit
e serviço; por isso **executar o instalador pode reiniciar o executor-ctl** e
exige a transição drenada abaixo. Esta entrega não executa o instalador.

## Migração futura sob passagem exclusiva do operador

1. Qualificar a composição final de leitor e receptor compartilhado, versão/hash
   único e senders correspondentes. Os resultados de um head anterior não
   validam esta composição. Guardar identidades dos arquivos instalados e retorno
   administrativo compatível; não instalar somente metade da mudança.
2. Drenar o ingresso e as tentativas em curso com o coordenador/operador69.
   Inventariar todas as referências ainda necessárias a tentativas aceitas,
   inclusive anteriores. Preservar cópia identificada da política/configuração
   anterior e dos recibos; o diretório antigo UID1001 permanece intacto.
3. Preparar a nova cadeia e política como administração root, a partir de
   referências aprovadas e de estado aceito confiável. Conferir cada digest e
   preservar o conjunto necessário; não fazer cópia cega do arquivo sob pai
   UID1001 e não reduzir a política ao único digest do próximo release. Alinhar
   o caminho literal em leitor/escritor e preservar socket, `runsc`, `work_area`,
   cotas, imagens, backups e serviços não envolvidos.
4. Executar a transição de serviço somente sob procedimento aprovado, após a
   drenagem. `Config` é carregada uma vez por `__main__.py`/`ExecutorService`:
   editar JSON em disco não muda a configuração do processo existente.
5. Antes de reabrir ingresso, identificar PID/instância e bytes instalados,
   correlacionar a transição com o arquivo de configuração aprovado e produzir
   prova de que **esse leitor ativo abre a nova política** em uma aceitação
   controlada. A observação de abertura relativa/descritores pelo PID, com
   ferramenta já disponível e autorização própria do operador, deve vincular o
   caminho administrativo à operação do processo. JSON em disco, `systemctl
   active`, um novo PID ou teste unitário, isoladamente, não provam o leitor
   ativo. Sem esse recibo, a ativação permanece pendente.

## Limites de retorno

Restaurar as imagens antigas do produto não restaura automaticamente o leitor,
receptor, configuração ou caminho de política antigos. O retorno precisa manter
o plano administrativo revisado e seus digests aprovados, comprovar compatibilidade
com as imagens antigas e usar envelope/hash do receptor realmente instalado.
Não há fallback para `/srv/goblinshift/executor/images.json`, dual-write,
auto-repair, remoção do legado ou promoção da confiança de UID1001. Ausência de
retorno compatível, bytes exatos de imagem ou prova de leitor ativo mantém o
bloqueio; esta fonte não demonstra rollback de produção.

## Verificação e pendências

Os testes de `test_images.py`, `test_config.py` e `test_install_preflight.py`
usam metadados root **sintéticos**, claramente separados da proteção real do
host. As aberturas relativas, recusa de symlinks/hardlinks/FIFO, schema e ordem
do instalador são exercitadas; as cópias temporárias do instalador usam stubs
nas primeiras fronteiras de mutação, nunca sudo ou serviços. O teste de Docker
existente usa a mesma fixture sintética apenas para suas políticas temporárias;
nenhum teste de Docker foi executado nesta entrega.

`tests/executor_ctl/test_policy_native.py` é fonte de aceitação **pendente** para
runner Linux descartável com root: usa donos/permissões reais em diretório
privado de `/root`, sem mocks, serviços ou imagem. Inclui UID/GID1001, escrita
externa, 0644 indevido na política, symlinks, hardlink, FIFO, reabertura e
preservação de dois digests. Mac ou Linux sem root pulam os casos e não geram
prova nativa. A execução nesse runner exige sua própria passagem; nenhuma
workflow/CI foi criada ou disparada aqui. A aceitação real da transição do daemon,
compatibilidade compartilhada e preservação operacional continua com o operador.
