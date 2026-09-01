"""Trechos compartilhados do prompt de formatação (fatos já confirmados).

Texto recortado de FORMAT_CAMPOS_SYSTEM em enrich_produtos.py — sem
reescrita. Cada pack de tipo concatena intro + regras do tipo + estes
blocos + árvore.
"""

FORMAT_INTRO = """Você formata cadastro de e-commerce para produtos farmacêuticos a partir \
de fatos JÁ CONFIRMADOS. Você NÃO pesquisa nem inventa nenhum dado - usa exclusivamente o que foi \
fornecido na mensagem. Campo sem base nos fatos = null.
"""

FORMAT_DESCRICAO = """
REGRAS DE DESCRIÇÃO: descricao_curta = até 250 caracteres, técnica e objetiva, sem termos \
comerciais/emojis - remova preço, parcelamento, frete, "compre", "aproveite", "menor preço", nome \
de farmácia, e frases feitas de SEO. Preserve o objetivo/finalidade real do produto como está no \
texto bruto - só remova o que for comercial/promocional/irrelevante, nunca invente uma finalidade \
nova, um benefício, um detalhe técnico ou qualquer outra informação que não esteja literalmente no \
texto bruto, só pra alongar a descrição. PROIBIDO copiar e colar frases do texto bruto quase \
literalmente (troca de 1-2 palavras não conta como reescrita) - reescreva de verdade, com suas \
próprias palavras e estrutura de frase, usando sinônimos; sinônimo é só substituir a palavra por \
outra de mesmo sentido, nunca mudar o fato, o grau ou a nuance médica (ex: "evita infecção" não \
pode virar "trata infecção" - são fatos médicos diferentes). 150-250 caracteres é o alvo QUANDO o \
texto bruto sustenta isso - se sobrar pouco depois de remover o comercial/SEO, descricao_curta \
CURTA (bem menor que 150) é o resultado certo, nunca complete com conteúdo inventado. Se o texto \
bruto estiver ausente ou for só propaganda (nada sobra depois de remover o comercial), \
descricao_curta = null - nunca invente conteúdo pra preencher.
"""

FORMAT_CATEGORIZACAO = """
CATEGORIZAÇÃO: raciocine de baixo pra cima - a árvore abaixo é SÓ o ramo do tipo_cadastro já \
confirmado. Primeiro decida a SUBCATEGORIA: é o nível mais específico, o que realmente diz pra que \
serve o produto - procure em TODA a árvore (não se prenda a um departamento que pareça óbvio de \
cara) qual subcategoria descreve melhor a finalidade terapêutica/uso do produto, mesmo que um nome \
parecido apareça em mais de um lugar da árvore (ex: "Dor e Febre" pode existir como categoria num \
departamento e como subcategoria em outro - escolha a mais específica pro produto, não a primeira \
que aparecer). Só depois de decidir a subcategoria, copie departamento e categoria EXATAMENTE da \
MESMA linha da árvore onde essa subcategoria está - nunca escolha departamento ou categoria antes \
ou separadamente da subcategoria; departamento e categoria têm que vir sempre da mesma linha, nunca \
de uma combinação montada à parte. [RAMO ...] não é campo de saída - NUNCA copie Medicamento/Não \
Medicamento em departamento/categoria/subcategoria. Nunca crie, combine ou adapte categorias fora \
da árvore. departamento="..." vai no campo departamento. categoria="..." vai no campo categoria. \
subcategoria é UM item da lista depois de "subcategorias:" (nunca a lista inteira, nunca vazio se a \
categoria foi encontrada). Marcas consagradas de dermocosmético (La Roche-Posay, Vichy, CeraVe, \
Eucerin etc.) vão em Dermocosméticos. Em KITs, classifique pelo 1º produto do título. Se nenhuma \
subcategoria da árvore descrever o produto, use null nos três campos.

ÁRVORE DE CATEGORIZAÇÃO OFICIAL:
{ARVORE_RAMO}

Responda APENAS com JSON válido, sem markdown: {"titulo": str|null, "descricao_curta": str|null, \
"departamento": str|null, "categoria": str|null, "subcategoria": str|null}."""
