# Integrações Sociais do Farmcortes

Este documento explica como configurar e usar as integrações com TikTok (oficial), Instagram Reels e Kwai.

---

## 1. TikTok — Content Posting API v2 (Oficial)

### 1.1 Criar o App no TikTok Developers

1. Acesse https://developers.tiktok.com/
2. Faça login com a conta que vai gerenciar o perfil do Coletivo Cortes
3. Clique em **"Create App"** → **"Content Posting API"**
4. Preencha:
   - **App name**: `Farmcortes Coletivo Cortes`
   - **Description**: `Publicação automática de cortes para o TikTok do Coletivo Cortes`
   - **Website**: `https://farmcortes.169-58-183-205.sslip.io`
5. Submit → aguarde aprovação (geralmente imediata para sandbox)

### 1.2 Configurar Produto "Content Posting API"

No painel do app:
1. Vá em **Products** → **Content Posting API** → **Add Product**
2. Em **Redirect URI**, adicione:
   ```
   https://farmcortes.169-58-183-205.sslip.io/oauth/tiktok/callback
   ```
3. Em **Scopes** (permissões), marque obrigatoriamente:
   - ✅ `video.upload`
   - ✅ `video.publish`
   - ✅ `user.info.basic`
4. Salve.

### 1.3 Credenciais (Client Key / Secret)

1. No painel do app, copie **Client Key** e **Client Secret**
2. No servidor onde roda o Farmcortes, defina as variáveis de ambiente:
   ```bash
   export FARMCORTES_TIKTOK_CLIENT_KEY="sua_client_key_aqui"
   export FARMCORTES_TIKTOK_CLIENT_SECRET="seu_client_secret_aqui"
   ```
   > **Dica**: Adicione ao `.bashrc` ou ao systemd service do Farmcortes para persistir.

### 1.4 Fluxo OAuth (Primeira vez)

1. Rode o script/helper que gere a URL de autorização (o Farmcortes expõe endpoint `/oauth/tiktok/start`):
   ```bash
   python -c "
   from farmcortes.social.tiktok import authorize_url
   print(authorize_url('https://farmcortes.169-58-183-205.sslip.io/oauth/tiktok/callback'))
   "
   ```
2. Abra a URL no navegador, logue no TikTok do Coletivo Cortes e autorize.
3. O TikTok redireciona para o callback → o Farmcortes troca o `code` por tokens e salva em:
   ```
   farmcortes/data/secrets/tiktok-token.json  (permissão 0600)
   ```
4. Pronto! O token inclui `refresh_token` válido por ~1 ano.

### 1.5 Sandbox vs Auditoria

| Fase | O que funciona | Limitações |
|------|----------------|------------|
| **Sandbox** (app recém-criado) | Upload + **apenas rascunho (draft)** | Vídeo cai na caixa de entrada do app TikTok; usuário abre o app e publica manualmente. `privacy_level` fixo em `SELF_ONLY`. |
| **Após auditoria** (app aprovado) | Upload + **publicação direta** | Pode publicar direto com `privacy_level`: `SELF_ONLY`, `FRIENDS`, `PUBLIC`. Requer submissão para review no painel TikTok. |

> **Importante**: O Farmcortes usa **modo rascunho (draft) por padrão**. Publicação direta só se `direct_post=True` E o app já passou na auditoria.

### 1.6 Renovação de Token

- Access token expira em ~24h.
- Refresh token expira em ~1 ano (ou quando usuário revoga).
- O Farmcortes renova automaticamente antes de cada upload.
- Se der erro de `refresh_token` inválido: repita o fluxo OAuth (1.4).

---

## 2. Instagram Reels — Pacote de Exportação

**Não há API oficial de publicação** (Instagram Graph API exige business verification complexo).  
O Farmcortes gera um **pacote .zip pronto para upload manual**.

### 2.1 O que o pacote contém

```
export_YYYYMMDD_HHMMSS_titulo.zip
├── video.mp4      (1080x1920, reutiliza o corte já renderizado)
├── cover.jpg      (frame do meio do vídeo, extraído via ffmpeg)
└── caption.txt    (título, descrição, CTA, hashtags, avisos de duração)
```

### 2.2 Como usar

