"""Agrega pedidos.id_cliente (FK a clientes) y su índice.

Uso (desde la raíz del proyecto):  python scripts/migrar_clientes.py

Solo modifica el esquema: NO crea clientes a partir de los pedidos existentes.
Es idempotente; puede ejecutarse varias veces.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text  # noqa: E402

from app import app  # noqa: E402
from config import db  # noqa: E402
from modelos.cliente import Cliente  # noqa: E402

SENTENCIAS = [
    "ALTER TABLE pedidos ADD COLUMN IF NOT EXISTS id_cliente INTEGER "
    "REFERENCES clientes(id_cliente) ON DELETE SET NULL",
    "CREATE INDEX IF NOT EXISTS ix_pedidos_id_cliente ON pedidos (id_cliente)",
]


def main():
    with app.app_context():
        # La FK necesita que exista la tabla clientes (no hace nada si ya existe).
        Cliente.__table__.create(db.engine, checkfirst=True)
        with db.engine.begin() as conn:
            for sql in SENTENCIAS:
                conn.execute(text(sql))
                print(f"OK: {sql}")
    print("Migración de clientes completada.")


if __name__ == "__main__":
    main()
