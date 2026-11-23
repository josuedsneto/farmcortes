# Modo Bíblia — configuração e operação

Gera um Short vertical por execução com a leitura sequencial da Bíblia (Almeida
Corrigida Fiel), narrado, legendado, com o CTA do app Bibliagram no fim, e o
coloca na mesma fila de revisão e no mesmo canal do YouTube dos cortes do
Farmcortes. Nada é publicado sem aprovação.

```
versiculos.csv ──(importa 1x)──> SQLite biblia_verses
        │
        ▼  (timer 08:00, 14:00, 20:00)
texto ACF → narração edge-tts (Piper de reserva) → fundo Pexels → legendas
        → render FFmpeg 1080x1920 + CTA Bibliagram (≤ 60 s)
        → review_queue (kind='biblia') ── aprovação (Telegram / API / automática)
        → YouTube (mesmo token e cota) + Facebook/Instagram/TikTok opcionais
```

## 1. Pré-requisitos no servidor

```bash
sudo apt install -y python3-venv ffmpeg fonts-dejavu-core
```

- Python 3.12, FFmpeg com `libass` (legendas) e `libx264`.
- Saída para a internet: API do texto bíblico, Pexels, edge-tts (Microsoft), YouTube.
- O Farmcortes já instalado (veja `DEPLOY-NOVO.md`); o modo Bíblia usa o mesmo
  `.venv`, a mesma pasta `data/` e o mesmo token do YouTube.

## 2. Código

```bash
cd /home/admin/.openclaw/workspace/farmcortes
git fetch origin && git checkout v0.2.0      # ou: git checkout unified-biblia
.venv/bin/pip install -r requirements.txt     # inclui edge-tts==7.3.1
.venv/bin/pytest -q tests/test_biblia.py tests/test_review.py
```

O banco existente é migrado sozinho na primeira abertura (a tabela
`review_queue` passa a aceitar `kind='biblia'`; nenhuma linha é perdida).

## 3. Variáveis de ambiente

Ficam em `/etc/farmcortes.env` (`chmod 600`, dono `root` ou `admin`). O serviço
lê esse arquivo com `EnvironmentFile=`. Escreva um `NOME=valor` por linha e
deixe comentários em linhas próprias (o systemd não remove `# ...` do fim da linha).

| Variável | Obrigatória? | Para quê |
|---|---|---|
| `FARMCORTES_BIBLIA_API_KEY` | **sim, na prática** | Texto bíblico (API Pesquisar na Bíblia; sem chave ela responde 406). Sem texto, o versículo é pulado. |
| `FARMCORTES_PEXELS_API_KEY` | recomendada | Foto de fundo vertical (grátis em pexels.com/api). Reserva: `FARMCORTES_PIXABAY_API_KEY`. Sem nenhuma, usa um gradiente liso. |
| `FARMCORTES_GROQ_API_KEY` | recomendada | Títulos e descrição por IA (grátis em console.groq.com). Reserva: `FARMCORTES_GEMINI_API_KEY`. Sem chave, usa títulos-modelo. |
| `FARMCORTES_BIBLIA_CTA_DIR` | não | Pasta dos MP4 do Bibliagram (padrão `data/cta`). |
| `FARMCORTES_BIBLIA_CTA_EXCLUDE` | não | Padrões de nome a ignorar. Padrão do exemplo: `*testadores*,*como-funciona-o-teste*` (CTAs do teste fechado, aposentados). |
| `FARMCORTES_BIBLIA_APP_URL` | não | Link do app na descrição ("📱 Conheça o Bibliagram: <link>"). |
| `FARMCORTES_BIBLIA_TTS` | não | `edge` (padrão) ou `piper`. |
| `FARMCORTES_BIBLIA_VOICE` | não | Voz do edge-tts (padrão `pt-BR-AntonioNeural`, a do Biblia). |
| `FARMCORTES_JAMENDO_CLIENT_ID` | não | Música instrumental de fundo (15% do volume), com crédito na descrição. |
| `FARMCORTES_BIBLIA_AUTO_APPROVE` | não | `true` aprova os vídeos da Bíblia sozinho (1º título). Padrão `false`. |
| `FARMCORTES_YOUTUBE_ENABLED` | para publicar | `true` liga o envio ao YouTube (mesmo token dos cortes). |
| `FARMCORTES_YOUTUBE_PRIVACY` | não | `private` (padrão), `unlisted` ou `public`. Em `public`, o vídeo é agendado no próximo horário de `FARMCORTES_YOUTUBE_SCHEDULE_TIMES`. |
| `FARMCORTES_DAILY_UPLOAD_LIMIT` | não | Uploads por dia somando cortes e Bíblia (padrão 8). |
| `FARMCORTES_META_ACCESS_TOKEN`, `FARMCORTES_FACEBOOK_PAGE_ID`, `FARMCORTES_INSTAGRAM_USER_ID` | não | Reels no Facebook/Instagram dos itens aprovados. |
| `FARMCORTES_TIKTOK_CLIENT_KEY`, `FARMCORTES_TIKTOK_CLIENT_SECRET`, `FARMCORTES_TIKTOK_MODE` | não | TikTok dos itens aprovados (`draft` padrão; `private`; `public` só após auditoria do app). |

