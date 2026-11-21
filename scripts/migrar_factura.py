"""Crea la tabla pedido_factura si aún no existe.

Uso (desde la raíz del proyecto): python scripts/migrar_factura.py
Es idempotente; puede ejecutarse varias veces.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app  # noqa: E402
from config import db  # noqa: E402
from modelos.pedido import DatosFactura  # noqa: E402


def main():
    with app.app_context():
        DatosFactura.__table__.create(db.engine, checkfirst=True)
    print("Migración de datos de factura completada.")


if __name__ == "__main__":
    main()
