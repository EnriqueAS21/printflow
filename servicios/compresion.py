"""Compresión gzip de las respuestas (Waitress no comprime por sí solo).

Solo HTML, JSON, CSS y JS de 1 KB o más, con gzip nivel 5. Los PDF, imágenes y descargas
no se tocan. El tablero completo baja de ~1.7 MB a ~100 KB.
"""
from flask_compress import Compress

NIVEL_GZIP = 5
TAMANO_MINIMO = 1024
MIMETYPES = (
    'text/html', 'text/css', 'text/javascript', 'application/javascript', 'application/json',
)


def configurar_compresion(app):
    app.config.update(
        COMPRESS_MIMETYPES=list(MIMETYPES),
        COMPRESS_ALGORITHM=['gzip'],   # sin brotli/zstd: mismo comportamiento en cualquier PC
        COMPRESS_LEVEL=NIVEL_GZIP,
        COMPRESS_MIN_SIZE=TAMANO_MINIMO,
    )
    Compress(app)
