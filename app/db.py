from contextlib import contextmanager

import psycopg2
import psycopg2.extras

from app.config import settings
from app.errors import DependencyUnavailable


@contextmanager
def get_conn():
    try:
        conn = psycopg2.connect(**settings.dsn)
    except psycopg2.Error as exc:
        raise DependencyUnavailable(f"Postgres indisponível: {exc}") from exc
    try:
        yield conn
    finally:
        conn.close()


def dict_cursor(conn):
    return conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)


def ping() -> bool:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
            return cur.fetchone()[0] == 1
