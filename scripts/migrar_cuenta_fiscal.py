"""Agrega datos bancarios para pagos con factura a configuracion_nota.

Uso (desde la raíz del proyecto): python scripts/migrar_cuenta_fiscal.py
Es idempotente; puede ejecutarse varias veces.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text  # noqa: E402

from app import app  # noqa: E402
from config import db  # noqa: E402

SENTENCIAS = [
    "ALTER TABLE configuracion_nota ADD COLUMN IF NOT EXISTS cuenta_fiscal_banco VARCHAR(60)",
    "ALTER TABLE configuracion_nota ADD COLUMN IF NOT EXISTS cuenta_fiscal_titular VARCHAR(150)",
    "ALTER TABLE configuracion_nota ADD COLUMN IF NOT EXISTS cuenta_fiscal_clabe VARCHAR(18)",
]


def main():
    with app.app_context():
        with db.engine.begin() as conn:
            for sql in SENTENCIAS:
                conn.execute(text(sql))
                print(f"OK: {sql}")
    print("Migración de cuenta fiscal completada.")


if __name__ == "__main__":
    main()
