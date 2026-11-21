"""Etiquetas 50x80 mm (horizontal) para Phomemo M110.

- generar_imagen(pedido): dibuja la etiqueta HORIZONTAL (80 ancho x 50 alto mm).
- imprimir(pedido): la rota 90 grados (el papel sale de la M110 a lo largo de
    los 80 mm) y la manda a Phomemo CLI o guarda un PNG de prueba, según el modo.

Requiere: pip install pillow qrcode
"""
import base64
import os
import shlex
import threading
import subprocess
import tempfile
from functools import lru_cache
from io import BytesIO
from dotenv import load_dotenv
from flask import current_app, request, url_for
from PIL import Image, ImageDraw, ImageFont
load_dotenv()

try:
    import qrcode
except ImportError:  # la etiqueta funciona sin QR
    qrcode = None

DPI = 203
ANCHO = round(80 / 25.4 * DPI)   # 639 px (largo del papel)
ALTO = round(50 / 25.4 * DPI)    # 400 px (ancho del papel)
ROTACION_GRADOS = 90             # si la etiqueta sale al reves, cambia a -90 (270)

_NEGRITA = ('arialbd.ttf', 'Arial Bold.ttf', 'DejaVuSans-Bold.ttf')
_NORMAL = ('arial.ttf', 'Arial.ttf', 'DejaVuSans.ttf')

# Una sola impresion a la vez (Waitress atiende con varios hilos).
_candado = threading.Lock()


@lru_cache(maxsize=None)
def _fuente(negrita, tamano):
    for nombre in (_NEGRITA if negrita else _NORMAL):
        try:
            return ImageFont.truetype(nombre, tamano)
        except OSError:
            continue
    return ImageFont.load_default(tamano)


def _ajustar(d, texto, negrita, maximo, minimo, ancho):
    """Mayor tamano de fuente (entre maximo y minimo) en el que el texto cabe."""
    for tam in range(maximo, minimo - 1, -2):
        if d.textlength(texto, font=_fuente(negrita, tam)) <= ancho:
            return _fuente(negrita, tam)
    return _fuente(negrita, minimo)


def _envolver(d, texto, fuente, ancho_max):
    lineas, actual = [], ''
    for palabra in texto.split():
        prueba = f'{actual} {palabra}'.strip()
        if d.textlength(prueba, font=fuente) <= ancho_max:
            actual = prueba
            continue
        if actual:
            lineas.append(actual)
        while d.textlength(palabra, font=fuente) > ancho_max:
            corte = len(palabra)
            while corte > 1 and d.textlength(palabra[:corte], font=fuente) > ancho_max:
                corte -= 1
            lineas.append(palabra[:corte])
            palabra = palabra[corte:]
        actual = palabra
    if actual:
        lineas.append(actual)
    return lineas


def _hora12(f):
    return f"{f.hour % 12 or 12}:{f.minute:02d} {'AM' if f.hour < 12 else 'PM'}"


