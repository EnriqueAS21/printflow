"""Agrega índices para acelerar el tablero, la caja y la bitácora.

Uso (desde la raíz del proyecto):  python scripts/migrar_indices_rendimiento.py

Es idempotente; puede ejecutarse varias veces.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text  # noqa: E402

from app import app  # noqa: E402
from config import db  # noqa: E402

SENTENCIAS = [
    "CREATE INDEX IF NOT EXISTS ix_pedidos_estatus_fecha_entrega ON pedidos (estatus, fecha_entrega)",
    # Mismo nombre que genera index=True en el modelo: si ya existe, no se duplica.
    "CREATE INDEX IF NOT EXISTS ix_pagos_id_pedido ON pagos (id_pedido)",
    "CREATE INDEX IF NOT EXISTS ix_pedido_responsables_id_usuario ON pedido_responsables (id_usuario)",
    "CREATE INDEX IF NOT EXISTS ix_bitacora_entidad_accion_id_bitacora ON bitacora (entidad, accion, id_bitacora)",
    "CREATE INDEX IF NOT EXISTS ix_pagos_fecha_pago ON pagos (fecha_pago)",
]


def main():
    with app.app_context():
        with db.engine.begin() as conn:
            for sql in SENTENCIAS:
                conn.execute(text(sql))
                print(f"OK: {sql}")
    print("Migración de índices de rendimiento completada.")


if __name__ == "__main__":
    main()
