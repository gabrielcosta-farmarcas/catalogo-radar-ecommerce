"""Pack de formatação de MEDICAMENTO — recorte literal das regras atuais.

Nenhuma regra nova. Few-shots de não-medicamento (Hipoglós, fralda, etc.)
não entram aqui de propósito: pertencem ao pack NMED.
"""

from pipeline.prompts.shared import (
    FORMAT_CATEGORIZACAO,
    FORMAT_DESCRICAO,
    FORMAT_INTRO,
    FORMAT_SAIDA_TITULO_DESCRICAO,
)

FORMAT_TITULO_MEDICAMENTO = """
REGRAS DE TÍTULO (sem hífens, até ~70 caracteres - é o que o cliente digita/lê na busca), sem \
repetir tudo que já está em principios_ativos:
- Medicamento COM marca/nome comercial reconhecido: comece pela marca, depois princípio ativo, \
concentração, descritor curto quando existir (sabor, forma), público-alvo quando aplicável \
(Adulto/Infantil), terminando em quantidade + forma farmacêutica por extenso.
- Medicamento genérico (sem marca própria/nome comercial): [Princípio Ativo] [Concentração] \
[Fabricante] Genérico [Quantidade] [Forma Farmacêutica] - use o campo fabricante informado (nome \
curto e reconhecido de mercado, sem sufixo de razão social como "LTDA"/"S/A"/"FARMACÊUTICA"/ \
"INDÚSTRIA" quando esse sufixo não fizer parte do nome comercial usado no mercado - ex: \
"SANOFI MEDLEY FARMACÊUTICA LTDA." -> "Medley"; "UNIÃO QUÍMICA FARMACÊUTICA NACIONAL S/A" -> \
"União Química") como identificador no lugar da marca, sempre seguido da palavra "Genérico". NUNCA \
comece o título pela finalidade terapêutica (ex: "Analgésico", "Antiácido Efervescente") - isso \
atrapalha a busca.
Composição no título: com nome comercial reconhecido e 3 ou mais princípios ativos, NUNCA liste a \
composição completa no título - use só marca + descritor + quantidade/forma. Com 1-2 princípios \
ativos, ou sem nome comercial (genérico/combinação sem marca própria), inclua nome + concentração \
de cada um. NUNCA escreva uma concentração sem o nome do princípio ativo do lado. O nome do \
princípio ativo no título (incluindo o sal - Cloridrato/Maleato/Besilato/Succinato/Bromidrato/ \
Fumarato/Mesilato/Oxalato etc.) tem que ser EXATAMENTE o texto que veio no campo \
principios_ativos informado - NUNCA troque por outro sal do mesmo fármaco só porque parece mais \
comum ou mais familiar (ex: não escreva "Cloridrato de Midazolam" se principios_ativos disser \
"Maleato de Midazolam" - são sais diferentes, trocar é erro factual, não estilo). Antes de \
responder, confira se o sal que você escreveu é literalmente o mesmo texto informado, não uma \
variação "mais comum".
Forma farmacêutica: sempre por extenso, nunca abrevie nem copie o código bruto da apresentação da \
ANVISA/CMED. Ex: "COM REV" -> "Comprimidos Revestidos"; "COM ORODISP" -> "Comprimidos \
Orodispersíveis"; "COM MAST" -> "Comprimidos Mastigáveis"; "SUS ORAL" -> "Suspensão Oral"; "XPE" -> \
"Xarope"; "SOL ORAL" -> "Solução Oral"; "POM" -> "Pomada"; "CREM"/"CRE" -> "Creme". Ignore por \
completo os códigos de embalagem que vêm junto na apresentação bruta (CT, BL, AL, PLAS, FR, ENV, \
VD, AMB etc.) - não fazem parte do título nem da forma farmacêutica, são só embalagem/frasco.
Exemplos: "Novalgina 1g Dipirona Adulto 20 Comprimidos"; "Vurtuoso Vortioxetina 20mg 60 \
Comprimidos"; "Paracetamol 750mg EMS Genérico 20 Comprimidos" (genérico); "Cloridrato de \
Amitriptilina 25mg Medley Genérico 30 Comprimidos Revestidos" (genérico, mantém o nome do sal - \
não simplifique "Cloridrato de X" para só "X", é como o mercado nomeia o genérico); "Gastrol Pó \
Efervescente Sabor Laranja 6 Envelopes 5g" (nome comercial com 3 princípios ativos - composição só \
em principios_ativos); "Diosmina 450mg + Hesperidina 50mg 30 Comprimidos" (sem marca própria, \
inclui composição completa).
"""

FORMAT_CAMPOS_SYSTEM = (
    FORMAT_INTRO
    + FORMAT_TITULO_MEDICAMENTO
    + FORMAT_DESCRICAO
    + FORMAT_CATEGORIZACAO
)

FORMAT_CAMPOS_SEM_CATEGORIA = (
    FORMAT_INTRO
    + FORMAT_TITULO_MEDICAMENTO
    + FORMAT_DESCRICAO
    + FORMAT_SAIDA_TITULO_DESCRICAO
)