1. O Farmcortes gera o zip em `data/exports/instagram/`
2. Baixe o arquivo (via interface web, scp, ou compartilhamento)
3. No celular: abra o Instagram → Reels → selecione o `video.mp4`
4. Copie o texto do `caption.txt` para a legenda
5. Use o `cover.jpg` como capa (opcional)
6. Publique

### 2.3 Limites do Reels

- **Duração recomendada**: até **90 segundos**
- Se o corte passar de 90s, o `caption.txt` traz um aviso ⚠️
- Resolução ideal: **1080x1920** (vertical 9:16)

---

## 3. Kwai — Pacote de Exportação

Mesmo esquema do Instagram: pacote .zip para upload manual.

### 3.1 O que o pacote contém

Mesma estrutura: `video.mp4`, `cover.jpg`, `caption.txt`  
Salvo em `data/exports/kwai/`

### 3.2 Limites do Kwai

- **Duração recomendada**: até **60 segundos**
- Aviso no `caption.txt` se exceder
- Resolução ideal: **1080x1920**

---

## 4. Uso Programático (para o Claude integrar depois)

```python
# TikTok - upload como rascunho (padrão)
from farmcortes.social.tiktok import upload_draft
result = upload_draft("cortes/job-123/clip_01.mp4", "Título do corte #cortes #gaming")

# TikTok - publicação direta (só se app auditado)
result = upload_draft("cortes/job-123/clip_01.mp4", "Título", direct_post=True, privacy_level="PUBLIC")

# Instagram Reels - gerar pacote
from farmcortes.social.export import build_instagram_pack
zip_path = build_instagram_pack("cortes/job-123/clip_01.mp4", {
    "title": "Título do corte",
    "description": "Descrição curta",
    "cta": "Siga para mais!",
    "hashtags": ["cortes", "gaming", "brasil"],
})

# Kwai - gerar pacote
from farmcortes.social.export import build_kwai_pack
zip_path = build_kwai_pack("cortes/job-123/clip_01.mp4", {...})
```

---

## 5. Checklist para o Lucas

### TikTok (uma vez só)
- [ ] Criar app em https://developers.tiktok.com/ (produto Content Posting API)
- [ ] Configurar Redirect URI: `https://farmcortes.169-58-183-205.sslip.io/oauth/tiktok/callback`
- [ ] Marcar scopes: `video.upload`, `video.publish`, `user.info.basic`
- [ ] Copiar Client Key / Secret → variáveis de ambiente no servidor
- [ ] Rodar autorização OAuth (gerar URL, abrir no navegador, autorizar)
- [ ] Verificar se `data/secrets/tiktok-token.json` foi criado (0600)
- [ ] (Opcional) Submeter app para auditoria se quiser publicação direta

### Instagram / Kwai (manual, sempre)
- [ ] Baixar zip gerado em `data/exports/instagram/` ou `data/exports/kwai/`
- [ ] Subir video.mp4 no app correspondente
- [ ] Copiar legenda do caption.txt
- [ ] Usar cover.jpg como capa
- [ ] Publicar

---

## 6. Troubleshooting

| Erro | Causa | Solução |
|------|-------|---------|
| `TikTokTokenError: Token não encontrado` | OAuth não feito | Repita seção 1.4 |
| `TikTokTokenError: Token expirado e sem refresh_token` | Refresh token inválido/expirado | Repita seção 1.4 |
| `TikTokRateLimitError` | Muitas requisições | Aguarde alguns minutos; o Farmcortes tem backoff |
| `TikTokError: privacy_level inválido` | App em sandbox + direct_post | Use `direct_post=False` (padrão) ou aguarde auditoria |
| `caption.txt` avisa duração > 90s/60s | Corte muito longo | Re-renderize com duração menor ou publique mesmo assim (corte pode ser truncado) |
| Vídeo não é 1080x1920 | Corte renderizado errado | Ajuste pipeline de renderização; o export avisa mas não impede |

---

## 7. Arquivos Envolvidos

```
farmcortes/
├── social/
│   ├── __init__.py
│   ├── tiktok.py        # OAuth + upload (Content Posting API v2)
│   └── export.py        # Pacotes Instagram/Kwai
├── data/
│   ├── secrets/
│   │   └── tiktok-token.json  (0600, auto-gerado)
│   └── exports/
│       ├── instagram/
│       └── kwai/
├── tests/
│   └── test_social.py   # Mocks HTTP, sem rede
└── docs/
    └── social.md        # Este arquivo
```
