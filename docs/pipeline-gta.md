# Pipeline GTA e aprovação

Nenhum serviço é instalado ou reiniciado por este projeto. A publicação continua sujeita a `FARMCORTES_YOUTUBE_ENABLED`; mantenha-a desativada até testar e revisar a configuração. As chamadas antigas a `publish_file` são bloqueadas: só o publicador da fila pode enviar. O limite diário conta apenas envios registrados por esta fila, no dia de São Paulo; envios externos e históricos não entram nessa conta. Um erro ou interrupção após o YouTube aceitar o upload e antes de salvar o ID exige reconciliação manual antes de tentar novamente.

Configure `data/streamers.json` como lista de objetos, com `approved: true` somente após liberação confirmada. Somente Twitch e Kick são aceitos. O publicador confere novamente a whitelist, inclusive se a liberação for revogada depois de aprovar o corte. Exemplo sem autorização real:

```json
[{"slug":"exemplo", "platform":"twitch", "approved":false, "rules":"informar restrições", "credit":"Crédito autorizado", "face_crop":{"x":0.7,"y":0,"w":0.3,"h":0.3}, "game_crop":{"x":0.2,"y":0,"w":0.6,"h":1}}]
```

Coordenadas são frações da imagem. Regras são registradas para consulta humana; não são interpretadas automaticamente. A captura utiliza streamlink, ffmpeg/ffprobe e faster-whisper small int8; o modelo pode precisar ser baixado na primeira execução. O chat e os segmentos têm retenção de 48 horas, removidos na inicialização ou periodicamente durante uma captura; arquivos aprováveis em `data/review` não são removidos automaticamente. Configure uma limpeza agendada independente se precisar garantir remoção enquanto não houver capturas.

```bash
.venv/bin/python -m farmcortes.livecut --streamer exemplo --platform twitch --test
.venv/bin/python -m farmcortes.livecut --manager --test
.venv/bin/python -m farmcortes.livecut --cleanup
.venv/bin/python -m farmcortes.telegram --poll
.venv/bin/python -m farmcortes.review
.venv/bin/pytest -q
```

O gerenciador inicia as entradas aprovadas uma vez; não monitora novas lives nem tenta novamente após desconexão. Cada processo ocupa um slot, limitado por `FARMCORTES_LIVE_LIMIT` (padrão 2). `FARMCORTES_CHAT_MIN_MESSAGES` ajusta o mínimo absoluto; `FARMCORTES_DAILY_UPLOAD_LIMIT` padrão 5. O publicador é uma execução única: agende-o externamente se desejar processar aprovados automaticamente. Testes nunca são enviados ao YouTube, mesmo aprovados.

O bot usa `FARMCORTES_TELEGRAM_BOT_TOKEN` e `FARMCORTES_TELEGRAM_CHAT_ID` no ambiente ou em `data/secrets/telegram.env` (permissão 600, uma atribuição por linha). Use chat privado do dono: callbacks exigem que o ID do remetente e do chat sejam o ID configurado. Se não configurado ou se o envio falhar, a fila continua disponível em GET `/api/review` e POST `/api/review` com JSON `{"id":1,"choice":1}` (ou `"discard"`) e o bearer token do worker local. Proteja o acesso à API e ao token. Aprovar não executa o publicador automaticamente.

Etapa 3: `titles.generate(context)` retorna três títulos provisórios; substituir por Nemotron e validar saída. Etapa 4: a varredura e descoberta de lives autorizadas não está implementada e ainda não tem módulo; usar a whitelist/CLI manual. Etapa 5: cortes longos não estão implementados e ainda não têm módulo; compilações aprováveis também não estão implementadas; `review.enqueue` já aceita seus tipos. Não há geração automática de longos a partir deste fluxo.

A análise de picos é heurística, e o volume mede RMS de toda a faixa, incluindo jogo/música, não voz isolada. A sincronia entre relógio do chat e início efetivo da gravação pode variar. Saídas e crops precisam de revisão humana. Duas lives em 720p com transcrição e duas renderizações 1080x1920 podem esgotar CPU, RAM e disco; comece com uma live e monitore. A compressão de prévia para 720p não garante arquivo abaixo do limite de envio do Telegram; falhas deixam o item na fila local.
