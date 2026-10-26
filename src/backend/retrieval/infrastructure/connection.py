"""Async PostgreSQL connection for read-only retrieval."""
import os

import psycopg
from psycopg.rows import dict_row


async def open_connection():
    # Never load .env implicitly or log connection strings. Standard PG* variables also work.
    return await psycopg.AsyncConnection.connect(
        os.environ.get('RETRIEVAL_DATABASE_URL', os.environ.get('DATABASE_URL', '')),
        connect_timeout=2, row_factory=dict_row)
