"""Apertura del cajón de dinero conectado a una impresora."""
import os
import sys

from flask import current_app, flash, has_request_context


def abrir_cajon() -> bool:
    """Abre el cajón usando el puerto de la impresora configurada."""
    impresora = os.environ.get('CAJON_IMPRESORA', '').strip()
    if not impresora or sys.platform != 'win32':
        current_app.logger.warning('Cajón: apertura simulada')
        return True

    try:
        import win32print

        comando = os.environ.get('CAJON_COMANDO', '1b700019fa')
        datos = bytes.fromhex(''.join(comando.split()))

        impresora_handle = None
        documento_iniciado = False
        pagina_iniciada = False
        try:
            impresora_handle = win32print.OpenPrinter(impresora)
            win32print.StartDocPrinter(
                impresora_handle, 1, ('Cajon PrintFlow', None, 'RAW')
            )
            documento_iniciado = True
            try:
                win32print.StartPagePrinter(impresora_handle)
                pagina_iniciada = True
                win32print.WritePrinter(impresora_handle, datos)
            finally:
                try:
                    if pagina_iniciada:
                        win32print.EndPagePrinter(impresora_handle)
                finally:
                    if documento_iniciado:
                        win32print.EndDocPrinter(impresora_handle)
        finally:
            if impresora_handle is not None:
                win32print.ClosePrinter(impresora_handle)
        return True
    except Exception:
        current_app.logger.exception('No se pudo abrir el cajón')
        return False


def abrir_cajon_o_avisar() -> bool:
    """Intenta abrir el cajón y avisa si no fue posible."""
    resultado = abrir_cajon()
    if not resultado and has_request_context():
        flash(
            'Se guardó, pero no se pudo abrir el cajón. Ábrelo con la llave.',
            'warning',
        )
    return resultado
