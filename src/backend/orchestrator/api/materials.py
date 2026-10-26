"""Serve only registered bank PDFs; never expose the raw data directory."""
import asyncio
from pathlib import Path
import re

from fastapi import HTTPException
from fastapi.responses import FileResponse

from retrieval.infrastructure.connection import open_connection


def registered_pdf(root, filename):
    root = Path(root).resolve()
    path = (root / filename).resolve()
    if path.parent != root or path.suffix.lower() != '.pdf' or not path.is_file():
        raise HTTPException(404, 'Материал не найден')
    return path


def register_materials(app, root):
    @app.get('/v1/materials/{document_id}', response_class=FileResponse)
    async def material(document_id: str):
        if not re.fullmatch(r'[0-9a-f]{64}', document_id):
            raise HTTPException(404, 'Материал не найден')
        try:
            async with asyncio.timeout(1):
                async with await open_connection() as conn:
                    await conn.execute('SET TRANSACTION READ ONLY')
                    await conn.execute("SET LOCAL statement_timeout = '500ms'")
                    row = await (await conn.execute(
                        'SELECT source_file FROM insights.documents WHERE document_id=%s',
                        (document_id,))).fetchone()
        except Exception:
            # Connection details and SQL must never be included in an HTTP response.
            raise HTTPException(503, 'Хранилище материалов недоступно') from None
        if not row:
            raise HTTPException(404, 'Материал не найден')
        path = registered_pdf(root, row['source_file'])
        return FileResponse(path, media_type='application/pdf', filename=path.name,
                            content_disposition_type='inline', headers={'X-Content-Type-Options':'nosniff'})
