from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import landscape, letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.platypus import (
    Image,
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from config import db
from modelos.configuracion_nota import apartados_para

# Paleta (misma que la nota de pedidos activos)
AZUL = colors.HexColor("#2F62E0")
MARINO = colors.HexColor("#1B2A5E")
TINTA = colors.HexColor("#1F2937")
GRIS = colors.HexColor("#6B7280")
FONDO_BANDA = colors.HexColor("#F2F6FC")
FONDO_CAJA = colors.HexColor("#F3F4F6")
LINEA = colors.HexColor("#E5E7EB")
LINEA_ENCABEZADO = colors.HexColor("#D1D5DB")
BLANCO = colors.white

POLITICAS = (
    "Favor de revisar su producto al momento de recibir. No hay cambios ni "
    "devoluciones una vez que sale de sucursal.",
    "Si después de 15 días de su fecha de entrega el cliente no ha recogido "
    "su material, no nos hacemos responsables por daño o pérdida.",
    "No se hacen devoluciones por cancelación.",
    "Es responsabilidad del cliente revisar su material antes de dejarlo "
    "para impresión o proceso.",
)


def _texto(valor):
    return escape(str(valor or "")).replace("\n", "<br/>")


def _pesos(valor):
    return f"${float(valor or 0):,.2f}"


def _fecha(valor):
    return valor.strftime("%d/%m/%Y") if valor else "—"


def _hora(valor):
    return valor.strftime("%I:%M %p") if valor else "—"


def _estilo(nombre, **kw):
    base = dict(fontName="Helvetica", fontSize=9, leading=12, textColor=TINTA, alignment=TA_LEFT)
    base.update(kw)
    return ParagraphStyle(nombre, **base)


ESTILOS = {
    "marca": _estilo("Marca", fontName="Helvetica-Bold", fontSize=22, leading=25, textColor=MARINO),
    "lema": _estilo("Lema", fontName="Helvetica-Oblique", fontSize=10.5, leading=13, textColor=GRIS),
    "sucursal": _estilo("Sucursal", fontSize=10, leading=13),
    "doc_titulo": _estilo("DocTitulo", fontName="Helvetica-Bold", fontSize=13, leading=16,
                          textColor=AZUL, alignment=TA_RIGHT),
    "folio": _estilo("Folio", fontName="Helvetica-Bold", fontSize=20, leading=23, alignment=TA_RIGHT),
    "emitida": _estilo("Emitida", fontSize=9.5, leading=12, textColor=GRIS, alignment=TA_RIGHT),
    "banda": _estilo("Banda", fontSize=10.5, leading=13, textColor=GRIS),
    "th": _estilo("Th", fontName="Helvetica-Bold", fontSize=8.5, leading=11, textColor=GRIS),
    "th_der": _estilo("ThDer", fontName="Helvetica-Bold", fontSize=8.5, leading=11,
                      textColor=GRIS, alignment=TA_RIGHT),
    "td": _estilo("Td", fontSize=10, leading=13),
    "td_der": _estilo("TdDer", fontSize=10, leading=13, alignment=TA_RIGHT),
    "td_der_b": _estilo("TdDerB", fontName="Helvetica-Bold", fontSize=10, leading=13, alignment=TA_RIGHT),
    "seccion": _estilo("Seccion", fontName="Helvetica-Bold", fontSize=9, leading=12),
    "titulo_bloque_azul": _estilo("TituloBloqueAzul", fontName="Helvetica-Bold", fontSize=9, leading=12,
                                  textColor=BLANCO),
    "nota": _estilo("Nota", fontSize=9.5, leading=13),
    "apartado_texto": _estilo("ApartadoTexto", fontSize=7.5, leading=9.5),
    "dato_etq": _estilo("DatoEtq", fontSize=8, leading=10, textColor=GRIS),
    "dato_val": _estilo("DatoVal", fontName="Helvetica-Bold", fontSize=9.5, leading=12),
    "caja_etq": _estilo("CajaEtq", fontSize=8.5, leading=11, textColor=GRIS, alignment=TA_RIGHT),
    "caja_val": _estilo("CajaVal", fontName="Helvetica-Bold", fontSize=15, leading=18, alignment=TA_RIGHT),
    "caja_etq_dest": _estilo("CajaEtqDest", fontSize=8.5, leading=11, textColor=BLANCO, alignment=TA_RIGHT),
    "caja_val_dest": _estilo("CajaValDest", fontName="Helvetica-Bold", fontSize=15, leading=18,
                             textColor=BLANCO, alignment=TA_RIGHT),
    "politica": _estilo("Politica", fontSize=8, leading=10.5, textColor=GRIS, leftIndent=9,
                        bulletIndent=0),
}


def _p(texto, estilo="td"):
    return Paragraph(texto, ESTILOS[estilo])


def _sin_padding(extra=()):
    return TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        *extra,
    ])


