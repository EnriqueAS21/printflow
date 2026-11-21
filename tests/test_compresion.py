import gzip
import unittest

from flask import Flask, jsonify

from servicios.compresion import configurar_compresion

HTML = '<html>' + '<div class="tarjeta">pedido</div>' * 200 + '</html>'


class CompresionTestCase(unittest.TestCase):
    def setUp(self):
        app = Flask(__name__)
        configurar_compresion(app)
        app.add_url_rule('/grande', 'grande', lambda: HTML)
        app.add_url_rule('/pequena', 'pequena', lambda: '<p>ok</p>')
        app.add_url_rule('/json', 'json', lambda: jsonify(datos=['x' * 50] * 100))
        app.add_url_rule('/binario', 'binario', lambda: ('a' * 5000, 200, {'Content-Type': 'application/octet-stream'}))
        self.cliente = app.test_client()

    def test_comprime_html_grande_si_el_cliente_acepta_gzip(self):
        r = self.cliente.get('/grande', headers={'Accept-Encoding': 'gzip'})
        self.assertEqual(r.headers.get('Content-Encoding'), 'gzip')
        self.assertIn('Accept-Encoding', r.headers.get('Vary', ''))
        self.assertEqual(gzip.decompress(r.data).decode(), HTML)
        self.assertLess(len(r.data), len(HTML) / 5)

    def test_sin_gzip_no_comprime(self):
        r = self.cliente.get('/grande', headers={'Accept-Encoding': 'identity'})
        self.assertIsNone(r.headers.get('Content-Encoding'))
        self.assertEqual(r.data.decode(), HTML)

    def test_respuesta_menor_a_1kb_no_se_comprime(self):
        r = self.cliente.get('/pequena', headers={'Accept-Encoding': 'gzip'})
        self.assertIsNone(r.headers.get('Content-Encoding'))

    def test_json_se_comprime_y_es_identico(self):
        r = self.cliente.get('/json', headers={'Accept-Encoding': 'gzip'})
        self.assertEqual(r.headers.get('Content-Encoding'), 'gzip')
        sin = self.cliente.get('/json', headers={'Accept-Encoding': 'identity'})
        self.assertEqual(gzip.decompress(r.data), sin.data)

    def test_otros_tipos_no_se_tocan(self):
        r = self.cliente.get('/binario', headers={'Accept-Encoding': 'gzip'})
        self.assertIsNone(r.headers.get('Content-Encoding'))


if __name__ == '__main__':
    unittest.main()
