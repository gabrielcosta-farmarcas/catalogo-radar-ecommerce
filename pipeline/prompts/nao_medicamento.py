"""Pack de formatação de NÃO-MEDICAMENTO — recorte literal das regras atuais.

Sem regras de sal, composição de bula ou forma farmacêutica da CMED.
"""

from pipeline.prompts.shared import FORMAT_CATEGORIZACAO, FORMAT_DESCRICAO, FORMAT_INTRO

FORMAT_TITULO_NAO_MEDICAMENTO = """
REGRAS DE TÍTULO (sem hífens, até ~70 caracteres - é o que o cliente digita/lê na busca):
- Não medicamento: [O que o produto é / Categoria] [Marca] [Linha] [Atributo/Especificação] \
[Volume/Quantidade]. "Categoria" é o tipo do objeto em português (Pomada, Fio Dental, \
Curativos, Absorvente, Shampoo, Enxaguante Bucal, Fórmula Infantil, Hastes Flexíveis, \
Fralda, Seringa), NÃO a finalidade terapêutica e NÃO o departamento da árvore. Ordem \
obrigatória: o que o produto É vem PRIMEIRO; marca vem DEPOIS. Pule o slot se não existir \
(sem linha = não invente linha). NUNCA comece pela marca - esse é o erro mais comum neste \
campo para não-medicamento. Errado: "Hipoglós Pomada Creme Assaduras 40g" / certo: "Pomada \
Creme Assaduras Hipoglós 40g". Errado: "Johnson's Baby Shampoo Regular 400ml" / certo: \
"Shampoo Johnson's Baby Regular 400ml". Errado: "Johnson's Reach Essencial Fio Dental \
Menta 100 Metros" / certo: "Fio Dental Johnson's Reach Essencial Menta 100 Metros". \
Errado: "Band Aid Curativos Transparente Respirável 40 Unidades" / certo: "Curativos Band \
Aid Transparente Respirável 40 Unidades". Errado: "Sempre Livre Absorvente Noturno com \
Abas Suave Leve 32 Unidades" / certo: "Absorvente Sempre Livre Noturno com Abas Suave \
Leve 32 Unidades". Errado: "Cotonete Johnson & Johnson Hastes Flexíveis 75 Unidades" / \
certo: "Hastes Flexíveis Cotonete Johnson & Johnson 75 Unidades". Errado: "Periogard \
Enxaguante Bucal Extra Mint Sem Álcool 250ml" / certo: "Enxaguante Bucal Periogard Extra \
Mint Sem Álcool 250ml". Errado: "Aptamil 2 Fórmula Infantil 400g" / certo: "Fórmula \
Infantil Aptamil 2 400g".
Exemplos: "Fralda Pampers Confort Sec XXG 56 Unidades".
Não aplique regra de título de medicamento (marca primeiro, composição, sal de fármaco).
"""

FORMAT_CAMPOS_SYSTEM = (
    FORMAT_INTRO
    + FORMAT_TITULO_NAO_MEDICAMENTO
    + FORMAT_DESCRICAO
    + FORMAT_CATEGORIZACAO
)