def _caja(etiqueta, valor, ancho, destacada=False):
    caja = Table(
        [[_p(etiqueta, "caja_etq_dest" if destacada else "caja_etq")],
         [_p(valor, "caja_val_dest" if destacada else "caja_val")]],
        colWidths=[ancho],
    )
    caja.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), AZUL if destacada else FONDO_CAJA),
        ("ROUNDEDCORNERS", [6, 6, 6, 6]),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (0, 0), 8),
        ("BOTTOMPADDING", (0, 0), (0, 0), 1),
        ("TOPPADDING", (0, 1), (0, 1), 0),
        ("BOTTOMPADDING", (0, 1), (0, 1), 9),
    ]))
    return caja


def _apartado(apartado, ancho, media_columna=False, relleno_inferior=0):
    """Tabla de un apartado personalizado.

    A todo el ancho, 2+ bloques se reparten en dos columnas; a media columna van
    siempre en una sola.
    """
    estilo_texto = ParagraphStyle(
        "ApartadoTextoTicket",
        fontName="Helvetica",
        fontSize=8,
        leading=10.5,
        textColor=colors.HexColor("#334155"),
    )
    celdas = []
    for bloque in apartado["bloques"]:
        subtitulo = (bloque.get("subtitulo") or "").strip()
        cuerpo = (bloque.get("texto") or "").strip()
        if not subtitulo and not cuerpo:
            continue
        texto = f'<font color="#334155">{_texto(cuerpo)}</font>'
        if subtitulo:
            texto = (
                f'<font name="Helvetica-Bold" color="#1B2A5E">{_texto(subtitulo)}</font>'
                f"<br/>{texto}"
            )
        celdas.append(Paragraph(texto, estilo_texto))

    columnas = 2 if len(celdas) >= 2 and not media_columna else 1
    if len(celdas) % columnas:
        celdas.append("")
    titulo_texto = (apartado.get("titulo") or "").strip()
    filas = [celdas[i:i + columnas] for i in range(0, len(celdas), columnas)]
    if titulo_texto:
        titulo = [_p(_texto(titulo_texto.upper()), "titulo_bloque_azul")] + [""] * (columnas - 1)
        filas.insert(0, titulo)
    if not filas:
        filas = [[""]]

    tabla = Table(filas, colWidths=[ancho / columnas] * columnas)
    color_borde = colors.HexColor("#CBD5E1")
    estilo = [
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, -1), (-1, -1), 9 + relleno_inferior),
        ("BACKGROUND", (0, 0), (-1, -1), BLANCO),
        ("BOX", (0, 0), (-1, -1), 0.8, color_borde),
        ("ROUNDEDCORNERS", [6] * 4),
    ]
    fila_contenido = 0
    if titulo_texto:
        estilo.extend([
            ("SPAN", (0, 0), (-1, 0)),
            ("TOPPADDING", (0, 0), (-1, 0), 9),
            ("BACKGROUND", (0, 0), (-1, 0), AZUL),
            ("BACKGROUND", (0, 1), (-1, -1), BLANCO),
        ])
        fila_contenido = 1
    if columnas == 2:
        estilo.extend([
            ("RIGHTPADDING", (0, fila_contenido), (0, -1), 3),
            ("LEFTPADDING", (1, fila_contenido), (1, -1), 3),
        ])
    tabla.setStyle(TableStyle(estilo))
    return tabla


