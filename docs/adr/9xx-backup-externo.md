# ADR 9xx. Backup externo do PostgreSQL por timer do systemd, cifrado com age

- **Estado:** Proposto (fatia 8, entrega antecipada, 2026-10-14). O número definitivo sai no merge, depois dos ADRs das fatias 5 e 6.
- **Plano:** [3.8](../GoblinShiftPlan.md#s3-8) (Backups), [3.7](../GoblinShiftPlan.md#s3-7) (alerta de causa "backup diário que falhou")
- **Relacionado:** [ADR 011](011-vps-kvm2-compartilhada.md) (VPS compartilhada), [ADR 013](013-openobserve-telemetria.md) (telemetria na VPS)

## Contexto

O plano pede dump diário cifrado do PostgreSQL em armazenamento de outro provedor, com 30 dias de retenção e restauração testada todo mês. O backup semanal e os snapshots da Hostinger ficam no mesmo provedor e não bastam (regra 3-2-1). Já há dado real na VPS desde a fatia 3. O alvo é o Backblaze B2 (API S3, 10 GB gratuitos, chave de aplicação com capacidades finas), escolhido no plano das fatias 7 e 8; os dados saem do Brasil, o que entra no aviso de privacidade.

Três decisões: **onde agenda**, **como cifra e envia** e **onde restaura**.

## Decisão

1. **Agendamento: timer do systemd no host** (`goblinshift-pg-backup.timer`, 06:00 UTC, por volta das 03h de Brasília, com `RandomizedDelaySec=30min` e `Persistent=true`), que roda `deploy/backup/pg-backup.sh` do checkout implantado como o usuário `goblinshift`. O backup independe da aplicação: continua rodando com a API, o worker ou a manutenção quebrados, que é quando ele mais importa.
2. **Dump, cifra e envio só com ferramentas do host:** `docker exec` no container do PostgreSQL roda `pg_dump --format=custom`, o `age` cifra para a chave pública versionada em `deploy/backup/age-recipients.txt` e o `curl` envia com assinatura AWS SigV4 (`--aws-sigv4`). A credencial entra pelo stdin do curl, nunca pela linha de comando. A VPS usa uma **chave de aplicação só de escrita** (`writeFiles`, sem `readFiles`, `listFiles` nem `deleteFiles`): uma VPS invadida não lê nem apaga os backups. A expiração em 30 dias é regra de ciclo de vida do bucket.
3. **Restauração mensal fora da VPS:** o workflow `restore-check.yml` do GitHub Actions baixa o dump mais recente com uma chave só de leitura, decifra com a chave privada (secret do environment `backup-restore`), restaura num `postgres:18.6-trixie` efêmero (a mesma imagem do `compose.yaml`) e confere: destino vazio antes, `alembic_version` conhecida pelo código, contas e execuções acima de zero e dump com menos de 26 h. Isso prova também que a chave privada guardada fora da VPS funciona.
4. **Alertas pelo healthchecks.io**, fora da VPS: o backup pinga `/start`, sucesso ou `/fail` no check "backup diário" (período de 1 dia, tolerância de 2 h); a restauração faz o mesmo no check "restauração mensal" (período de 31 dias). Sem ping de sucesso, o healthchecks avisa sozinho, inclusive se a VPS inteira cair.

## Alternativas descartadas

| Alternativa | Prós | Motivo da recusa |
|---|---|---|
| Serviço no Compose (container com cron, como o `supercronic`) | Fica junto da pilha; sobe e desce com `docker compose` | Mais uma imagem para manter e fixar por digest; o container precisaria do socket do Docker ou da senha do banco e das ferramentas `age`/`curl`; para quando a pilha para. O timer do host não tem nenhum desses custos |
| Ciclo no processo `maintenance` | Reaproveita um processo existente, com telemetria pronta | Acopla o backup à saúde da aplicação: o processo que falha é o mesmo que deveria salvar o banco. Exigiria `pg_dump` e `age` na imagem da aplicação |
| GitHub Actions puxando o dump por SSH | Nada agendado na VPS | A chave de deploy da CI só executa `deploy.sh` (forced command); abrir outro comando amplia a superfície de quem invade o GitHub. O dump trafegaria em claro até o runner |
| `rclone` ou CLI da AWS para o upload | Upload multipart, retomada | Pacote a mais no host; o `rclone` faz HEAD e listagem por padrão, que a chave só de escrita não permite. Um PUT do curl com SigV4 cobre até 5 GB, folga grande sobre um banco de 10 GiB comprimido |
| `gpg` em vez de `age` | Disponível em todo lugar | Configuração e chaves bem mais complexas; o `age` tem um formato só, chaves curtas e está no Ubuntu 24.04 |

## Consequências

- **Chave privada é ponto único de perda:** perdê-la perde todos os backups. Fica em dois lugares fora da VPS (gerenciador de senhas e secret `BACKUP_AGE_IDENTITY`). A restauração mensal prova que a cópia do GitHub funciona.
- **Enquanto `age-recipients.txt` não tiver chave, o backup falha de propósito** e avisa pelo healthchecks.io, em vez de enviar texto claro.
- **O dump passa por um arquivo temporário** (`PrivateTmp`), porque o S3 não aceita corpo chunked num PUT simples. Ele já sai cifrado do pipe, então nunca toca o disco em claro.
- **Revisão "conhecida", não "igual à da `main`":** uma migração que entra na `main` depois do último dump não reprova a restauração; uma revisão que o código não conhece, sim.
- **Testes locais usam o SeaweedFS** como S3 (as imagens do MinIO deixaram de ser publicadas), com três identidades que imitam as chaves do B2: a da VPS só escreve, a da restauração só lê e lista. O teste não prova a ausência de `deleteFiles`, que é configuração da chave no B2.
- **Dados fora do Brasil:** o B2 tem datacenters nos EUA e na UE; o aviso de privacidade e a revisão jurídica precisam tratar disso (plano das fatias 7 e 8, 2.1).

## Como verificar

- `uv run pytest tests/backup`: backup e restauração reais contra PostgreSQL e S3 locais (dump cifrado, sem texto claro no objeto, credencial recusada, dump que falha, destinatário ausente, configuração incompleta, dump velho, revisão desconhecida, chave errada, destino não vazio), com os pings conferidos num healthchecks falso.
- Na VPS, depois de `sudo bash deploy/backup/install.sh` com a configuração preenchida: `sudo systemctl start goblinshift-pg-backup.service` e o objeto aparece no bucket; o check "backup diário" fica verde.
- A primeira execução manual do workflow `restore-check.yml` termina verde, e o resultado vai para o README.
