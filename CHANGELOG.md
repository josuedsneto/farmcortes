# Changelog

## v0.2.0 — Modo Bíblia (2026-11-23)

O projeto Biblia passa a ser um modo do Farmcortes: um app, um canal, uma VPS.
Configuração e operação: `docs/biblia.md`.

### Novo
- **Modo Bíblia** (`python -m farmcortes.biblia`): gera 1 Short por execução com a
  leitura sequencial da Bíblia (Almeida Corrigida Fiel, 31.106 versículos, incluindo
  João e Isaías 57–66, que faltavam no CSV do Biblia). Versículos longos viram partes
  de até 60 s ("Parte X/N").
- Narração **edge-tts** com a voz do Biblia (`pt-BR-AntonioNeural`), com o Piper como reserva
  automática. Legendas quebradas na pontuação e sincronizadas pelos tempos de cada palavra.
- Fundo Pexels (Pixabay de reserva) que sempre cobre a tela, com faixas escuras da foto cortadas
  e o crédito do fotógrafo na descrição. Música Jamendo opcional.
- **CTA do Bibliagram** no fim de cada vídeo: rodízio pelo uso mais antigo, duplicatas
  ignoradas, lista de exclusão, duração descontada para o vídeo inteiro caber em 60 s, e
  "Conheça o Bibliagram" (+ link) na descrição.
- Títulos e descrições por IA (Groq, Gemini de reserva) com cache, e títulos-modelo sem chave.
  Geração de roteiro teológico (`insight`).
- Facebook Reels, Instagram Reels e TikTok (rascunho por padrão) para itens aprovados da Bíblia.
- Timer `farmcortes-biblia.timer` (08:00, 14:00 e 20:00, `Nice=10`).

### Mudanças
- `review_queue` aceita `kind='biblia'`, com migração automática que preserva os dados.
  Ganha as colunas `approved_by`, `scheduled_for` e `social_json`.
- A aprovação automática da Bíblia (`FARMCORTES_BIBLIA_AUTO_APPROVE`) vem desligada por padrão;
  nada é publicado sem aprovação.
- O limite diário padrão (`FARMCORTES_DAILY_UPLOAD_LIMIT`) passou de 5 para 8.
- `social/tiktok.py` passou a usar os endpoints reais da Content Posting API.

### Atenção
- O edge-tts usa um endpoint não oficial da Microsoft, que pode mudar sem aviso; o Piper
  cobre as falhas.
- A API de texto bíblico exige chave (`FARMCORTES_BIBLIA_API_KEY`).
- O repositório Biblia tem `credentials/youtube_creds.json` versionado. Esse segredo não foi
  copiado para cá; recomenda-se trocar o client OAuth e limpar o histórico daquele repositório.
