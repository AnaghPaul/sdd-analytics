import os

from psycopg_pool import ConnectionPool

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://telemetry:telemetry@localhost:5433/telemetry"
)

pool = ConnectionPool(conninfo=DATABASE_URL, min_size=1, max_size=10, open=True)


def get_conn():
    with pool.connection() as conn:
        yield conn
