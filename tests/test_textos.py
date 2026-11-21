import unittest

from servicios.textos import normalizar_correo, normalizar_nombre, normalizar_rfc


class NormalizacionTextosTestCase(unittest.TestCase):
    def test_nombre_colapsa_espacios_y_conserva_acentos(self):
        self.assertEqual(normalizar_nombre("  josé   pérez "), "JOSÉ PÉREZ")
        self.assertEqual(normalizar_nombre("peña\tnúñez"), "PEÑA NÚÑEZ")

    def test_nombre_vacio_es_none(self):
        self.assertIsNone(normalizar_nombre(None))
        self.assertIsNone(normalizar_nombre(""))
        self.assertIsNone(normalizar_nombre("   "))

    def test_rfc_con_guiones_y_espacios(self):
        self.assertEqual(normalizar_rfc("abc-010101-xy1"), "ABC010101XY1")
        self.assertEqual(normalizar_rfc(" ABC 010101 XY1 "), "ABC010101XY1")
        self.assertIsNone(normalizar_rfc(" - "))

    def test_correo_en_mayusculas(self):
        self.assertEqual(normalizar_correo("  Ventas@Empresa.MX "), "ventas@empresa.mx")
        self.assertIsNone(normalizar_correo("  "))

    def test_es_idempotente(self):
        for funcion, texto in ((normalizar_nombre, " josé  pérez"),
                               (normalizar_rfc, "abc-010101-xy1"),
                               (normalizar_correo, " A@B.MX ")):
            una = funcion(texto)
            self.assertEqual(funcion(una), una)


if __name__ == "__main__":
    unittest.main()