Exemplo mínimo:

```bash
sudo tee /etc/farmcortes.env >/dev/null <<'EOF'
FARMCORTES_BIBLIA_API_KEY=coloque_aqui
FARMCORTES_PEXELS_API_KEY=coloque_aqui
FARMCORTES_GROQ_API_KEY=coloque_aqui
FARMCORTES_BIBLIA_CTA_EXCLUDE=*testadores*,*como-funciona-o-teste*
FARMCORTES_BIBLIA_APP_URL=
FARMCORTES_YOUTUBE_ENABLED=true
FARMCORTES_YOUTUBE_PRIVACY=private
FARMCORTES_DAILY_UPLOAD_LIMIT=8
EOF
sudo chmod 600 /etc/farmcortes.env
```

## 4. Arquivos que não vão no git

**CTAs do Bibliagram (só os de propaganda):**

```bash
mkdir -p data/cta
scp CTAvideos/bibliagram-01-quanto-tempo-voce-rolou-hoje.mp4 \
    CTAvideos/bibliagram-03-biblia-no-bolso-sem-sinal.mp4 \
    CTAvideos/bibliagram-04-devocionais-comunidade-em-breve.mp4 \
    CTAvideos/bibliagram-05-anuncio-6s.mp4 \
    CTAvideos/bibliagram-06-biblia-inteira-em-2027.mp4 \
    admin@<vps>:/home/admin/.openclaw/workspace/farmcortes/data/cta/
```

Os CTAs do teste fechado (`bibliagram-00-…` e `bibliagram-00b-…`) não entram.
Se forem copiados por engano, o `FARMCORTES_BIBLIA_CTA_EXCLUDE` do exemplo os
ignora. O rodízio usa o clipe usado há mais tempo e registra cada uso em
`biblia_cta_uses`. Sem pasta, ou com a pasta vazia, o vídeo sai sem CTA.

**Voz Piper (reserva do edge-tts):** `data/models/pt_BR-edresson-low.onnx` e o `.onnx.json`
(huggingface.co/rhasspy/piper-voices, `pt/pt_BR/edresson/low`). Sem ela, uma
falha do edge-tts derruba a execução.

**Música (opcional):** defina `FARMCORTES_JAMENDO_CLIENT_ID`, ou coloque uma
faixa com licença conhecida em `data/biblia/music.mp3` e o crédito em
`data/biblia/music.json` (`{"credit": "Música: … (licença)"}`).

## 5. Primeira execução manual

