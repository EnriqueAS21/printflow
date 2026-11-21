import unittest

from servicios.rutas_produccion import _ruta_para_datos


class RutaProduccionTestCase(unittest.TestCase):
    def setUp(self):
        self.cache = {
            "procesos": {
                1: {"nombre": "Diseño", "orden": 1, "es_sistema": False},
                2: {"nombre": "Impresión", "orden": 2, "es_sistema": False},
            },
            "rutas_producto": {},
            "procesos_principales_producto": {},
            "reglas": [],
            "sin_clasificar": 99,
        }
        self.cache["procesos"][99] = {
            "nombre": "Sin clasificar", "orden": 99, "es_sistema": True,
        }

    def test_producto_con_ruta(self):
        self.cache["rutas_producto"] = {10: [(1, 1, False), (2, 2, True)]}
        self.assertEqual(
            _ruta_para_datos(self.cache, 10, "lonas"),
            [(1, 1, False), (2, 2, True)],
        )

    def test_producto_solo_proceso_principal(self):
        self.cache["procesos_principales_producto"] = {11: 2}
        self.assertEqual(
            _ruta_para_datos(self.cache, 11, "otra"),
            [(1, 2, False)],
        )

    def test_regla_por_palabra_clave(self):
        self.cache["reglas"] = [{
            "palabra_clave": "lona",
            "prioridad": 10,
            "id_proceso_principal": None,
            "pasos": [(1, 1, False), (2, 2, False)],
        }]
        self.assertEqual(
            _ruta_para_datos(self.cache, None, "Imprimir una lona"),
            [(1, 1, False), (2, 2, False)],
        )

    def test_prioridad_entre_reglas(self):
        self.cache["reglas"] = [
            {"palabra_clave": "lona", "prioridad": 20, "id_proceso_principal": 2, "pasos": []},
            {"palabra_clave": "lona", "prioridad": 10, "id_proceso_principal": 1, "pasos": []},
        ]
        self.assertEqual(
            _ruta_para_datos(self.cache, None, "lona grande"),
            [(1, 2, False)],
        )

    def test_sin_coincidencia(self):
        self.assertEqual(
            _ruta_para_datos(self.cache, None, "tarjeta"),
            [(1, 99, False)],
        )

    def test_proceso_inactivo_cae_a_sin_clasificar(self):
        self.cache["reglas"] = [{
            "palabra_clave": "acabado",
            "prioridad": 10,
            "id_proceso_principal": 3,
            "pasos": [(1, 3, False)],
        }]
        self.assertEqual(
            _ruta_para_datos(self.cache, None, "acabado especial"),
            [(1, 99, False)],
        )


if __name__ == "__main__":
    unittest.main()
