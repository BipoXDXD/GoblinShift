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
um caminho alternativo ou uma grafia com `./` não são aceitos. O `runtime` só
aceita `runsc` (gVisor, ADR 004): `runc` é recusado no arquivo, e portanto no
preflight do instalador, mesmo com `require_mount` desligado.

O `group` (`gs-exec`, GID 61001) é o da área de trabalho e o do `--user` do
executor; o `socket_group` (`gs-ctl`, GID 61002) é só o do socket de controle,
e precisa ser outro grupo: quem escapa do sandbox fica com o primeiro e não
conecta no socket (D11). O instalador cria os dois grupos e põe o usuário
`goblinshift` nos dois; worker e manutenção recebem os dois no `group_add`.

Exemplo de configuração administrativa a preparar e revisar pelo operador:

```json
{
  "socket": "/run/goblinshift/executor-ctl.sock",
  "group": "gs-exec",
  "socket_group": "gs-ctl",
  "work_area": "/srv/goblinshift/exec-work",
  "runtime": "runsc",
  "images_file": "/var/lib/coolify-release/goblinshift/executor-images.json"
}
```

### Teto de I/O de disco (opcional, D13)

A área de trabalho fica no mesmo disco do banco. A chave opcional `io_limits`
limita o quanto um executor lê e escreve nele; **sem a chave não há teto**, e o
`docker run` sai exatamente como antes. Com ela, as quatro chaves são
obrigatórias e cada valor é um inteiro positivo (bytes/s e operações/s):

```json
"io_limits": {"read_bps": 104857600, "write_bps": 52428800, "read_iops": 2000, "write_iops": 1000}
```

O dispositivo sai do `st_dev` de `work_area` (o loop device do filesystem de
tamanho fixo da área, ADR 005), seguindo o link do udev `/dev/block/<major>:<minor>`
até o nó real (`/dev/loopN`): o Docker separa dispositivo e valor no primeiro
`:` de `--device-read-bps`, e o link com `:` faria todo start falhar. O ctl
resolve o nó na subida e **não sobe** (host não pronto) se o resultado não for
um caminho em `/dev/` sem `:`; depois, resolve de novo a cada start, sob o lock
da área, porque uma remontagem pode trocar o loopN. As quatro flags são
`--device-read-bps`, `--device-write-bps`, `--device-read-iops` e
`--device-write-iops`.

**Como medir (operador, no host):** pela sonda de disco da aceitação, que roda
com as mesmas opções do executor, inclusive o teto. Sem a chave, rode a
aceitação e leia a evidência da sonda `ENOSPC aos 6 GiB` (GiB escritos e
segundos): isso é a vazão normal de escrita da área. Ligue `io_limits` com
valores abaixo dela (sugestão: metade), reinstale (Tarefa 26) e rode a
aceitação de novo: a mesma sonda mostra a vazão já com o teto. Cuidado: a sonda
escreve 6 GiB e tem prazo de 900 s, então um `write_bps` abaixo de ~7 MB/s
(6 GiB / 900 s ≈ 7,2 MB/s) a reprova por prazo, não por defeito. A carga do
Image Forge (`image_forge_load.py`) também roda com o teto e mostra se ele
cabe no teto de 10 min de uma tentativa.

O efeito do teto com gVisor (`runsc`) sobre loop device só se prova no host: o
throttling é do cgroup do container (onde ficam o sentry e o gofer), e a escrita
com cache de página chega ao disco pelo writeback. Se a sonda com o teto não
mostrar vazão menor, registrar isso e voltar sem a chave.

