"""Agrega la comisión de tarjeta a las ventas de mostrador (movimientos_caja).

Uso (desde la raíz del proyecto): python scripts/migrar_comision_mostrador.py
La migración es idempotente.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text  # noqa: E402

from app import app  # noqa: E402
from config import db  # noqa: E402

SENTENCIAS = [
    "ALTER TABLE movimientos_caja ADD COLUMN IF NOT EXISTS comision NUMERIC(10,2) NOT NULL DEFAULT 0",
]


def main():
    with app.app_context():
        with db.engine.begin() as conn:
            for sql in SENTENCIAS:
                conn.execute(text(sql))
                print(f"OK: {sql}")
    print("Migración de comisión de mostrador completada.")


if __name__ == "__main__":
    main()
