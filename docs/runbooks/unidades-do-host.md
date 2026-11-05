# Unidades auxiliares do host

A VPS roda serviços de monitoramento e backup fora do Coolify e fora de qualquer repositório. Eles não vão com a imagem nem com o Compose: numa reinstalação do host, o código volta do backup dele, sem estas correções. Este roteiro guarda o que foi corrigido em 2026-11-05, para reaplicar.

Nada aqui passa pela release. Tudo roda na VPS, como root. Antes de cada troca, guarde o arquivo atual em `/root/vps-fix-<data>/`; reverter é copiá-lo de volta. Nenhum passo reinicia gateway, Traefik, Coolify, Docker ou container de aplicação.

Confira o estado com:

```bash
systemctl --failed --no-legend --plain
journalctl -u healthchecks-supervisor -n 3 --no-pager -o cat
```

## Envio de e-mail: `healthchecks-supervisor` e `openclaw-healthchecks-review`

**Sintoma:** o supervisor falha a cada minuto com `ModuleNotFoundError: No module named 'email_watch'`, e nenhum aviso sai. Ele só importa o módulo quando há problema a notificar, então a falha aparece justamente quando o aviso importa.

**Causa:** o `email_watch.py` saiu do host quando o monitor antigo de encaminhamento foi aposentado, em 2026-10-25, e `supervise.py` e `review.py` continuaram importando dele.

**Correção:** criar `/usr/local/lib/project-heartbeats/mail_send.py`, sobre o runtime do `current-vps-mail-notifier`, com a credencial e o remetente que ele já usa:

```python
"""Envio dos avisos da VPS pelo runtime CURRENT, sucessor do email_watch aposentado em 2026-10-25."""
import sys

RUNTIME_DIR = '/opt/current-vps-mail-notifier'


def send(cfg, subject, text, event_id):
    sys.path.insert(0, RUNTIME_DIR)
    from mail_runtime import credentials, resend
    key, sender = credentials()
    outcome = resend(key, {'from': sender, 'to': [cfg['recipient']],
                           'subject': '[Auditoria VPS] ' + subject, 'text': text},
                     'healthchecks-supervisor/' + event_id)
    if not outcome.get('accepted'):
        raise RuntimeError('envio recusado: ' + str(outcome.get('error')))
    return outcome['id']
```

Em `/usr/local/lib/project-heartbeats/supervise.py` (função `send_event`) e em `/opt/openclaw-healthchecks-2026/review.py`, trocar as duas linhas, mantendo a indentação:

```python
# antes
sys.path.insert(0,'/var/lib/forwarding-mail-monitor')
from email_watch import send
# depois
sys.path.insert(0,'/usr/local/lib/project-heartbeats')
from mail_send import send
```

**Prova:** aceite do provedor não é entrega. O teste só vale com a mensagem na caixa de entrada do destinatário já configurado. O envio precisa rodar com o sandbox do serviço (`ProtectSystem=strict`, `ProtectHome=true`, `NoNewPrivileges=true`), não num shell de root. O `review.py` usa o mesmo caminho, mas só envia quando tem saída pendente, e esse caminho não foi exercitado depois da troca.

## Pins do `current-vps-mail-notifier`

**Sintoma:** a cada minuto, `{"status": "SOURCE_UNQUALIFIED"}`, e nenhum aviso sai.

**Causa:** é a trava funcionando. `current_mailer.py` fixa o SHA-256 de `monitor.py`, `ops.py` e `monitor-kingbipo.yaml`, em `/opt/openclaw-ops-2026`, e recusa rodar se algum mudar. **Toda mudança nesses três arquivos exige repetir este passo**, senão os avisos param em silêncio.

**Correção:** primeiro, confirme que o parser ainda entende a fonte: `parse_source` sobre `/var/lib/openclaw-ops-2026/monitor-v2.json` precisa devolver a amostra com `__source__` verdadeiro, e todo check da política precisa existir em `checks`. Só então troque `MONITOR_SHA256`, `OPS_SHA256` e `CONFIG_SHA256` em `/opt/current-vps-mail-notifier/current_mailer.py` pelos hashes atuais. Trocar o hash sem essa conferência só esconde a falha.

**Prova:** o serviço termina com sucesso e o e-mail da próxima mudança de estado chega à caixa de entrada.

## `kingbipo-image-scan`

**Sintoma:** `IndexError: list index out of range` em `scanner_identity`, ou `cpu_pressure_persistent`, e os recibos do monitor envelhecem.

**Causa:** a poda diária de imagens apaga `aquasec/trivy`, que só existe enquanto roda, e a identidade do scanner vinha vazia. Além disso, a espera por folga de CPU olhava só o PSI, que serviços com `CPUQuota` baixa inflam com a máquina ociosa.

**Correção:** em `/usr/local/sbin/kingbipo-image-scan`:

```diff
@@ -17,6 +17,8 @@
 MAX_SCAN_AGE_S = 26 * 3600
 MAX_DB_AGE_S = 48 * 3600
 SCAN_TIMEOUT_S = 900
+PULL_TIMEOUT_S = 600
+IDLE_BUSY_PERCENT = 50
 DOCKER_RUN = ["docker", "run", "--rm", "--memory", "1500m", "--cpus", "0.5",
               "-v", CACHE + ":/root/.cache/trivy"]
 
@@ -47,17 +49,39 @@
     return {"digest": "sha256:" + digest.hexdigest(), "updated_at": updated}
 
 
+def ensure_scanner_image():
+    # A poda diária de imagens da VPS remove o Trivy, que só existe enquanto roda.
+    if run(["docker", "image", "inspect", TRIVY_IMAGE]).returncode == 0:
+        return
+    if run(["docker", "pull", "-q", TRIVY_IMAGE], timeout=PULL_TIMEOUT_S).returncode != 0:
+        raise RuntimeError("scanner_image_unavailable")
+
+
 def scanner_identity():
+    ensure_scanner_image()
     image_id = run(["docker", "image", "inspect", "--format", "{{.Id}}", TRIVY_IMAGE]).stdout.strip()
     return {"name": "trivy", "version": TRIVY_IMAGE.split(":")[1], "binary_sha256": image_id.split(":")[1]}
 
 
+def cpu_times():
+    fields = [int(value) for value in open("/proc/stat").readline().split()[1:9]]
+    return sum(fields), fields[3] + fields[4]
+
+
+def cpu_busy_percent():
+    total_before, idle_before = cpu_times()
+    time.sleep(2)
+    total_after, idle_after = cpu_times()
+    return 100 * (1 - (idle_after - idle_before) / max(1, total_after - total_before))
+
+
 def wait_for_cpu_headroom():
     # O supervisor do OpenClaw para o gateway com PSI avg10 > 50 e CPU > 85% por 30 s.
     deadline = time.monotonic() + 600
     while time.monotonic() < deadline:
         avg10 = float(open("/proc/pressure/cpu").readline().split()[1].split("=")[1])
-        if avg10 < 20:
+        # Serviços com CPUQuota baixa inflam o PSI mesmo com a máquina ociosa.
+        if avg10 < 20 or cpu_busy_percent() < IDLE_BUSY_PERCENT:
             return
         time.sleep(15)
     sys.exit("cpu_pressure_persistent")
```

**Prova:** o log termina com `"errors": 0`, e `/var/lib/kingbipo-image-scan/scan-state.json` tem recibos novos para todas as imagens em execução. A poda continua apagando a imagem; o script a baixa de novo a cada execução que precisar dela.

## `openclaw-daily-backup-2026`

**Sintoma:** o serviço morre por `start operation timed out` depois de 20 minutos, com cerca de 2 minutos de CPU, e deixa um `snapshot-*` em `/var/backups/openclaw-private-2026`.

**Causa:** com o container `openclaw-private-2026` parado de forma não limpa, a cópia do banco do agente pela API de backup do SQLite não termina, e um `-shm` antigo é o suspeito. Leitura com `immutable=1` passou. A linha exata do travamento no SQLite não foi identificada.

**Correção:** em `/opt/openclaw-ops-2026/backup.py`:

```diff
@@ -1,9 +1,17 @@
 #!/usr/bin/python3
 """Daily online SQLite backup of agent only; never copies live database files blindly."""
-import datetime,fcntl,hashlib,json,os,pathlib,shutil,sqlite3,tarfile,tempfile,time
+import datetime,fcntl,hashlib,json,os,pathlib,shutil,sqlite3,subprocess,tarfile,tempfile,time
 OUT=pathlib.Path('/var/backups/openclaw-private-2026');STATE=pathlib.Path('/var/lib/openclaw-private-2026')
+def gateway_running():
+ try:return subprocess.run(['docker','inspect','-f','{{.State.Running}}','openclaw-private-2026'],capture_output=True,text=True,timeout=15).stdout.strip()!='false'
+ except (OSError,subprocess.SubprocessError):return True
+def source_uri(src):
+ # Com o gateway parado, um -shm antigo trava a leitura normal; WAL vazio torna a leitura imutável segura.
+ wal=pathlib.Path(str(src)+'-wal')
+ idle=not wal.exists() or wal.stat().st_size==0
+ return 'file:'+str(src)+('?mode=ro&immutable=1' if idle and not gateway_running() else '?mode=ro')
 def sqlite_copy(src,dst):
- with sqlite3.connect('file:'+str(src)+'?mode=ro',uri=True,timeout=10) as a,sqlite3.connect(dst) as b:
+ with sqlite3.connect(source_uri(src),uri=True,timeout=10) as a,sqlite3.connect(dst) as b:
   a.backup(b,pages=128,sleep=.05)
   if b.execute('pragma quick_check').fetchone()[0]!='ok':raise RuntimeError('integrity_failed')
  os.chmod(dst,0o600)
```

A leitura imutável só vale com o container parado e o WAL vazio. Banco com WAL pendente segue pela leitura normal, porque a imutável ignoraria o WAL.

**Prova:** o serviço termina em menos de um minuto, com `sqlite_integrity: ok` e `uploaded_verified`.