class _CanvasNumerado(rl_canvas.Canvas):
    """Guarda cada página hasta el final para poder escribir "Página X de Y"."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._paginas = []
        self.pie = None  # (texto, x_derecha, y) que fija el callback de página

    def showPage(self):
        self._paginas.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        total = len(self._paginas)
        for estado in self._paginas:
            self.__dict__.update(estado)
            if self.pie:
                texto, x, y = self.pie
                self.saveState()
                self.setFont("Helvetica", 7)
                self.setFillColor(GRIS)
                self.drawRightString(x, y, f"{texto} · Página {self._pageNumber} de {total}")
                self.restoreState()
            super().showPage()
        super().save()


def generar_ticket_pdf(pedido, atendio="", configuracion=None, logo_path=None):
    """Devuelve la nota de pedido en PDF (en memoria) con el diseño de la nota de pedidos activos."""
    nombre_negocio = getattr(configuracion, "nombre_negocio", None) or "AC 628 IMPRENTA"
    lema = getattr(configuracion, "lema", None) or "imprimimos tus ideas"
    direccion = getattr(configuracion, "direccion", None) or "Zaragoza 628 Sur, Centro de Santa Catarina"
    telefono_negocio = getattr(configuracion, "telefono", None) or "81 1250 5911"
    politicas_raw = getattr(configuracion, "politicas", None)
    politicas = (
        tuple(linea.strip() for linea in politicas_raw.splitlines() if linea.strip())
        if configuracion is not None and politicas_raw is not None
        else POLITICAS
    )

    salida = BytesIO()
    doc = SimpleDocTemplate(
        salida,
        pagesize=landscape(letter),
        rightMargin=14 * mm,
        leftMargin=14 * mm,
        topMargin=12 * mm,
        bottomMargin=12 * mm,
        title=f"Nota de pedido {pedido.folio}",
        author=nombre_negocio,
    )
    # SimpleDocTemplate deja 6 pt de padding a cada lado del Frame interno.
    ancho = doc.width - 12
    historia = []

    # 1. Encabezado: logo + negocio | NOTA DE PEDIDO + folio + fecha de elaboración
    marca = [
        _p(_texto(nombre_negocio), "marca"),
        _p(_texto(lema), "lema") if lema else Spacer(1, 2),
        Spacer(1, 4),
        _p(_texto(direccion), "sucursal"),
        _p(f"Tel. {_texto(telefono_negocio)}", "sucursal"),
    ]
    ancho_der = ancho * 0.32
    ancho_izq = ancho - ancho_der
    if logo_path and Path(logo_path).is_file():
        logo = Image(str(logo_path), width=26 * mm, height=26 * mm, kind="proportional")
        identidad = Table([[logo, marca]], colWidths=[32 * mm, ancho_izq - 32 * mm])
        identidad.setStyle(_sin_padding([("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
    else:
        identidad = marca

    elaborada = pedido.fecha_creacion.strftime("%d/%m/%Y %I:%M %p").lower() if pedido.fecha_creacion else "—"
    documento = [
        _p("NOTA DE PEDIDO", "doc_titulo"),
        Spacer(1, 2),
        _p(_texto(pedido.folio), "folio"),
        Spacer(1, 2),
        _p(f"Elaborada: {elaborada}", "emitida"),
    ]
    encabezado = Table([[identidad, documento]], colWidths=[ancho_izq, ancho_der])
    encabezado.setStyle(_sin_padding([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
        ("LINEBELOW", (0, 0), (-1, -1), 2.2, AZUL),
    ]))
    historia.extend([encabezado, Spacer(1, 10)])

    # 2. Banda con cliente y entrega
    banda = Table([[
        _p(f"Cliente: <font color='#1F2937'><b>{_texto(pedido.nombre_cliente)}</b></font>", "banda"),
        _p(f"Teléfono: <font color='#1F2937'><b>{_texto(pedido.telefono_cliente)}</b></font>", "banda"),
        _p(f"Entrega: <font color='#1F2937'><b>{_fecha(pedido.fecha_entrega)} · "
           f"{_hora(pedido.fecha_entrega)}</b></font>", "banda"),
    ]], colWidths=[ancho * 0.42, ancho * 0.25, ancho * 0.33])
    banda.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), FONDO_BANDA),
        ("ROUNDEDCORNERS", [6, 6, 6, 6]),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 12),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 9),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
    ]))
    historia.extend([banda, Spacer(1, 10)])

    # 3. Productos
    filas = [[_p("CANT.", "th"), _p("DESCRIPCIÓN", "th"), _p("P. UNIT.", "th_der"), _p("SUBTOTAL", "th_der")]]
    for detalle in pedido.detalles:
        filas.append([
            _p(_texto(f"{float(detalle.cantidad):g}")),
            _p(_texto(detalle.descripcion)),
            _p(_pesos(detalle.precio_unitario), "td_der"),
            _p(_pesos(detalle.subtotal), "td_der_b"),
        ])
    if len(filas) == 1:
        filas.append([_p("—"), _p("Sin productos registrados"), _p(""), _p("—", "td_der")])

    tabla = Table(
        filas,
        colWidths=[ancho * 0.10, ancho * 0.56, ancho * 0.16, ancho * 0.18],
        repeatRows=1,
    )
    tabla.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, 0), 4),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 7),
        ("TOPPADDING", (0, 1), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 1), (-1, -1), 7),
        ("LINEBELOW", (0, 0), (-1, 0), 1.5, LINEA_ENCABEZADO),
        ("LINEBELOW", (0, 1), (-1, -1), 0.6, LINEA),
    ]))
    historia.extend([tabla, Spacer(1, 12)])

    factura = getattr(pedido, "factura", None)

    # 4. Subtotal, IVA (si aplica) y total. Sin factura conserva el diseño existente.
    if factura:
        filas_totales = [[_p("Subtotal", "td"), _p(_pesos(pedido.subtotal), "td_der")]]
        if float(pedido.iva or 0) > 0:
            filas_totales.append([_p("IVA 16%", "td"), _p(_pesos(pedido.iva), "td_der")])
        filas_totales.append([_p("TOTAL", "td_der_b"), _p(_pesos(pedido.total_float), "td_der_b")])
        tabla_totales = Table(filas_totales, colWidths=[ancho * 0.20, ancho * 0.20], hAlign="RIGHT")
        tabla_totales.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LINEABOVE", (0, -1), (-1, -1), 0.8, LINEA_ENCABEZADO),
        ]))
        historia.extend([tabla_totales, Spacer(1, 10)])

    # 5. Pagos y comisiones
    hay_comisiones = any(float(pago.comision or 0) > 0 for pago in pedido.pagos)
    if factura or hay_comisiones:
        filas_pagos = [[_p("MÉTODO", "th"), _p("MONTO", "th_der")]]
        if hay_comisiones:
            filas_pagos[0].append(_p("COMISIÓN", "th_der"))
        for pago in pedido.pagos:
            fila = [_p(_texto(pago.metodo_pago)), _p(_pesos(pago.monto), "td_der")]
            if hay_comisiones:
                fila.append(_p(_pesos(pago.comision) if float(pago.comision or 0) > 0 else "—", "td_der"))
            filas_pagos.append(fila)
        if len(filas_pagos) == 1:
            filas_pagos.append([_p("Sin pagos registrados"), _p("—", "td_der")])
            if hay_comisiones:
                filas_pagos[-1].append(_p("—", "td_der"))
        widths_pagos = [ancho * (0.50 if hay_comisiones else 0.62), ancho * 0.19]
        if hay_comisiones:
            widths_pagos.append(ancho * 0.19)
        tabla_pagos = Table(filas_pagos, colWidths=widths_pagos, hAlign="RIGHT", repeatRows=1)
        tabla_pagos.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, 0), 4),
            ("BOTTOMPADDING", (0, 0), (-1, 0), 7),
            ("TOPPADDING", (0, 1), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 1), (-1, -1), 5),
            ("LINEBELOW", (0, 0), (-1, 0), 1.2, LINEA_ENCABEZADO),
            ("LINEBELOW", (0, 1), (-1, -1), 0.5, LINEA),
        ]))
        historia.extend([tabla_pagos, Spacer(1, 10)])

    # 6. Datos de pago/atención (izquierda) y cajas de totales (derecha)
    metodos = ", ".join(dict.fromkeys(pago.metodo_pago for pago in pedido.pagos if pago.metodo_pago))
    datos = [
        ("Forma de pago", _texto(metodos) or "Pendiente"),
        ("Responsable", _texto(pedido.nombre_responsable) or "Sin asignar"),
        ("Atendió", _texto(atendio) or "—"),
    ]
    if getattr(pedido, "requiere_factura", False):
        datos.append(("Factura", "Sí"))
    ancho_datos = ancho * 0.36
    # Responsable suele traer varios nombres: le damos más espacio.
    pesos = [0.26, 0.50, 0.24] if len(datos) == 3 else [0.22, 0.42, 0.20, 0.16]
    datos_tabla = Table(
        [[_p(etq, "dato_etq") for etq, _ in datos], [_p(val, "dato_val") for _, val in datos]],
        colWidths=[ancho_datos * f for f in pesos],
    )
    datos_tabla.setStyle(_sin_padding([
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 2),
    ]))

    separacion = 8
    ancho_cajas = ancho - ancho_datos - 10
    ancho_caja = (ancho_cajas - 2 * separacion) / 3
    cajas = Table([[
        _caja("TOTAL", _pesos(pedido.total_float), ancho_caja),
        "",
        _caja("ANTICIPO", _pesos(pedido.total_pagado), ancho_caja),
        "",
        _caja("RESTO POR PAGAR", _pesos(pedido.saldo_restante), ancho_caja, destacada=True),
    ]], colWidths=[ancho_caja, separacion, ancho_caja, separacion, ancho_caja])
    cajas.setStyle(_sin_padding())

    resumen = Table([[datos_tabla, "", cajas]], colWidths=[ancho_datos, 10, ancho_cajas])
    resumen.setStyle(_sin_padding([("VALIGN", (0, 0), (0, 0), "MIDDLE")]))
    historia.extend([resumen, Spacer(1, 6)])

    # 7. Datos fiscales y cuenta bancaria, solo si el pedido tiene factura
    if factura:
        filas_factura = [
            [_p("RFC", "dato_etq"), _p(_texto(factura.rfc), "dato_val"),
             _p("Razón social", "dato_etq"), _p(_texto(factura.razon_social), "dato_val")],
            [_p("Régimen fiscal", "dato_etq"), _p(_texto(factura.regimen_fiscal), "dato_val"),
             _p("CP fiscal", "dato_etq"), _p(_texto(factura.cp_fiscal), "dato_val")],
            [_p("Uso de CFDI", "dato_etq"), _p(_texto(factura.uso_cfdi), "dato_val"),
             _p("Correo", "dato_etq"), _p(_texto(factura.correo) if factura.correo else "—", "dato_val")],
        ]
        tabla_factura = Table(filas_factura, colWidths=[ancho * 0.13, ancho * 0.37, ancho * 0.13, ancho * 0.37])
        tabla_factura.setStyle(_sin_padding([
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LINEABOVE", (0, 0), (-1, 0), 0.6, LINEA),
        ]))
        historia.extend([_p("DATOS DE FACTURACIÓN", "seccion"), tabla_factura])

        cuenta_fiscal = configuracion is not None and any((
            getattr(configuracion, "cuenta_fiscal_banco", None),
            getattr(configuracion, "cuenta_fiscal_titular", None),
            getattr(configuracion, "cuenta_fiscal_clabe", None),
        ))
        if cuenta_fiscal:
            transferencia = " · ".join(
                _texto(valor) for valor in (
                    configuracion.cuenta_fiscal_banco,
                    configuracion.cuenta_fiscal_titular,
                    configuracion.cuenta_fiscal_clabe,
                ) if valor
            )
            historia.append(_p(f"<b>Transferencias:</b> {transferencia}", "nota"))
        historia.append(Spacer(1, 8))

    # 8. Detalles del pedido (notas), solo si hay
    if (pedido.notas or "").strip():
        detalles = Table(
            [[_p("DETALLES DEL PEDIDO", "seccion")], [_p(_texto(pedido.notas.strip()), "nota")]],
            colWidths=[ancho],
        )
        detalles.setStyle(_sin_padding([
            ("LINEABOVE", (0, 0), (-1, 0), 0.6, LINEA),
            ("TOPPADDING", (0, 0), (-1, 0), 10),
            ("BOTTOMPADDING", (0, 0), (-1, 0), 3),
            ("BOTTOMPADDING", (0, 1), (-1, 1), 12),
        ]))
        historia.append(detalles)

    # 5b. Apartados personalizados de la configuración. Se consultan aquí, cuando el
    # pedido ya está cargado, y un fallo no debe impedir que se genere la nota.
    try:
        apartados = apartados_para("nota", pedido) if configuracion is not None else []
    except Exception:
        db.session.rollback()
        apartados = []
    apartados = [
        apartado for apartado in apartados
        if any(
            (bloque.get("subtitulo") or "").strip()
            or (bloque.get("texto") or "").strip()
            for bloque in apartado["bloques"]
        )
    ]
    # Rejilla de 2 en 2; si son impares, el último va a todo el ancho.
    separacion_apartados = 6
    ancho_medio = ancho / 2 - separacion_apartados / 2
    for i in range(0, len(apartados), 2):
        par = apartados[i:i + 2]
        if len(par) == 2:
            _, alto_izq = _apartado(par[0], ancho_medio, True).wrap(ancho_medio, 0)
            _, alto_der = _apartado(par[1], ancho_medio, True).wrap(ancho_medio, 0)
            alto = max(alto_izq, alto_der)
            tarjeta_izquierda = _apartado(
                par[0], ancho_medio, True, relleno_inferior=alto - alto_izq
            )
            tarjeta_derecha = _apartado(
                par[1], ancho_medio, True, relleno_inferior=alto - alto_der
            )
            fila = Table(
                [[tarjeta_izquierda, "", tarjeta_derecha]],
                colWidths=[ancho_medio, separacion_apartados, ancho_medio],
            )
            fila.setStyle(_sin_padding([
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]))
        else:
            fila = KeepTogether([_apartado(par[0], ancho)])
        historia.extend([fila, Spacer(1, 6)])

    # 6. Términos y políticas
    bloque = [[_p("TÉRMINOS Y POLÍTICAS", "seccion")]]
    bloque.extend([[Paragraph(_texto(t), ESTILOS["politica"], bulletText="•")] for t in politicas])
    terminos = Table(bloque, colWidths=[ancho])
    terminos.setStyle(_sin_padding([
        ("LINEABOVE", (0, 0), (-1, 0), 0.6, LINEA),
        ("TOPPADDING", (0, 0), (-1, 0), 4),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 4),
        ("BOTTOMPADDING", (0, 1), (-1, -1), 1.5),
    ]))
    historia.append(KeepTogether([terminos]))

    # Pie (todas las páginas) y encabezado de continuación (de la 2 en adelante),
    # alineados con el contenido dentro de los márgenes.
    x_izq = doc.leftMargin + 6
    x_der = doc.leftMargin + doc.width - 6
    folio = pedido.folio or ""

    def _primera_pagina(lienzo, documento):
        lienzo.pie = (f"Nota {folio}", x_der, doc.bottomMargin - 5 * mm)

    def _paginas_siguientes(lienzo, documento):
        _primera_pagina(lienzo, documento)
        lienzo.saveState()
        lienzo.setFont("Helvetica", 7)
        lienzo.setFillColor(GRIS)
        lienzo.drawString(x_izq, doc.pagesize[1] - doc.topMargin + 4 * mm,
                          f"{nombre_negocio} · Nota {folio} (continuación)")
        lienzo.restoreState()

    doc.build(historia, onFirstPage=_primera_pagina, onLaterPages=_paginas_siguientes,
              canvasmaker=_CanvasNumerado)
    salida.seek(0)
    return salida