"""Normaliza los nombres existentes de procesos de producción.

Uso: python scripts/normalizar_procesos.py

Es idempotente y conserva "Sin clasificar" protegido.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app  # noqa: E402
from config import db  # noqa: E402
from modelos.produccion import Proceso  # noqa: E402
from servicios.textos import normalizar_nombre  # noqa: E402


def main():
    modificados = 0
    with app.app_context():
        for proceso in Proceso.query.order_by(Proceso.id_proceso).all():
            nombre = normalizar_nombre(proceso.nombre)
            if not nombre:
                continue
            if proceso.nombre != nombre:
                proceso.nombre = nombre
                modificados += 1
        db.session.commit()
    print(f"Procesos modificados: {modificados}")


if __name__ == "__main__":
    main()
