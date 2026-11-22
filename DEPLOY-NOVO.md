# Farmcortes — novo deploy

Pacote gerado em 21/11/2026 a partir do commit 9038b07 (servidor atual: farmcortes.169-58-183-205.sslip.io).

## O que vem no pacote
- Código completo (`farmcortes/`), testes, `requirements.txt`, políticas (`editorial-policy.json`, `caption-glossary.json`, `longform-projects.json`).
- `deploy/`: modelos de systemd e nginx. `deploy/producao-atual/`: cópia exata dos arquivos que rodam hoje
  (systemd com os drop-ins `pause-publish.conf`, que hoje DESLIGAM a publicação no YouTube, e o nginx).
- `dados-iniciais/`: banco SQLite atual (fila, revisão, descobertas; sem senhas) e `youtube-settings.json`.
- `.env.example`: todas as variáveis que o código lê.
- `dados-iniciais/biblia/`: `versiculos.csv` (fila do modo Bíblia, importada uma vez para o SQLite) e `temas.csv`.

## O que NÃO vem (por segurança ou tamanho)
- Credenciais do projeto Biblia (`credentials/`, tokens, `.env`): não foram copiadas; o modo Bíblia publica pelo token
  do Farmcortes. Chaves Pexels/Pixabay/Jamendo/Groq/Gemini/Meta vão só em `/etc/farmcortes.env`.
- Credenciais: `data/secrets/` (token OAuth do YouTube, token do TikTok, token do worker local) e a senha do nginx
  (`/etc/nginx/.htpasswd-farmcortes`). No servidor novo: autorize o canal de novo pelo fluxo OAuth do app e crie
  outra senha com `htpasswd -c /etc/nginx/.htpasswd-farmcortes <usuario>`.
- Vídeos e trabalhos (`data/jobs`, `compilations`, `originals`… ~11 GB) e o `.venv`.
- Voz Piper `pt_BR-edresson-low.onnx` (+ `.json`): baixe em https://huggingface.co/rhasspy/piper-voices
  (pt/pt_BR/edresson/low) para `data/models/`. Os modelos Whisper baixam sozinhos no primeiro uso.

## Passo a passo (Ubuntu 24.04)
1. `sudo apt install -y python3-venv python3-dev ffmpeg nginx certbot python3-certbot-nginx git`
2. Copie a pasta para `/opt/farmcortes` (ou outro caminho; ajuste os caminhos nos arquivos de systemd).
3. `cd /opt/farmcortes && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`
4. `mkdir -p data/secrets data/models && cp dados-iniciais/* data/` (opcional: começar com o banco atual).
5. Baixe a voz Piper para `data/models/` (acima).
6. Teste: `.venv/bin/pytest -q` e `.venv/bin/uvicorn farmcortes.app:app --host 127.0.0.1 --port 8012`.
7. systemd: copie `deploy/farmcortes*.service|timer` (ou os de `deploy/producao-atual/systemd`) para
   `/etc/systemd/system/`, ajuste `User`, `WorkingDirectory` e caminhos, e:
   `sudo systemctl daemon-reload && sudo systemctl enable --now farmcortes farmcortes-discovery.timer farmcortes-compilations.timer`
   Os drop-ins `pause-publish.conf` deixam a publicação desligada; remova-os quando quiser publicar.
   Modo Bíblia (opcional): copie também `deploy/farmcortes-biblia.service` e `.timer`, ponha as chaves em
   `/etc/farmcortes.env` (chmod 600; lido via `EnvironmentFile=`; seção "Modo Bíblia" do `.env.example`) e rode
   `sudo systemctl enable --now farmcortes-biblia.timer`. Teste uma vez com
   `sudo systemctl start farmcortes-biblia.service && journalctl -u farmcortes-biblia -n 50`.
   O item fica pendente na fila até ser aprovado, salvo com `FARMCORTES_BIBLIA_AUTO_APPROVE=true`.
   CTA do Bibliagram (não vai no git): copie os MP4 de `CTAvideos/` para `data/cta/` no servidor, por exemplo
   `scp CTAvideos/*.mp4 <usuario>@<vps>:/home/admin/.openclaw/workspace/farmcortes/data/cta/` (ou outra pasta em
   `FARMCORTES_BIBLIA_CTA_DIR`, legível pelo usuário do serviço). Duplicatas idênticas são ignoradas e os 7 clipes
   entram no rodízio. `FARMCORTES_BIBLIA_CTA_EXCLUDE` fica vazio; só preencha (padrões de nome separados por
   vírgula) ou apague o arquivo se um dia quiser aposentar um clipe. Sem a pasta, os vídeos saem sem CTA.
   Usa a mesma voz Piper e o mesmo token do YouTube; não há segundo OAuth nem Node/Remotion.
8. nginx: use `deploy/producao-atual/nginx-farmcortes.conf` trocando o `server_name`, crie o `.htpasswd`
   e rode `sudo certbot --nginx -d <seu-dominio>`. `client_max_body_size 4G` e timeouts longos são necessários para upload.
9. YouTube: crie o OAuth no Google Cloud (YouTube Data API v3), autorize pelo app e só então
   `FARMCORTES_YOUTUBE_ENABLED=true`.

## Observações
- Na VPS, o YouTube costuma bloquear download anônimo (verificação anti-robô). O caminho legítimo é enviar o
  arquivo ou usar link do Google Drive/Dropbox. Não usar cookies, proxy ou PO Token para contornar.
- Processamento pesado (Whisper/renderização) consome bastante CPU e memória.