def _qr(texto, lado):
    if qrcode is None:
        return None
    q = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_L, border=0, box_size=1)
    q.add_data(texto)
    q.make(fit=True)
    modulos = q.modules_count
    escala = max(lado // modulos, 1)
    return q.make_image(fill_color='black', back_color='white').convert('L').resize(
        (modulos * escala, modulos * escala), Image.NEAREST)


def generar_qr_data_uri(texto, lado=240):
    """Genera una imagen QR PNG incrustada para mostrarla en documentos HTML."""
    imagen = _qr(texto, lado)
    if imagen is None:
        return None
    salida = BytesIO()
    imagen.save(salida, format='PNG')
    contenido = base64.b64encode(salida.getvalue()).decode('ascii')
    return f'data:image/png;base64,{contenido}'


def _url_pedido(pedido):
    """Construye la URL absoluta a la ficha del pedido para codificarla en el QR."""
    base = (current_app.config.get('URL_BASE') or request.host_url).rstrip('/')
    ruta = url_for('pedidos.ficha', folio=pedido.folio)
    return f'{base}{ruta}'


def generar_imagen(pedido):
    print("========== SE EJECUTÓ generar_imagen() ==========", flush=True)
    print(f"Folio: {pedido.folio}", flush=True)
    
    img = Image.new('L', (ANCHO, ALTO), 255)
    d = ImageDraw.Draw(img)
    M = 22                       # margen interior
    COL_DER = 478                # inicio de la columna del QR / estado
    ANCHO_IZQ = COL_DER - M - 16

    # ---- marco y cabecera negra: folio + entrega ----
    d.rounded_rectangle((3, 3, ANCHO - 4, ALTO - 4), radius=18, outline=0, width=4)
    d.rounded_rectangle((3, 3, ANCHO - 4, 84), radius=18, fill=0)
    d.rectangle((3, 50, ANCHO - 4, 84), fill=0)

    ancho_folio = ANCHO - 2 * M
    if pedido.fecha_entrega:
        f = pedido.fecha_entrega
        txt_ent = f"{f.strftime('%d/%m/%y')}  {_hora12(f)}"
        f_ent = _fuente(True, 28)
        d.text((ANCHO - M, 24), 'ENTREGA', font=_fuente(True, 16), fill=255, anchor='rm')
        d.text((ANCHO - M, 56), txt_ent, font=f_ent, fill=255, anchor='rm')
        ancho_folio -= d.textlength(txt_ent, font=f_ent) + 24
    d.text((M, 44), pedido.folio, font=_ajustar(d, pedido.folio, True, 64, 36, ancho_folio),
           fill=255, anchor='lm')

    # ---- columna izquierda ----
    y = 96
    d.text((M, y), 'CLIENTE', font=_fuente(True, 18), fill=0)
    d.text((M + 110, y - 1), f'Tel. {pedido.telefono_cliente}', font=_fuente(False, 20), fill=0)
    y += 22
    f_cli = _ajustar(d, pedido.nombre_cliente, True, 30, 22, ANCHO_IZQ)
    for linea in _envolver(d, pedido.nombre_cliente, f_cli, ANCHO_IZQ)[:2]:
        d.text((M, y), linea, font=f_cli, fill=0)
        y += f_cli.size + 3
    y += 4

    d.text((M, y), 'RESPONSABLE', font=_fuente(True, 18), fill=0)
    y += 20
    f_resp = _fuente(False, 22)
    for linea in _envolver(d, pedido.nombre_responsable, f_resp, ANCHO_IZQ)[:2]:
        d.text((M, y), linea, font=f_resp, fill=0)
        y += 25
    y += 4
    d.line((M, y, COL_DER - 16, y), fill=0, width=2)
    y += 5

    d.text((M, y), 'ARTÍCULOS', font=_fuente(True, 18), fill=0)
    y += 20
    articulos = [f"{float(x.cantidad or 0):g} x {x.descripcion}" for x in pedido.detalles] \
        or [pedido.notas or 'Sin especificaciones']
    f_art = _fuente(False, 22)
    lh = 25
    limite = ALTO - 32
    mostrados = 0
    for i, art in enumerate(articulos):
        lineas = _envolver(d, art, f_art, ANCHO_IZQ)
        reserva = lh if i < len(articulos) - 1 else 0
        if y + len(lineas) * lh + reserva > limite:
            break
        for linea in lineas:
            d.text((M, y), linea, font=f_art, fill=0)
            y += lh
        mostrados += 1
    if mostrados < len(articulos):
        d.text((M, min(y, limite - lh + 4)), f'... +{len(articulos) - mostrados} más',
               font=_fuente(True, 22), fill=0)

    

    # ---- columna derecha: QR + estado de pago + factura ----
    ancho_der = ANCHO - M - COL_DER
    yd = 96
    url_qr = _url_pedido(pedido)
    current_app.logger.info("URL codificada en QR: %s", url_qr)

    qr = _qr(url_qr, ancho_der)
    if qr:
        img.paste(qr, (COL_DER + (ancho_der - qr.width) // 2, yd))
        yd += qr.height + 10

    if pedido.estado_pago == 'Pagado':
        d.rounded_rectangle((COL_DER, yd, ANCHO - M, yd + 52), radius=10, fill=0)
        d.text(((COL_DER + ANCHO - M) / 2, yd + 26), 'PAGADO',
               font=_ajustar(d, 'PAGADO', True, 30, 18, ancho_der - 12), fill=255, anchor='mm')
    else:
        monto = f"${pedido.saldo_restante:,.2f}"
        d.rounded_rectangle((COL_DER, yd, ANCHO - M, yd + 52), radius=10, outline=0, width=3)
        d.text(((COL_DER + ANCHO - M) / 2, yd + 14), 'SALDO', font=_fuente(True, 16), fill=0, anchor='mm')
        d.text(((COL_DER + ANCHO - M) / 2, yd + 36), monto,
               font=_ajustar(d, monto, True, 26, 14, ancho_der - 12), fill=0, anchor='mm')
    yd += 60

    if pedido.requiere_factura:
        d.rounded_rectangle((COL_DER, yd, ANCHO - M, yd + 34), radius=17, fill=0)
        d.text(((COL_DER + ANCHO - M) / 2, yd + 17), 'FACTURA', font=_fuente(True, 20),
               fill=255, anchor='mm')

    # ---- pie ----
    if pedido.fecha_creacion:
        d.text((M, ALTO - 14), f"Recibido: {pedido.fecha_creacion.strftime('%d/%m/%y')}",
               font=_fuente(False, 18), fill=0, anchor='ls')

    return img.point(lambda p: 255 if p > 150 else 0).convert('1', dither=Image.Dither.NONE)


def _imprimir_windows(img, impresora):
    import win32ui
    from PIL import ImageWin

    hdc = win32ui.CreateDC()
    hdc.CreatePrinterDC(impresora)
    try:
        ancho_px = hdc.GetDeviceCaps(8)    # HORZRES: área imprimible
        # Se conserva la proporción de la etiqueta
        alto_px = round(ancho_px * img.height / img.width)
        hdc.StartDoc('Etiqueta PrintFlow')
        hdc.StartPage()
        ImageWin.Dib(img.convert('RGB')).draw(hdc.GetHandleOutput(), (0, 0, ancho_px, alto_px))
        hdc.EndPage()
        hdc.EndDoc()
    finally:
        hdc.DeleteDC()


def modo_impresion():
    """Devuelve el modo configurado, resolviendo ``auto`` a real o prueba."""
    modo = os.environ.get('ETIQUETAS_MODO', 'auto').strip().lower()
    if modo not in ('auto', 'real', 'prueba'):
        raise RuntimeError("ETIQUETAS_MODO debe ser 'auto', 'real' o 'prueba'.")
    if modo == 'auto':
        phomemo_cli = os.environ.get('PHOMEMO_CLI', '').strip()
        return 'real' if phomemo_cli and os.path.isfile(phomemo_cli) else 'prueba'
    return modo



def imprimir(pedido):
    print("========== SE EJECUTÓ imprimir() ==========", flush=True)
    print(f"Folio recibido: {pedido.folio}", flush=True)

    img = generar_imagen(pedido)
    modo = modo_impresion()

    if modo == 'prueba':
        carpeta = os.path.join(
            current_app.root_path, 'etiquetas_prueba'
        )
        os.makedirs(carpeta, exist_ok=True)
        ruta = os.path.join(carpeta, f'{pedido.folio}.png')
        img.save(ruta)
        current_app.logger.info(
            'Etiqueta de prueba guardada en %s', ruta
        )
        return {'modo': 'prueba', 'ruta': ruta, 'stdout': ''}

    # Leer configuración desde las variables de entorno (.env).
    phomemo_cli = os.environ.get('PHOMEMO_CLI', '').strip()
    direccion = os.environ.get('PHOMEMO_ADDR', '').strip()
    etiqueta_tamano = os.environ.get('PHOMEMO_LABEL', '50x80').strip()

    try:
        densidad = int(os.environ.get('PHOMEMO_DENSITY', '10'))
        velocidad = int(os.environ.get('PHOMEMO_SPEED', '3'))
    except ValueError as exc:
        raise RuntimeError(
            'PHOMEMO_DENSITY y PHOMEMO_SPEED deben ser números enteros.'
        ) from exc

    # Validar la configuración antes de intentar imprimir.
    if not phomemo_cli:
        raise RuntimeError(
            'Falta configurar PHOMEMO_CLI en las variables de entorno.'
        )

    if not os.path.isfile(phomemo_cli):
        raise RuntimeError(
            f'No se encontró el ejecutable de Phomemo: {phomemo_cli}'
        )

    if not direccion:
        raise RuntimeError(
            'Falta configurar PHOMEMO_ADDR con la dirección Bluetooth de la M110.'
        )

    if etiqueta_tamano not in ('50x80', '80x50'):
        raise RuntimeError(
            'PHOMEMO_LABEL debe ser 50x80 o 80x50.'
        )

    if not 1 <= densidad <= 15:
        raise RuntimeError('PHOMEMO_DENSITY debe estar entre 1 y 15.')

    if not 1 <= velocidad <= 5:
        raise RuntimeError('PHOMEMO_SPEED debe estar entre 1 y 5.')

    # Evitar dos impresiones simultáneas desde distintos hilos de Flask.
    with _candado:
        ruta_temporal = None

        try:
            # La prueba física correcta utilizó imagen girada y etiqueta 50x80.
            etiqueta = img.rotate(ROTACION_GRADOS, expand=True)

            with tempfile.NamedTemporaryFile(
                suffix='.png',
                prefix='printflow_etiqueta_',
                delete=False
            ) as archivo:
                ruta_temporal = archivo.name

            etiqueta.save(ruta_temporal, format='PNG')

            comando = [
                phomemo_cli,
                'print-image',
                ruta_temporal,
                '--addr', direccion,
                '--label', etiqueta_tamano,
                '--density', str(densidad),
                '--speed', str(velocidad),
            ]
            comando_log = comando.copy()
            comando_log[comando_log.index('--addr') + 1] = '<PHOMEMO_ADDR>'
            current_app.logger.info(
                'Ejecutando comando de impresión: %s', shlex.join(comando_log)
            )
            resultado = subprocess.run(
                comando,
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
                cwd=os.path.dirname(phomemo_cli) or None,
            )
            current_app.logger.info(
                'Comando de impresión finalizado; returncode=%s',
                resultado.returncode,
            )

            if resultado.returncode != 0:
                detalle = (
                    resultado.stderr.strip()
                    or resultado.stdout.strip()
                    or 'El comando de impresión terminó con error.'
                )
                raise RuntimeError(detalle)

            stdout = resultado.stdout

        except subprocess.TimeoutExpired as exc:
            current_app.logger.warning(
                'Comando de impresión agotó el tiempo de espera; returncode=timeout'
            )
            raise RuntimeError(
                'La impresión superó los 120 segundos. '
                'Comprueba que la M110 esté encendida y disponible por Bluetooth.'
            ) from exc

        except FileNotFoundError as exc:
            current_app.logger.error(
                'No se pudo iniciar el comando de impresión; returncode=unavailable'
            )
            raise RuntimeError(
                'No se encontró el ejecutable o un archivo necesario para imprimir. '
                'Verifica PHOMEMO_CLI y la instalación de pyphomemo.'
            ) from exc

        except Exception as exc:
            current_app.logger.exception(
                'Error al imprimir el pedido %s.', pedido.folio
            )
            raise RuntimeError(
                f'No se pudo imprimir la etiqueta del pedido '
                f'{pedido.folio}: {exc}'
            ) from exc

        finally:
            if ruta_temporal and os.path.exists(ruta_temporal):
                try:
                    os.remove(ruta_temporal)
                except OSError:
                    current_app.logger.warning(
                        'No se pudo eliminar el PNG temporal: %s',
                        ruta_temporal
                    )
        return {'modo': 'real', 'ruta': None, 'stdout': stdout}
