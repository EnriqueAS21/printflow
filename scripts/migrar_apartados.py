"""Agrega configuracion_nota.apartados (JSON con los apartados personalizados).

Uso (desde la raíz del proyecto):  python scripts/migrar_apartados.py

Es idempotente; puede ejecutarse varias veces.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text  # noqa: E402

from app import app  # noqa: E402
from config import db  # noqa: E402

SENTENCIAS = [
    "ALTER TABLE configuracion_nota ADD COLUMN IF NOT EXISTS apartados JSON NOT NULL DEFAULT '[]'",
]


def main():
    with app.app_context():
        with db.engine.begin() as conn:
            for sql in SENTENCIAS:
                conn.execute(text(sql))
                print(f"OK: {sql}")
    print("Migración de apartados completada.")


if __name__ == "__main__":
    main()
