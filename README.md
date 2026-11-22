# Farmcortes

Plataforma privada para transformar vídeos autorizados do YouTube, Google Drive ou Dropbox em cortes
verticais. O processamento acontece no próprio servidor e não chama Codex nem
serviços pagos.

## Fluxo

1. O usuário confirma que possui autorização sobre o conteúdo e cola a URL do YouTube ou um link compartilhável do Google Drive/Dropbox.
2. `yt-dlp` importa vídeo e legendas públicas, sem contornar autenticação. Links de armazenamento precisam estar compartilhados com quem possui o link.
3. Na ausência de legenda, `faster-whisper` transcreve localmente.
4. Um seletor determinístico encontra trechos completos e não sobrepostos.
5. FFmpeg renderiza 1080x1920, normaliza o áudio e grava as legendas por último.
6. Todos os vídeos recebem uma chamada visual clara para inscrição; cortes com 24 segundos ou mais repetem o CTA perto do encerramento.
7. O job termina como "Cortes prontos para revisão"; nada é publicado sem aprovação (veja Publicação no YouTube).

A interface permite cortes de até 3 minutos, limite atual dos Shorts verticais,
e duração mínima de até 1 minuto e 30 segundos para realmente forçar cortes
mais longos. O padrão inteligente é 45–65 segundos; durações maiores são
escolhidas por trabalho quando o assunto sustenta um trecho mais longo.

## Estratégia de monetização

O seletor pontua gancho, clareza, progressão narrativa, conclusão e adequação da
duração. Candidatos com contexto ambíguo ou final incompleto são rejeitados.
Cada corte inclui três opções de título e gancho, sem forçar `#Shorts` no título,
e recebe um alerta quando ultrapassa 60 segundos para lembrar a verificação de
Content ID.

`POST /api/strategy/metrics` registra uma fotografia do YouTube Studio e
`GET /api/strategy/report` calcula conversão de inscritos, duração média derivada
e inconsistências de escopo. Cada trabalho concluído oferece ainda
`GET /api/jobs/{id}/longform-plan`, um plano de 6–12 minutos que mostra quanto
material e quanta narração original ainda faltam.

## Compilações longas editoriais

`python -m farmcortes.source_compilations` trabalha diretamente com
as fontes longas originais, não com os Shorts já renderizados. A rotina exige ao
menos três vídeos CC BY do mesmo tema e 30 minutos úteis, mira aproximadamente
uma hora e usa até 20 minutos de cada fonte. Um modelo local escreve abertura e
contextos com base no próprio material, o Piper gera a narração em português e
o FFmpeg normaliza os capítulos, mostra a atribuição e inclui chamadas de
inscrição. Apenas uma nova compilação é gerada por execução; a rotina só renderiza, não publica.

Esse tratamento reduz o risco de conteúdo repetitivo, mas não garante entrada
ou permanência no Programa de Parcerias do YouTube; direitos autorais,
autenticidade e valor editorial continuam sendo avaliados pelo YouTube.

O motor de corte é local e não consome créditos. A base open source AutoClip
(MIT) fornece os componentes de reenquadramento e diagnóstico instalados no
mesmo ambiente. Quando o YouTube bloqueia a VPS com verificação anti-robô, o
usuário pode usar o link compartilhável do original no Google Drive ou Dropbox;
o sistema não usa cookies pessoais, proxies ou serviços pagos para contornar a restrição.

## Short original: pesquisa até o vídeo pronto

O modo original evita baixar vídeos de terceiros. Ele pesquisa um fato histórico
do dia na Wikimedia, escolhe uma pauta com imagem aberta, monta o roteiro,
produz narração pt-BR local com Piper e renderiza em 1080x1920 com créditos.
O comando só renderiza; não publica.

```bash
.venv/bin/python -m farmcortes.original --date 2026-11-06
```

O modelo de voz deve existir em
`data/models/pt_BR-edresson-low.onnx` (com o JSON de configuração ao lado).

## Publicação no YouTube

A publicação ocorre só depois de aprovação na fila de revisão (`review_queue`).
Cada item recebe uma prévia no Telegram com três opções de título; o botão
escolhido aprova o item (`python -m farmcortes.telegram --poll`) e
`python -m farmcortes.review` (`review.publish_approved`) envia os aprovados pela
API oficial. Cortes de jobs, Shorts originais e compilações não são publicados
automaticamente.

Configure `FARMCORTES_YOUTUBE_ENABLED=true`, `FARMCORTES_YOUTUBE_TOKEN` com o
token OAuth renovável, `FARMCORTES_YOUTUBE_PRIVACY` (`private`, `unlisted` ou
`public`) e `FARMCORTES_DAILY_UPLOAD_LIMIT`. Só são publicados itens de
streamers aprovados em `data/streamers.json`.

## Varredura diária de fontes

`python -m farmcortes.discovery --count 3` usa a API oficial do YouTube para
pesquisar vídeos longos recentes em português. Todos os resultados ficam
registrados como sinais editoriais; apenas fontes Creative Commons entram na
fila automaticamente.
Os candidatos são priorizados por velocidade de visualizações e engajamento e
salvos como sinais editoriais. Somente fontes em português, com tração mínima e
licença CC BY entram na fila de cortes; vídeos sob licença padrão jamais são
baixados automaticamente. Autor, URL e licença são preservados na descrição.
O arquivo `editorial-policy.json` define temas, capacidade da fila e campanhas.
De 19 a 25/11/2026, dois terços das buscas serão dedicados ao lançamento de GTA
VI. O timer `farmcortes-discovery.timer` executa a busca diariamente às 03:10
no horário de Brasília e deixa de criar jobs quando há oito trabalhos pendentes.
O painel oferece também **Rodar varredura agora**: a ação dispara exatamente a
mesma rotina em segundo plano, mostra o andamento e impede uma segunda execução
enquanto a busca manual ou agendada estiver ativa.

## Desenvolvimento

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn farmcortes.app:app --host 127.0.0.1 --port 8012
```

Dados e resultados ficam em `data/`, que não deve ser versionado.

## Componentes open source

- [AutoClip](https://github.com/Gutsken/Autoclip), commit
  `5d0eac36fa615b79dd2104083bf273a96f8d68bb`, licença MIT.
- FFmpeg, yt-dlp e faster-whisper, instalados no ambiente local.
