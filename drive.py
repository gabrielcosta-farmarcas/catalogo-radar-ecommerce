"""
Envio das fotos de produto para uma pasta do Google Drive.

Estrutura na pasta raiz (DRIVE_FOLDER_ID):
    medicamentos/{ean}.jpg
    nao-medicamentos/{ean}.jpg

Autenticação por service account: a pasta raiz precisa estar compartilhada com o e-mail dela
(Editor) ou ficar num Drive compartilhado em que ela seja membro.

Variáveis de ambiente:
    GOOGLE_SERVICE_ACCOUNT_JSON   conteúdo da chave JSON da service account (uma linha)
    DRIVE_FOLDER_ID               id da pasta raiz (o trecho final da URL da pasta)

Sem as duas variáveis o módulo fica desligado (`configurado()` é False) e o enriquecimento
segue só com a cópia local. Falha de upload nunca derruba o cadastro: devolve None.

Reenviar o mesmo EAN atualiza o arquivo existente em vez de duplicar.
"""

import json
import os
import threading

PASTA_MEDICAMENTOS = "medicamentos"
PASTA_NAO_MEDICAMENTOS = "nao-medicamentos"

_ESCOPO = ["https://www.googleapis.com/auth/drive"]
_MIME_PASTA = "application/vnd.google-apps.folder"
_TENTATIVAS = 3

_local = threading.local()  # httplib2 não é thread-safe: um cliente por thread (--concurrency)
_lock_pastas = threading.Lock()
_pastas_cache = {}


def configurado():
    return bool(os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON") and os.environ.get("DRIVE_FOLDER_ID"))


def _servico():
    servico = getattr(_local, "servico", None)
    if servico is None:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build

        info = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])
        credenciais = service_account.Credentials.from_service_account_info(info, scopes=_ESCOPO)
        servico = build("drive", "v3", credentials=credenciais, cache_discovery=False)
        _local.servico = servico
    return servico


def _escapar(valor):
    return valor.replace("\\", "\\\\").replace("'", "\\'")


def _buscar(servico, nome, pai, mime_pasta=False):
    consulta = f"name = '{_escapar(nome)}' and '{pai}' in parents and trashed = false"
    if mime_pasta:
        consulta += f" and mimeType = '{_MIME_PASTA}'"
    resposta = servico.files().list(
        q=consulta,
        fields="files(id)",
        pageSize=1,
        supportsAllDrives=True,
        includeItemsFromAllDrives=True,
    ).execute(num_retries=_TENTATIVAS)
    arquivos = resposta.get("files", [])
    return arquivos[0]["id"] if arquivos else None


def _id_pasta(servico, nome):
    raiz = os.environ["DRIVE_FOLDER_ID"]
    chave = (raiz, nome)
    with _lock_pastas:
        if chave in _pastas_cache:
            return _pastas_cache[chave]
        pasta_id = _buscar(servico, nome, raiz, mime_pasta=True)
        if pasta_id is None:
            criada = servico.files().create(
                body={"name": nome, "mimeType": _MIME_PASTA, "parents": [raiz]},
                fields="id",
                supportsAllDrives=True,
            ).execute(num_retries=_TENTATIVAS)
            pasta_id = criada["id"]
        _pastas_cache[chave] = pasta_id
        return pasta_id


def enviar_imagem(caminho_local, ean, medicamento=False):
    """
    Sobe `caminho_local` para {raiz}/{medicamentos|nao-medicamentos}/{ean}.jpg e devolve o link
    (webViewLink) do arquivo, ou None se o Drive não estiver configurado ou o envio falhar.
    """
    if not configurado():
        return None
    nome = f"{''.join(c for c in str(ean) if c.isdigit())}.jpg"
    try:
        from googleapiclient.http import MediaFileUpload

        servico = _servico()
        pasta_id = _id_pasta(servico, PASTA_MEDICAMENTOS if medicamento else PASTA_NAO_MEDICAMENTOS)
        midia = MediaFileUpload(caminho_local, mimetype="image/jpeg", resumable=False)
        existente = _buscar(servico, nome, pasta_id)
        if existente:
            arquivo = servico.files().update(
                fileId=existente,
                media_body=midia,
                fields="id,webViewLink",
                supportsAllDrives=True,
            ).execute(num_retries=_TENTATIVAS)
        else:
            arquivo = servico.files().create(
                body={"name": nome, "parents": [pasta_id]},
                media_body=midia,
                fields="id,webViewLink",
                supportsAllDrives=True,
            ).execute(num_retries=_TENTATIVAS)
        return arquivo.get("webViewLink") or f"https://drive.google.com/file/d/{arquivo['id']}/view"
    except Exception as exc:  # rede, cota, permissão, JSON inválido: nada disso pode derrubar o cadastro
        print(f"  [aviso] não enviou imagem ao Drive para EAN {ean} ({type(exc).__name__}: {exc})")
        return None
