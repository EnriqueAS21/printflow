"""Agrega pedidos.fecha_entregado y rellena los pedidos ya Entregado.

Uso (desde la raíz del proyecto):  python scripts/migrar_fecha_entregado.py

Relleno: fecha del último registro de bitácora en que el pedido pasó a Entregado;
si no hay, fecha_entrega. Es idempotente; solo toca filas con fecha_entregado vacía.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text  # noqa: E402

from app import app  # noqa: E402
from config import db  # noqa: E402

SENTENCIAS = [
    "ALTER TABLE pedidos ADD COLUMN IF NOT EXISTS fecha_entregado TIMESTAMP NULL",
    """UPDATE pedidos p SET fecha_entregado = COALESCE(
         (SELECT MAX(b.fecha) FROM bitacora b
           WHERE b.entidad = 'pedido' AND b.id_entidad = p.id_pedido
             AND b.accion = 'PEDIDO_ESTADO' AND b.detalle LIKE '%: Entregado'),
         p.fecha_entrega)
       WHERE p.estatus = 'Entregado' AND p.fecha_entregado IS NULL""",
    "CREATE INDEX IF NOT EXISTS ix_pedidos_estatus_fecha_entregado ON pedidos (estatus, fecha_entregado)",
]


def main():
    with app.app_context():
        with db.engine.begin() as conn:
            for sql in SENTENCIAS:
                conn.execute(text(sql))
                print(f"OK: {sql.splitlines()[0]}")
    print("Migración de fecha_entregado completada.")


if __name__ == "__main__":
    main()
