import os


def _origens(valor: str) -> list[str]:
    return [item.strip() for item in valor.split(",") if item.strip()]


class Settings:
    app_name = "Catálogo Radar e-commerce"
    api_prefix = "/api/v1"
    modelo_padrao = os.environ.get("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")
    cors_origins = _origens(
        os.environ.get(
            "CORS_ORIGINS",
            "http://localhost:5173,http://localhost:3000,"
            "http://127.0.0.1:5173,http://127.0.0.1:3000",
        )
    )
    pg_host = os.environ.get("PG_HOST", "localhost")
    pg_port = os.environ.get("PG_PORT", "5433")
    pg_user = os.environ.get("PG_USER", "cadastro")
    pg_password = os.environ.get("PG_PASSWORD", "cadastro")
    pg_db = os.environ.get("PG_DB", "cadastro_produtos")
    pagina_padrao = 50
    pagina_maxima = 200

    @property
    def dsn(self) -> dict:
        return {
            "host": self.pg_host,
            "port": self.pg_port,
            "user": self.pg_user,
            "password": self.pg_password,
            "dbname": self.pg_db,
        }

    def anthropic_configurada(self) -> bool:
        chave = os.environ.get("ANTHROPIC_API_KEY", "")
        return bool(chave) and "COLOQUE_SUA_KEY_AQUI" not in chave


settings = Settings()
