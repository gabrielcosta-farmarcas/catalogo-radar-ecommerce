import drive


class _Exec:
    def __init__(self, valor):
        self.valor = valor

    def execute(self, num_retries=0):
        return self.valor


class _Files:
    """Drive falso: guarda pastas/arquivos em memória e registra as chamadas."""

    def __init__(self, existentes=None):
        self.existentes = existentes or {}  # (nome, pai) -> id
        self.criados = []
        self.atualizados = []
        self.proximo = 0

    def _novo_id(self):
        self.proximo += 1
        return f"id{self.proximo}"

    def list(self, q, **kw):
        nome = q.split("name = '")[1].split("'")[0]
        pai = q.split("and '")[1].split("'")[0]
        achado = self.existentes.get((nome, pai))
        return _Exec({"files": [{"id": achado}] if achado else []})

    def create(self, body, media_body=None, **kw):
        novo = self._novo_id()
        self.existentes[(body["name"], body["parents"][0])] = novo
        self.criados.append(body["name"])
        return _Exec({"id": novo, "webViewLink": f"https://drive/{novo}"})

    def update(self, fileId, media_body=None, **kw):
        self.atualizados.append(fileId)
        return _Exec({"id": fileId, "webViewLink": f"https://drive/{fileId}"})


class _Servico:
    def __init__(self, files):
        self._files = files

    def files(self):
        return self._files


def _ligar(monkeypatch, files):
    monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_JSON", "{}")
    monkeypatch.setenv("DRIVE_FOLDER_ID", "RAIZ")
    monkeypatch.setattr(drive, "_servico", lambda: _Servico(files))
    drive._pastas_cache.clear()


def _jpg(tmp_path):
    caminho = tmp_path / "x.jpg"
    caminho.write_bytes(b"\xff\xd8\xff\xd9")
    return str(caminho)


def test_desligado_sem_variaveis(monkeypatch, tmp_path):
    monkeypatch.delenv("GOOGLE_SERVICE_ACCOUNT_JSON", raising=False)
    monkeypatch.delenv("DRIVE_FOLDER_ID", raising=False)
    assert drive.configurado() is False
    assert drive.enviar_imagem(_jpg(tmp_path), "789", medicamento=True) is None


def test_cria_pasta_e_arquivo_e_devolve_link(monkeypatch, tmp_path):
    files = _Files()
    _ligar(monkeypatch, files)
    link = drive.enviar_imagem(_jpg(tmp_path), "7896637023672", medicamento=True)
    assert link.startswith("https://drive/")
    assert files.criados == ["medicamentos", "7896637023672.jpg"]


def test_nao_medicamento_vai_para_outra_pasta(monkeypatch, tmp_path):
    files = _Files()
    _ligar(monkeypatch, files)
    drive.enviar_imagem(_jpg(tmp_path), "123", medicamento=False)
    assert files.criados[0] == "nao-medicamentos"


def test_reenvio_atualiza_em_vez_de_duplicar(monkeypatch, tmp_path):
    files = _Files()
    _ligar(monkeypatch, files)
    drive.enviar_imagem(_jpg(tmp_path), "123", medicamento=False)
    antes = list(files.criados)
    drive.enviar_imagem(_jpg(tmp_path), "123", medicamento=False)
    assert files.criados == antes
    assert len(files.atualizados) == 1


def test_ean_com_pontuacao_vira_so_digitos(monkeypatch, tmp_path):
    files = _Files()
    _ligar(monkeypatch, files)
    drive.enviar_imagem(_jpg(tmp_path), "78-96.63", medicamento=False)
    assert "789663.jpg" in files.criados


def test_falha_do_drive_devolve_none_sem_levantar(monkeypatch, tmp_path):
    _ligar(monkeypatch, _Files())

    def quebra():
        raise RuntimeError("sem permissão")

    monkeypatch.setattr(drive, "_servico", quebra)
    assert drive.enviar_imagem(_jpg(tmp_path), "123", medicamento=False) is None


class _Cur:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.conn.chamadas.append((sql, params))


class _Conn:
    def __init__(self):
        self.chamadas = []
        self.commits = 0

    def cursor(self):
        return _Cur(self)

    def commit(self):
        self.commits += 1


def test_enviar_imagem_drive_grava_link_no_produto(monkeypatch):
    import enrich_produtos as ep

    monkeypatch.setattr(ep.drive, "configurado", lambda: True)
    monkeypatch.setattr(ep.drive, "enviar_imagem", lambda *a, **k: "https://drive/abc")
    conn = _Conn()
    assert ep.enviar_imagem_drive(conn, "123", "/x.jpg", True) == "https://drive/abc"
    assert conn.chamadas[0][1] == ("https://drive/abc", "123")
    assert conn.commits == 1


def test_enviar_imagem_drive_sem_link_nao_toca_no_banco(monkeypatch):
    import enrich_produtos as ep

    monkeypatch.setattr(ep.drive, "configurado", lambda: True)
    monkeypatch.setattr(ep.drive, "enviar_imagem", lambda *a, **k: None)
    conn = _Conn()
    assert ep.enviar_imagem_drive(conn, "123", "/x.jpg", False) is None
    assert conn.chamadas == [] and conn.commits == 0
