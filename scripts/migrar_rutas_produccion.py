"""Crea el esquema base de rutas de producción y completa datos vacíos.

Uso (desde la raíz del proyecto): python scripts/migrar_rutas_produccion.py

Es idempotente; puede ejecutarse varias veces.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text  # noqa: E402

from app import app  # noqa: E402
from config import db  # noqa: E402
from modelos.pedido import DetalleAsignacion  # noqa: E402
from modelos.produccion import (  # noqa: E402
    LineaPaso,
    Proceso,
    ProductoPaso,
    ReglaClasificacion,
    ReglaPaso,
)


TABLAS_PRODUCCION = [
    Proceso.__table__,
    ProductoPaso.__table__,
    ReglaClasificacion.__table__,
    ReglaPaso.__table__,
    LineaPaso.__table__,
]

SENTENCIAS = [
    "ALTER TABLE productos ADD COLUMN IF NOT EXISTS id_proceso_principal INTEGER "
    "REFERENCES procesos(id) ON DELETE SET NULL",
    "ALTER TABLE detalle_asignaciones ADD COLUMN IF NOT EXISTS fecha_completado TIMESTAMP",
    "ALTER TABLE detalle_asignaciones ADD COLUMN IF NOT EXISTS id_usuario_completo INTEGER "
    "REFERENCES usuarios(id_usuario) ON DELETE SET NULL",
    "ALTER TABLE detalle_asignaciones ADD COLUMN IF NOT EXISTS id_proceso_actual INTEGER "
    "REFERENCES procesos(id) ON DELETE SET NULL",
    "ALTER TABLE detalle_asignaciones ALTER COLUMN id_usuario DROP NOT NULL",
    "CREATE INDEX IF NOT EXISTS ix_productos_id_proceso_principal "
    "ON productos (id_proceso_principal)",
    "CREATE INDEX IF NOT EXISTS ix_producto_pasos_id_producto_orden "
    "ON producto_pasos (id_producto, orden)",
    "CREATE INDEX IF NOT EXISTS ix_regla_pasos_id_regla_orden "
    "ON regla_pasos (id_regla, orden)",
    "CREATE INDEX IF NOT EXISTS ix_linea_pasos_detalle_orden "
    "ON linea_pasos (id_detalle, orden)",
    "CREATE INDEX IF NOT EXISTS ix_linea_pasos_pendientes "
    "ON linea_pasos (id_detalle) WHERE estado = 'pendiente'",
    "CREATE INDEX IF NOT EXISTS ix_detalle_asignaciones_proceso_actual "
    "ON detalle_asignaciones (id_proceso_actual) WHERE completado = false",
    "CREATE INDEX IF NOT EXISTS ix_detalle_asignaciones_fecha_completado "
    "ON detalle_asignaciones (fecha_completado)",
    "CREATE INDEX IF NOT EXISTS ix_detalle_asignaciones_id_usuario "
    "ON detalle_asignaciones (id_usuario)",
]


def main():
    with app.app_context():
        db.metadata.create_all(db.engine, tables=TABLAS_PRODUCCION, checkfirst=True)
        print("OK: tablas de rutas de producción verificadas.")

        with db.engine.begin() as conn:
            for sql in SENTENCIAS:
                conn.execute(text(sql))
                print(f"OK: {sql}")

            conn.execute(text(
                "INSERT INTO procesos (nombre, orden, activo, es_sistema) "
                "VALUES ('Sin clasificar', 0, TRUE, TRUE) "
                "ON CONFLICT (nombre) DO UPDATE SET es_sistema = TRUE"
            ))
            print("OK: proceso de sistema Sin clasificar verificado.")

            conn.execute(text(
                "INSERT INTO detalle_asignaciones "
                "(id_detalle, id_usuario, completado) "
                "SELECT d.id_detalle, NULL, FALSE "
                "FROM detalle_pedido d "
                "WHERE NOT EXISTS ("
                "SELECT 1 FROM detalle_asignaciones a "
                "WHERE a.id_detalle = d.id_detalle"
                ")"
            ))
            print("OK: detalle_asignaciones vacía creada para líneas sin asignación.")

    print("Migración de rutas de producción completada.")


if __name__ == "__main__":
    main()
