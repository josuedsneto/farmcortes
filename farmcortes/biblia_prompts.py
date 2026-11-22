"""Prompt templates ported verbatim from Biblia (only the verse prompt now asks for 3 titles)."""

VERSE_PROMPT = """Você é um especialista em criação de conteúdo para YouTube voltado a versículos bíblicos.
Gere metadados para um vídeo de curta duração apresentando o seguinte trecho bíblico:

Livro: {book}
Capítulo: {chapter}
Versículos: {verse_range}
Tradução: {translation}

Texto do Versículo:
{verse_text}

════════════════════════════════════════
FORMATO DO TÍTULO (siga exatamente):
════════════════════════════════════════
TEMA_EM_CAIXA_ALTA: Referência Bíblica | Leia a Bíblia Toda em Um Ano

Regras:
- O TEMA deve ser em caixa alta, impactante, 3-6 palavras, relacionado ao conteúdo
- A Referência Bíblica deve estar no formato "Livro Cap:V a Cap:V" (ex: "Gênesis 7:20 a 8:4") ou "Livro Cap:V" para versículo único
- Sempre termina com " | Leia a Bíblia Toda em Um Ano"

Exemplos:
- "DEUS SE LEMBROU DE NOÉ: Gênesis 7:20 a 8:4 | Leia a Bíblia Toda em Um Ano"
- "A CRIAÇÃO DO MUNDO: Gênesis 1:1-5 | Leia a Bíblia Toda em Um Ano"
- "O SENHOR É MEU PASTOR: Salmos 23 | Leia a Bíblia Toda em Um Ano"

════════════════════════════════════════
FORMATO DA DESCRIÇÃO (siga exatamente):
════════════════════════════════════════
Estrutura obrigatória em português, nesta ordem:

1. Parágrafo de abertura (2-3 linhas): Contextualize o trecho na jornada de leitura anual. Apresente o tema central de forma envolvente e narrativa.

2. Linha: "O que você verá neste corte:"

3. Lista de versículos: Para cada versículo (ou grupo lógico), uma linha no formato:
   "Referência: Descrição do conteúdo do versículo e seu significado."

4. Parágrafo de ensinamento (3-5 linhas): Lição espiritual ou aplicação prática central do trecho.

5. Bloco de CTA:
✨ Apoie o nosso canal:

Inscreva-se para não perder nenhum dia da nossa leitura anual!

Deixe seu Like para que esta mensagem alcance mais pessoas.

Comente: "[frase temática curta relacionada ao conteúdo]"

6. Destaque do dia:
📖 Destaque do dia: "[versículo mais marcante do trecho entre aspas]" (Referência)

7. Hashtags: 10-12 tags relevantes, incluindo #LeiaABíblia #PlanoDeLeitura #BíbliaSagrada #PalavraDeDeus

════════════════════════════════════════

Gere TRÊS opções de título diferentes no formato acima (temas diferentes), cada uma com no máximo 100 caracteres.

Retorne APENAS um JSON válido com as chaves "titulos" (lista com 3 strings) e "descricao", sem código markdown ou explicações adicionais:

{{
  "titulos": ["...", "...", "..."],
  "descricao": "..."
}}
"""

THEOLOGICAL_SCRIPT_PROMPT = """Você é um especialista em teologia cristã e criação de conteúdo educacional profundo.
Gere um roteiro estruturado de ~1500 palavras para um vídeo de insight teológico de 8 a 10 minutos sobre o seguinte tema:

Tema: {tema}
Subtema: {subtema}
Palavras-chave: {palavras_chave}

O roteiro deve ser dividido em 4 seções com as seguintes características. É OBRIGATÓRIO atingir o número mínimo de palavras indicado em cada seção:

1. **Introdução** (~200 palavras mínimo):
   - Abertura envolvente do tema
   - Contextualizar por que este tema é importante
   - Gancho para manter atenção do espectador
   - Apresentação clara do que será abordado

2. **Desenvolvimento** (~700 palavras mínimo):
   - Exploração profunda e detalhada do tema teológico
   - Múltiplas referências bíblicas com explicação de cada uma
   - Análise histórica e diferentes perspectivas cristãs
   - Conexões com outras doutrinas e implicações teológicas
   - Exemplos bíblicos concretos e narrativas relevantes

3. **Aplicação Prática** (~400 palavras mínimo):
   - Como este conhecimento se aplica à vida contemporânea
   - Exemplos práticos e situações do cotidiano
   - Desafios, oportunidades e transformação pessoal
   - Orientações concretas para viver este princípio

4. **Conclusão** (~200 palavras mínimo):
   - Resumo dos pontos principais abordados
   - Chamada inspiradora à ação
   - Encorajamento final e mensagem de esperança
   - Convite à reflexão pessoal e oração

Retorne APENAS um JSON válido com a seguinte estrutura, sem código markdown ou explicações adicionais:

{{
  "titulo_video": "Título atrativo em 6-10 palavras",
  "secoes": [
    {{"nome": "Introdução", "texto": "..."}},
    {{"nome": "Desenvolvimento", "texto": "..."}},
    {{"nome": "Aplicação Prática", "texto": "..."}},
    {{"nome": "Conclusão", "texto": "..."}}
  ],
  "verse_highlights": [
    {{"versiculo": "texto curto do versículo (<100 chars)", "referencia": "João 3:16", "section_index": 1, "position_ratio": 0.3}}
  ]
}}

Para verse_highlights:
- Inclua 1 a 3 versículos bíblicos que são explicitamente citados no roteiro
- "section_index" é o índice da seção (0=Introdução, 1=Desenvolvimento, 2=Aplicação Prática, 3=Conclusão)
- "position_ratio" é um valor entre 0.0 e 1.0 indicando onde na seção o versículo aparece
- O texto do versículo deve ter menos de 100 caracteres

Certifique-se de que:
- Cada seção tem o comprimento de palavras especificado
- O texto é apropriado para ser narrado em voz alta
- A linguagem é clara, inspiradora e acessível
- Não há jargão desnecessário
"""

THEOLOGICAL_METADATA_PROMPT = """Você é um especialista em otimização de conteúdo para YouTube de longa duração (5-10 minutos).
Gere metadados profissionais para um vídeo de insight teológico com o seguinte conteúdo:

Tema: {tema}
Subtema: {subtema}

Seções do Roteiro:
{secoes_texto}

Requisitos para o título:
- 8-15 palavras
- Temático e envolvente
- Otimizado para SEO do YouTube
- Inclua o tema principal
- Exemplo: "Salvação: Entenda a Obra Completa de Cristo | Teologia Profunda"

Requisitos para a descrição:
- Introdução (2-3 linhas) apresentando o tema
- Resumo das seções principais (Introdução, Desenvolvimento, Aplicação Prática, Conclusão)
- Significância teológica e aplicação prática
- Pontos-chave (use bullets ou numeração)
- Timestamp aproximado para cada seção
- Chamadas à ação (inscrever, like, comentários, notificações)
- Tags sugeridas no final
- Rodapé com convite à comunidade

Retorne APENAS um JSON válido com as chaves "titulo" e "descricao", sem código markdown ou explicações adicionais:

{{
  "titulo": "...",
  "descricao": "..."
}}
"""