```bash
cd /home/admin/.openclaw/workspace/farmcortes
set -a; . /etc/farmcortes.env; set +a
.venv/bin/python -m farmcortes.biblia --data-dir data import   # 31.106 versículos (1x)
.venv/bin/python -m farmcortes.biblia --data-dir data run      # gera 1 vídeo
```

A saída é um JSON com `status` (`pending` ou `approved`), `review_id`,
`reference`, `part`/`parts`, `cta` e o que foi publicado. O vídeo fica em
`data/biblia/<ordem>-p<parte>/short.mp4`. Baixe esse arquivo e assista antes
de ligar o timer.

## 6. Aprovação e publicação

- **Telegram:** cada vídeo pendente chega como prévia marcada com `[BÍBLIA]` e
  três botões de título. Precisa de `FARMCORTES_TELEGRAM_BOT_TOKEN` e
  `FARMCORTES_TELEGRAM_CHAT_ID` (veja `docs/pipeline-gta.md`).
- **API:** com o token do worker local (`data/secrets/local-worker-token`):
  ```bash
  TOKEN=$(cat data/secrets/local-worker-token)
  curl -s -H "Authorization: Bearer $TOKEN" "https://<dominio>/api/review?kind=biblia"
  curl -s -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
       -d '{"id": 12, "choice": 1}' https://<dominio>/api/review    # ou "choice": "discard"
  ```
- **Automática:** `FARMCORTES_BIBLIA_AUTO_APPROVE=true`.

Os itens aprovados são enviados no fim da próxima execução do timer. Para
enviar agora, sem gerar vídeo novo:

```bash
.venv/bin/python -c "from farmcortes import review; print(review.publish_approved(kinds=('biblia',))); print(review.publish_social())"
```

O envio respeita `FARMCORTES_YOUTUBE_ENABLED`, a privacidade e o limite diário.
A cada execução do timer, os vídeos já publicados em todas as plataformas ligadas são apagados do disco.

## 7. Timer (3 vídeos por dia)

```bash
sudo cp deploy/farmcortes-biblia.service deploy/farmcortes-biblia.timer /etc/systemd/system/
# ajuste User, WorkingDirectory e caminhos se a instalação não for /home/admin/.openclaw/workspace/farmcortes
sudo systemctl daemon-reload
sudo systemctl enable --now farmcortes-biblia.timer
systemctl list-timers farmcortes-biblia.timer
```

- Para rodar uma vez agora: `sudo systemctl start farmcortes-biblia.service`.
- Logs: `journalctl -u farmcortes-biblia -n 100 --no-pager`.
- Horários: 08:00, 14:00 e 20:00 (America/Sao_Paulo), com `Nice=10` e
  `CPUWeight=20` para não atrasar os cortes. Para mudar, edite `OnCalendar=` no `.timer`.
- Para pausar: `sudo systemctl disable --now farmcortes-biblia.timer`.

## 8. Outros comandos

```bash
# roteiro + título/descrição de um estudo teológico de temas.csv (só texto, com cache)
.venv/bin/python -m farmcortes.biblia --data-dir data insight --ordem 1
# reimportar uma versão nova do CSV (só acrescenta; nada publicado volta para a fila)
.venv/bin/python -m farmcortes.biblia --data-dir data import --csv dados-iniciais/biblia/versiculos.csv
```

## 9. Problemas comuns

| Sintoma | Causa provável |
|---|---|
| `status: idle` | A fila acabou ou todos os versículos já foram publicados. |
| Versículo pulado no log ("não veio da API") | Chave da API ausente/errada ou numeração diferente; o próximo versículo segue. |
| Narração com voz robótica | O edge-tts falhou e o Piper assumiu; veja o log `edge-tts falhou`. |
| Vídeo sem CTA | `data/cta` vazio, ou todos os clipes excluídos ou longos demais para o versículo. |
| Nada publicado | Item ainda `pending`, `FARMCORTES_YOUTUBE_ENABLED` diferente de `true`, ou o limite diário foi atingido. |
| Fundo liso | Sem chave Pexels/Pixabay ou as APIs falharam. |