O instalador executa preflight somente leitura, usando `load_config` completo e
o leitor da política do checkout, antes de `getent`, `groupadd`, `usermod`,
`install`, qualquer escrita ou `systemctl`. Configuração ausente/legada/inválida,
política ausente/insegura/vazia/inválida interrompem com indicação de migração
administrativa. Ele não cria configuração, diretórios de política ou uma lista
vazia, não copia a política antiga e não tenta corrigir donos/permissões.
Com `require_mount` ligado (o padrão), o preflight também confere a área com a
regra do próprio ctl: `work_area` montada e com `nosuid,nodev,noexec` em
`/proc/self/mountinfo` do host; sem isso, recusa e aponta `deploy/filesystems.sh`
e a remontagem. Antes dos grupos, `/opt/goblinshift` e o prefixo do pacote
precisam ser do root e sem escrita para grupo ou outros.
Depois de preflight válido, mantém o comportamento de atualização do pacote/unit
e serviço; por isso **executar o instalador pode reiniciar o executor-ctl** e
exige a transição drenada abaixo. Esta entrega não executa o instalador.

## Atualização desta versão (ordem)

Esta versão separa o socket no grupo `gs-ctl` (D11), exige a área com
`nosuid,nodev,noexec` (D12) e troca a unit. A ordem importa:

1. **Deploy da app** (Coolify), com o `group_add` de worker e manutenção em
   61001 e 61002. A app nova funciona com o ctl velho; o contrário não: o ctl
   novo põe o socket no `gs-ctl`, e a app velha toma `EACCES` nele.
2. **Remontar `exec-work` com `nosuid,nodev,noexec`.** O `deploy/filesystems.sh`
   grava essas opções numa linha nova do `/etc/fstab`, mas numa linha antiga só
   avisa: acrescente as três à linha à mão, remonte
   (`sudo mount -o remount /srv/goblinshift/exec-work`) e confira
   com `findmnt -no OPTIONS /srv/goblinshift/exec-work`. O ctl novo recusa subir
   sem as três opções, e o instalador recusa trocar o pacote antes disso.
3. **`"socket_group": "gs-ctl"`** no JSON da configuração administrativa.
4. **Reinstalar o ctl** (`sudo bash deploy/executor-ctl/install.sh`).

Os passos 3 e 4 vão na mesma janela drenada: o parser do ctl velho recusa a
chave nova, e um restart dele entre os dois passos o deixaria fora do ar. A unit
nova traz `KillMode=mixed` (o SIGTERM vai só ao processo principal, que termina
as requisições em voo), `TimeoutStopSec=210` (acima do pior caso, o stop de
175 s) e o marcador `.restart-pending` no prefixo, que faz a execução seguinte
do instalador reiniciar o serviço se a anterior falhou no meio.

## Container alheio com o nome da tentativa (409 `foreign_container`)

O ctl só age sobre o container que ele mesmo criou: nome `gs-exec-<id>`, label
`goblinshift.attempt=<id>` e, no `start`, a imagem pedida. Um container com o
nome da tentativa sem essa label (de outro projeto do host, ou de um engano de
operador) faz `start`, `status`, `stop` e `remove` responderem 409
`foreign_container` sem tocá-lo, e o journal registra um aviso com o nome.

O que fazer (operador, no host): inspecionar antes de qualquer coisa,
`docker inspect --format '{{.Config.Image}} {{json .Config.Labels}} {{.State.Status}}' gs-exec-<id>`,
descobrir de quem é e, se for mesmo sobra ou engano, removê-lo à mão com
`docker rm gs-exec-<id>` (ou `docker stop` antes, se roda). **Nunca pelo ctl**:
o ctl não tem como saber de quem é, e por isso recusa. Enquanto o nome estiver
tomado, a tentativa não sobe: para o worker a recusa é `RunnerError` com o
código `foreign_container`, a tentativa termina como falha transitória e a
manutenção não toca no container.

Exceção: um container com a label da tentativa e **outra imagem** é da
tentativa. Só o `start` o recusa (409 `foreign_container`, motivo "outra imagem"
no journal); `status`, `stop` e `remove` agem sobre ele normalmente, e a
manutenção pode pará-lo e removê-lo pelo ctl.

O `remove` sem container (`absent`) não chama `docker rm`: só apaga
`<area>/<id>`. Assim um container alheio que tome o nome entre o `inspect` e o
`rm` não é apagado.

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
