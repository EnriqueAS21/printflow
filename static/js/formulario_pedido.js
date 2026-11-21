// Formulario de pedidos: alta y edición. Los datos del servidor llegan como data-* en .pf-form.
window.cargarBuscadorProductos ??= function (callback) {
    if (window.BuscadorProductos) { callback(); return; }
    let script = document.getElementById('buscador-productos-script');
    if (!script) {
        script = document.createElement('script');
        script.id = 'buscador-productos-script';
        script.src = document.querySelector('.pf-form[data-url-buscador-productos]').dataset.urlBuscadorProductos;
        script.addEventListener('load', callback, { once: true });
        document.head.appendChild(script);
    } else {
        script.addEventListener('load', callback, { once: true });
    }
};

// Los listeners globales se registran una sola vez, aunque haya varios formularios.
(function () {
    if (window.__pfFormLoaded) return;
    window.__pfFormLoaded = true;

    const limpiezasFormulario = new WeakMap();

    function agregarLimpieza(form, limpiar) {
        if (!limpiezasFormulario.has(form)) limpiezasFormulario.set(form, []);
        limpiezasFormulario.get(form).push(limpiar);
    }

    function datosFormulario(elemento) {
        return elemento.closest('.pf-form').dataset;
    }

    function puedeUsarProductos(elemento) {
        return datosFormulario(elemento).puedeUsarProductos === '1';
    }

    function poblarResponsables(select) {
        const datos = datosFormulario(select);
        const nombres = JSON.parse(datos.responsableNombres);
        JSON.parse(datos.responsableIds).forEach((id, indice) => {
            select.add(new Option(nombres[indice], id));
        });
    }

    // ---------- Productos ----------
    function agregarFila(btn) {
        const form = btn.closest('form');
        const tbody = form.querySelector('.tabla-productos-dinamica tbody');
        tbody.insertAdjacentHTML('beforeend', `<tr>
            <td><input type="number" step="any" min="0.01" class="form-control" name="cantidad[]" value="1" oninput="calcularSubtotal(this)" required></td>
            <td><input type="hidden" name="detalle_id[]" value=""><input type="text" class="form-control${puedeUsarProductos(form) ? ' pf-producto-descripcion' : ''}" name="descripcion[]" placeholder="Descripción del producto" required autocomplete="off">
                <input type="hidden" name="producto_id[]" value=""><input type="hidden" name="presentacion_id[]" value=""><input type="hidden" name="configuracion[]" value="">
            </td>
            <td><input type="text" inputmode="decimal" class="form-control" name="precio_unitario[]" placeholder="0.00" oninput="calcularSubtotal(this)" required></td>
            <td><input type="text" class="form-control subtotal-field bg-light" readonly value="$0.00"></td>
            <td><select class="form-select select-responsable-producto" name="responsable_producto[]"><option value="">Sin asignar</option></select></td>
            <td><button type="button" class="btn btn-outline-danger btn-sm" onclick="eliminarFilaProducto(this)">✕</button></td>
        </tr>`);
        const nuevaFila = tbody.lastElementChild;
        activarBuscadorFila(nuevaFila);
        const select = nuevaFila.querySelector('.select-responsable-producto');
        poblarResponsables(select);
        const asignarTodos = form.querySelector('.select-asignar-todos');
        if (asignarTodos.value !== '__varios__') select.value = asignarTodos.value;
        sincronizarAsignarTodos(form);
    }

    function eliminarFilaProducto(btn) {
        const form = btn.closest('form');
        const tbody = form.querySelector('.tabla-productos-dinamica tbody');
        if (tbody.querySelectorAll('tr').length > 1) {
            btn.closest('tr').remove();
            calcularGranTotal(form);
            sincronizarAsignarTodos(form);
        }
    }

    function leerPrecio(input) {
        return parseFloat(input.value.replace(',', '.')) || 0;
    }

    function calcularSubtotal(elemento) {
        const fila = elemento.closest('tr');
        const form = elemento.closest('form');
        const cantidad = parseFloat(fila.querySelector('input[name="cantidad[]"]').value) || 0;
        const precio = leerPrecio(fila.querySelector('input[name="precio_unitario[]"]'));
        fila.querySelector('.subtotal-field').value = '$' + (cantidad * precio).toFixed(2);
        calcularGranTotal(form);
    }

    function calcularGranTotal(form) {
        let subtotal = 0;
        form.querySelectorAll('.subtotal-field').forEach(campo => {
            subtotal += parseFloat(campo.value.replace('$', '')) || 0;
        });
        subtotal = Math.round((subtotal + Number.EPSILON) * 100) / 100;
        const requiereFactura = !!form.querySelector('[name="requiere_factura"]')?.checked;
        const iva = requiereFactura
            ? Math.round((subtotal * parseFloat(datosFormulario(form).tasaIva) + Number.EPSILON) * 100) / 100
            : 0;
        const total = Math.round((subtotal + iva + Number.EPSILON) * 100) / 100;

        form.querySelector('.gran-subtotal-display').textContent = '$' + subtotal.toFixed(2);
        form.querySelector('.iva-display').textContent = '$' + iva.toFixed(2);
        form.querySelector('.iva-resumen').classList.toggle('d-none', !requiereFactura);
        form.querySelector('.gran-total-display').textContent = '$' + total.toFixed(2);
        actualizarResumenPago(form);
    }

    function actualizarResumenPago(form) {
        const tarjetaPago = form.querySelector('.tarjeta-pago');
        if (!tarjetaPago) return;

        const total = parseFloat(form.querySelector('.gran-total-display').textContent.replace('$', '')) || 0;
        const pagado = parseFloat(tarjetaPago.dataset.pagado) || 0;
        const input = tarjetaPago.querySelector('.input-anticipo');
        const monto = Math.max(parseFloat(input.value) || 0, 0);
        const saldoDisponible = Math.max(Math.round((total - pagado + Number.EPSILON) * 100) / 100, 0);
        const resta = Math.max(Math.round((saldoDisponible - monto + Number.EPSILON) * 100) / 100, 0);
        const excedido = monto > saldoDisponible;

        tarjetaPago.querySelectorAll('.tabla-registro-pagos tr[data-acumulado]').forEach(fila => {
            const restante = Math.max(Math.round((total - parseFloat(fila.dataset.acumulado) + Number.EPSILON) * 100) / 100, 0);
            const celda = fila.querySelector('.restante-registro');
            celda.textContent = '$' + restante.toFixed(2);
            celda.classList.toggle('text-danger', restante > 0);
            celda.classList.toggle('text-success', restante <= 0);
        });

        tarjetaPago.querySelector('.pago-display').textContent = '$' + monto.toFixed(2);
        tarjetaPago.querySelector('.resta-display').textContent = '$' + resta.toFixed(2);
        tarjetaPago.querySelector('.badge-liquidado').classList.toggle(
            'd-none', !(total > 0 && resta <= 0.000001 && !excedido)
        );

        const mensajeError = tarjetaPago.querySelector('.mensaje-anticipo-error');
        input.classList.toggle('is-invalid', excedido);
        mensajeError.textContent = excedido
            ? `La cantidad no puede superar el saldo pendiente de $${saldoDisponible.toFixed(2)}.`
            : '';

        const metodo = tarjetaPago.querySelector('[name="metodo_pago"]:checked')?.value;
        const resumenComision = tarjetaPago.querySelector('.resumen-comision');
        if (metodo === 'Tarjeta' && monto > 0) {
            const comision = Math.round((monto * parseFloat(datosFormulario(form).tasaComision) + Number.EPSILON) * 100) / 100;
            resumenComision.querySelector('.comision-display').textContent = '$' + comision.toFixed(2);
            resumenComision.querySelector('.cobro-terminal-display').textContent = '$' + (monto + comision).toFixed(2);
            resumenComision.classList.remove('d-none');
        } else {
            resumenComision.classList.add('d-none');
        }
    }

    function actualizarFactura(form) {
        const requiereFactura = !!form.querySelector('[name="requiere_factura"]')?.checked;
        const camposFactura = form.querySelector('.campos-factura');
        camposFactura.classList.toggle('d-none', !requiereFactura);
        camposFactura.querySelectorAll('.dato-factura').forEach(campo => {
            campo.disabled = !requiereFactura;
            campo.required = requiereFactura && campo.name !== 'correo_factura';
        });
        const tarjeta = form.querySelector('[name="metodo_pago"][value="Tarjeta"]');
        if (tarjeta) {
            if (requiereFactura && tarjeta.checked) {
                form.querySelector('[name="metodo_pago"][value="Efectivo"]').checked = true;
            }
            tarjeta.disabled = requiereFactura;
        }
        const etiquetaTransferencia = form.querySelector('.etiqueta-transferencia');
        if (etiquetaTransferencia) {
            etiquetaTransferencia.textContent = requiereFactura
                ? 'Transferencia (cuenta fiscal)' : 'Transferencia';
        }
        calcularGranTotal(form);
    }

    function configurarPago(form) {
        const tarjetaPago = form.querySelector('.tarjeta-pago');
        if (!tarjetaPago || tarjetaPago.dataset.configurado === 'true') return;
        tarjetaPago.dataset.configurado = 'true';

        tarjetaPago.querySelector('.input-anticipo').addEventListener('input', () => actualizarResumenPago(form));
        tarjetaPago.querySelectorAll('[name="metodo_pago"]').forEach(radio => {
            radio.addEventListener('change', () => actualizarResumenPago(form));
        });
        tarjetaPago.querySelector('.btn-liquidar').addEventListener('click', () => {
            const total = parseFloat(form.querySelector('.gran-total-display').textContent.replace('$', '')) || 0;
            const pagado = parseFloat(tarjetaPago.dataset.pagado) || 0;
            const input = tarjetaPago.querySelector('.input-anticipo');
            input.value = Math.max(total - pagado, 0).toFixed(2);
            input.dispatchEvent(new Event('input', { bubbles: true }));
            actualizarResumenPago(form);
        });

        const limpiezaSubmit = new AbortController();
        agregarLimpieza(form, () => limpiezaSubmit.abort());

        form.addEventListener('submit', event => {
            actualizarResumenPago(form);
            const total = parseFloat(form.querySelector('.gran-total-display').textContent.replace('$', '')) || 0;
            const pagado = parseFloat(tarjetaPago.dataset.pagado) || 0;
            const monto = Math.max(parseFloat(tarjetaPago.querySelector('.input-anticipo').value) || 0, 0);
            const saldoDisponible = Math.max(Math.round((total - pagado + Number.EPSILON) * 100) / 100, 0);
            if (monto > saldoDisponible) {
                event.preventDefault();
                tarjetaPago.querySelector('.input-anticipo').focus();
                return;
            }

            if (event.defaultPrevented || form.dataset.efectivoConfirmado === '1') return;
            const metodo = tarjetaPago.querySelector('[name="metodo_pago"]:checked')?.value;
            if (metodo !== 'Efectivo' || monto <= 0) return;

            // Se detiene este envío y, ya cobrado, se repite con el mismo botón (accion=guardar|pdf).
            event.preventDefault();
            const { submitter } = event;
            window.cobrarEfectivo(monto).then(cobro => {
                if (!cobro || limpiezaSubmit.signal.aborted) return;
                form.dataset.efectivoConfirmado = '1';
                try {
                    form.requestSubmit(submitter);
                } finally {
                    // El envío se despacha de forma síncrona; si falla o se regresa con "Atrás", vuelve a cobrar.
                    delete form.dataset.efectivoConfirmado;
                }
            });
        }, { signal: limpiezaSubmit.signal });

        const switchFactura = form.querySelector('[name="requiere_factura"]');
        switchFactura?.addEventListener('change', () => actualizarFactura(form));
        actualizarFactura(form);
    }

    function configurarVistaPrevia(form) {
        const layout = form.closest('.pf-layout');
        const panel = layout?.querySelector('.nota-preview-panel');
        const modal = layout?.querySelector('.modal[id^="modalNotaPedido-"]');
        if (!panel || !modal || panel.dataset.configurado === 'true') return;
        panel.dataset.configurado = 'true';

        const raiz = form.closest('.pf-form');
        const alternar = raiz.querySelector('[data-pf-toggle-preview]');
        // Cerrar solo tiene sentido dentro de un modal; "Volver al Dashboard", solo fuera de uno.
        if (form.closest('.modal')) raiz.querySelector('.pf-volver-dashboard')?.remove();
        else raiz.querySelector('.pf-cerrar-modal')?.remove();

        // Escritorio: muestra/oculta el panel y recuerda la elección. Pantallas angostas: abre la nota en un modal
        // (sin data-bs-toggle, Bootstrap cerraría el modal del formulario al abrir la nota anidada).
        const escritorio = window.matchMedia('(min-width: 992px)');
        const CLAVE_VISTA_PREVIA = 'pf_vista_previa_oculta';
        let ocultaPreferida = false;
        try { ocultaPreferida = localStorage.getItem(CLAVE_VISTA_PREVIA) === '1'; } catch (_e) { /* sin almacenamiento */ }

        function aplicarVistaPrevia() {
            const visible = escritorio.matches && !ocultaPreferida;
            raiz.classList.toggle('pf-sin-preview', !visible);
            if (!alternar) return;
            alternar.setAttribute('aria-expanded', String(visible));
            alternar.querySelector('[data-pf-toggle-preview-texto]').textContent =
                visible ? 'Ocultar vista previa' : 'Ver vista previa';
            alternar.querySelector('i').className = `bi ${visible ? 'bi-eye-slash' : 'bi-eye'} me-1`;
        }

        const limpiezaBarra = new AbortController();
        alternar?.addEventListener('click', () => {
            if (!escritorio.matches) {
                bootstrap.Modal.getOrCreateInstance(modal).show();
                return;
            }
            ocultaPreferida = !ocultaPreferida;
            try { localStorage.setItem(CLAVE_VISTA_PREVIA, ocultaPreferida ? '1' : '0'); } catch (_e) { /* sin almacenamiento */ }
            aplicarVistaPrevia();
        }, { signal: limpiezaBarra.signal });
        escritorio.addEventListener('change', aplicarVistaPrevia, { signal: limpiezaBarra.signal });
        agregarLimpieza(form, () => limpiezaBarra.abort());
        aplicarVistaPrevia();

        const contenido = panel.querySelector('.nota-preview-content');
        const hogar = panel.querySelector('.nota-preview-home');
        const modalHost = modal.querySelector('.preview-modal-host');
        const iframe = contenido.querySelector('.nota-preview-iframe');
        const spinner = contenido.querySelector('.preview-spinner');
        let controlador = null;
        let temporizador = null;
        const limpieza = new AbortController();

        function ajustarAnchoIframe() {
            try {
                if (iframe.hidden) return;
                const documento = iframe.contentDocument;
                const hoja = documento?.querySelector('.hoja');
                const anchoColumna = contenido.clientWidth;
                if (!hoja || !anchoColumna) return;
                documento.documentElement.style.width = '816px';
                documento.documentElement.style.overflowX = 'hidden';
                documento.body.style.padding = '0';
                documento.body.style.margin = '0';
                documento.body.style.width = '816px';
                documento.body.style.overflowX = 'hidden';
                hoja.style.width = '816px';
                hoja.style.maxWidth = 'none';
                hoja.style.margin = '0';
                hoja.style.transform = 'none';
                const altoDocumento = Math.max(
                    hoja.offsetHeight,
                    hoja.scrollHeight,
                    documento.body.scrollHeight,
                    documento.documentElement.scrollHeight
                );
                const escala = anchoColumna / 816;
                iframe.style.width = '816px';
                iframe.style.height = `${altoDocumento}px`;
                iframe.style.transform = `scale(${escala})`;
                contenido.style.height = `${altoDocumento * escala}px`;
            } catch (_error) {
                // El iframe puede estar cargando el documento de vista previa.
            }
        }

        async function cargarVistaPrevia() {
            controlador?.abort();
            controlador = new AbortController();
            spinner.classList.remove('d-none');
            try {
                const respuesta = await fetch(panel.dataset.previewUrl, {
                    method: 'POST',
                    body: new FormData(form),
                    signal: controlador.signal,
                });
                if (!respuesta.ok) {
                    iframe.hidden = true;
                    contenido.style.height = 'auto';
                    spinner.classList.add('d-none');
                    return;
                }

                const html = await respuesta.text();
                iframe.addEventListener('load', () => {
                    ajustarAnchoIframe();
                    spinner.classList.add('d-none');
                }, { once: true });
                iframe.srcdoc = html;
                iframe.hidden = false;
            } catch (fallo) {
                if (fallo.name === 'AbortError') return;
                iframe.hidden = true;
                contenido.style.height = 'auto';
                spinner.classList.add('d-none');
            }
        }

        function solicitarVistaPrevia() {
            clearTimeout(temporizador);
            controlador?.abort();
            spinner.classList.remove('d-none');
            temporizador = setTimeout(cargarVistaPrevia, 800);
        }

        form.addEventListener('input', solicitarVistaPrevia);
        form.addEventListener('change', solicitarVistaPrevia);
        iframe.addEventListener('load', ajustarAnchoIframe);

        modal.addEventListener('show.bs.modal', () => modalHost.appendChild(contenido));
        modal.addEventListener('shown.bs.modal', () => {
            cargarVistaPrevia();
            requestAnimationFrame(ajustarAnchoIframe);
        });
        form.closest('.modal')?.addEventListener('shown.bs.modal', cargarVistaPrevia, { signal: limpieza.signal });
        modal.addEventListener('hidden.bs.modal', () => {
            hogar.appendChild(contenido);
            requestAnimationFrame(ajustarAnchoIframe);
        });
        const observadorPreview = new ResizeObserver(ajustarAnchoIframe);
        observadorPreview.observe(contenido);
        observadorPreview.observe(modalHost);
        window.addEventListener('resize', ajustarAnchoIframe, { signal: limpieza.signal });
        agregarLimpieza(form, () => {
            limpieza.abort();
            clearTimeout(temporizador);
            controlador?.abort();
            observadorPreview.disconnect();
        });

        cargarVistaPrevia();
    }

    function sincronizarAsignarTodos(form) {
        const asignarTodos = form.querySelector('.select-asignar-todos');
        const valores = [...new Set(
            Array.from(form.querySelectorAll('.select-responsable-producto'), select => select.value)
        )];
        let varios = asignarTodos.querySelector('option[value="__varios__"]');

        if (valores.length > 1) {
            if (!varios) {
                varios = new Option('Varios', '__varios__');
                varios.disabled = true;
                asignarTodos.add(varios, asignarTodos.options[0]);
            }
            asignarTodos.value = '__varios__';
            return;
        }

        varios?.remove();
        asignarTodos.value = valores[0] || '';
    }

    function configurarAsignarTodos(form) {
        const asignarTodos = form.querySelector('.select-asignar-todos');
        asignarTodos.addEventListener('change', () => {
            if (asignarTodos.value === '__varios__') return;
            form.querySelectorAll('.select-responsable-producto').forEach(select => {
                select.value = asignarTodos.value;
            });
            sincronizarAsignarTodos(form);
        });
        form.addEventListener('change', event => {
            if (event.target.matches('.select-responsable-producto')) {
                sincronizarAsignarTodos(form);
            }
        });
        sincronizarAsignarTodos(form);
    }

    // ---------- Fecha y hora de entrega ----------
    function fijarFechaEntrega(btn, dias) {
        const d = new Date();
        d.setDate(d.getDate() + dias);
        const f = d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
        const campo = btn.closest('form').querySelector('[name="fecha_entrega"]');
        campo.value = f;
        campo.dispatchEvent(new Event('input', { bubbles: true }));
    }

    function fijarHoraEntrega(btn, hhmm) {
        const campo = btn.closest('form').querySelector('[name="hora_entrega"]');
        campo.value = hhmm;
        campo.dispatchEvent(new Event('input', { bubbles: true }));
    }

    // ---------- Buscador de clientes (nombre_cliente) ----------
    const busquedasCliente = new WeakMap();
    const cacheBusquedasCliente = new Map();
    let listaClienteCompartida = null;
    let inputClienteActivo = null;
    let inputClienteSolicitud = null;
    let controladorCliente = null;
    let secuenciaCliente = 0;

    function obtenerListaClientes() {
        if (!listaClienteCompartida) {
            listaClienteCompartida = document.createElement('div');
            listaClienteCompartida.className = 'list-group shadow pf-cliente-sugerencias d-none';
            listaClienteCompartida.setAttribute('role', 'listbox');
            listaClienteCompartida.id = 'pf-cliente-sugerencias';
        }
        return listaClienteCompartida;
    }

    function posicionarClientes() {
        const input = inputClienteActivo;
        const lista = listaClienteCompartida;
        if (!input || !lista || lista.classList.contains('d-none')) return;
        const rect = input.getBoundingClientRect();
        const visible = input.isConnected && input.getClientRects().length > 0
            && rect.width > 0 && rect.height > 0
            && rect.bottom > 0 && rect.top < window.innerHeight
            && rect.right > 0 && rect.left < window.innerWidth;
        if (!visible || !inputClienteDentroDeVista(input, rect)) {
            cerrarSugerencias(input.closest('form'));
            return;
        }
        const host = input.closest('.modal.show') || document.body;
        if (lista.parentElement !== host) host.appendChild(lista);
        lista.style.left = `${rect.left}px`;
        lista.style.top = `${rect.bottom + 4}px`;
        lista.style.width = `${rect.width}px`;
    }

    function inputClienteDentroDeVista(input, rect) {
        for (let elemento = input.parentElement; elemento; elemento = elemento.parentElement) {
            const estilo = window.getComputedStyle(elemento);
            const recortaX = /auto|scroll|hidden|clip/.test(estilo.overflowX);
            const recortaY = /auto|scroll|hidden|clip/.test(estilo.overflowY);
            if (!recortaX && !recortaY) continue;
            const caja = elemento.getBoundingClientRect();
            if ((recortaX && (rect.right <= caja.left || rect.left >= caja.right))
                || (recortaY && (rect.bottom <= caja.top || rect.top >= caja.bottom))) return false;
        }
        return true;
    }

    document.addEventListener('scroll', posicionarClientes, true);
    window.addEventListener('resize', posicionarClientes);
    document.addEventListener('hidden.bs.modal', event => {
        if (inputClienteSolicitud?.closest('.modal') === event.target) {
            cerrarSugerencias(inputClienteSolicitud.closest('form'));
        }
        if (inputClienteActivo?.closest('.modal') === event.target) {
            cerrarSugerencias(inputClienteActivo.closest('form'));
        }
    });
    document.addEventListener('click', event => {
        if (!inputClienteActivo) return;
        if (!inputClienteActivo.contains(event.target) && !listaClienteCompartida?.contains(event.target)) {
            cerrarSugerencias(inputClienteActivo.closest('form'));
        }
    });

    const escaparHtml = s => String(s ?? '').replace(/[&<>"']/g,
        c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

    function estadoBusqueda(input) {
        if (!busquedasCliente.has(input)) {
            busquedasCliente.set(input, { temporizador: null, resultados: [], activo: -1 });
        }
        return busquedasCliente.get(input);
    }

    function cerrarSugerencias(form) {
        const input = form?.querySelector('.pf-cliente-nombre');
        if (!input) return;
        const estado = estadoBusqueda(input);
        estado.resultados = [];
        estado.activo = -1;
        input.setAttribute('aria-expanded', 'false');
        clearTimeout(estado.temporizador);
        if (inputClienteSolicitud === input) {
            controladorCliente?.abort();
            controladorCliente = null;
            secuenciaCliente++;
            inputClienteSolicitud = null;
        }
        if (inputClienteActivo === input) {
            listaClienteCompartida.innerHTML = '';
            listaClienteCompartida.classList.add('d-none');
            inputClienteActivo = null;
        }
    }

    function marcarSugerencia(form, indice) {
        const input = form.querySelector('.pf-cliente-nombre');
        const estado = estadoBusqueda(input);
        estado.activo = indice;
        listaClienteCompartida?.querySelectorAll('.list-group-item').forEach((item, i) => {
            item.classList.toggle('active', i === indice);
            item.setAttribute('aria-selected', i === indice ? 'true' : 'false');
            if (i === indice) item.scrollIntoView({ block: 'nearest' });
        });
    }

    function mostrarSugerencias(form, clientes) {
        const input = form.querySelector('.pf-cliente-nombre');
        if (!clientes.length) { cerrarSugerencias(form); return; }   // sin resultados: captura libre
        const estado = estadoBusqueda(input);
        const lista = obtenerListaClientes();
        estado.resultados = clientes;
        estado.activo = -1;
        (input.closest('.modal.show') || document.body).appendChild(lista);
        lista.innerHTML = clientes.map((c, i) => `
            <button type="button" class="list-group-item list-group-item-action py-2" role="option"
                    aria-selected="false" data-indice="${i}" tabindex="-1">
                <div class="fw-semibold">${escaparHtml(c.nombre)}</div>
                <div class="small text-muted">${escaparHtml(c.telefono)} · ${escaparHtml(c.clave)}</div>
            </button>`).join('');
        lista.classList.remove('d-none');
        inputClienteActivo = input;
        input.setAttribute('aria-expanded', 'true');
        posicionarClientes();
    }

    function vincularCliente(form, cliente) {
        form.querySelector('.pf-cliente-nombre').value = cliente.nombre;
        form.querySelector('[name="telefono_cliente"]').value = cliente.telefono;
        form.querySelector('[name="id_cliente"]').value = cliente.id_cliente;
        fijarListaPrecios(form, cliente.id_lista_precio, cliente.nombre_lista, cliente.lista_predeterminada);
        form.querySelector('.pf-cliente-pastilla-texto').textContent = `Cliente ${cliente.clave} ✓`;
        form.querySelector('.pf-cliente-pastilla').classList.remove('d-none');
        const guardar = form.querySelector('.pf-guardar-cliente');
        if (guardar) {
            guardar.classList.add('d-none');
            guardar.querySelector('[name="guardar_cliente"]').checked = false;
        }
        cerrarSugerencias(form);
    }

    function desvincularCliente(form) {
        form.querySelector('[name="id_cliente"]').value = '';
        fijarListaPrecios(form, '');
        form.querySelector('.pf-cliente-pastilla').classList.add('d-none');
        form.querySelector('.pf-guardar-cliente')?.classList.remove('d-none');
    }

    async function buscarClientes(input, q) {
        const form = input.closest('form');
        controladorCliente?.abort();
        const controlador = new AbortController();
        controladorCliente = controlador;
        const consulta = ++secuenciaCliente;
        const urlBuscar = datosFormulario(input).urlBuscarClientes;
        const claveCache = `${urlBuscar}|${q.toLocaleLowerCase()}`;
        const cacheado = cacheBusquedasCliente.get(claveCache);
        if (cacheado && Date.now() - cacheado.timestamp < 60000) {
            if (consulta === secuenciaCliente && document.activeElement === input) {
                mostrarSugerencias(form, cacheado.clientes);
            }
            if (inputClienteSolicitud === input) inputClienteSolicitud = null;
            return;
        }
        if (cacheado) cacheBusquedasCliente.delete(claveCache);
        try {
            const resp = await fetch(`${urlBuscar}?q=${encodeURIComponent(q)}`,
                { headers: { 'X-Requested-With': 'XMLHttpRequest' }, signal: controlador.signal });
            const datos = resp.ok ? await resp.json() : [];
            const clientes = Array.isArray(datos) ? datos : [];
            if (resp.ok) {
                const ahora = Date.now();
                cacheBusquedasCliente.forEach((valor, clave) => {
                    if (ahora - valor.timestamp >= 60000) cacheBusquedasCliente.delete(clave);
                });
                cacheBusquedasCliente.set(claveCache, { timestamp: ahora, clientes });
            }
            if (consulta !== secuenciaCliente || document.activeElement !== input) return;
            mostrarSugerencias(form, clientes);
        } catch (error) {
            if (error.name !== 'AbortError' && consulta === secuenciaCliente) cerrarSugerencias(form);
        } finally {
            if (inputClienteSolicitud === input && consulta === secuenciaCliente) {
                inputClienteSolicitud = null;
            }
        }
    }

    document.addEventListener('input', e => {
        const input = e.target;
        if (!input.matches || !input.matches('.pf-cliente-nombre')) return;
        if (input.getAttribute('data-buscador-activo') !== '1') {
            input.setAttribute('data-buscador-activo', '1');
        }
        const form = input.closest('form');
        if (form.querySelector('[name="id_cliente"]').value) desvincularCliente(form);
        if (!datosFormulario(input).urlBuscarClientes) return;
        const estado = estadoBusqueda(input);
        clearTimeout(estado.temporizador);
        if (inputClienteSolicitud) {
            clearTimeout(estadoBusqueda(inputClienteSolicitud).temporizador);
            inputClienteSolicitud = null;
        }
        controladorCliente?.abort();
        controladorCliente = null;
        secuenciaCliente++;
        if (inputClienteActivo) cerrarSugerencias(inputClienteActivo.closest('form'));
        const q = input.value.trim();
        if (q.length < 2) {
            cerrarSugerencias(form);
            return;
        }
        inputClienteSolicitud = input;
        estado.temporizador = setTimeout(() => buscarClientes(input, q), 250);
    });

    document.addEventListener('keydown', e => {
        const input = e.target;
        if (!input.matches || !input.matches('.pf-cliente-nombre')) return;
        const form = input.closest('form');
        const lista = listaClienteCompartida;
        if (inputClienteActivo !== input || !lista || lista.classList.contains('d-none')) return;
        const estado = estadoBusqueda(input);
        const total = estado.resultados.length;
        if (e.key === 'ArrowDown') {
            e.preventDefault();
            marcarSugerencia(form, (estado.activo + 1) % total);
        } else if (e.key === 'ArrowUp') {
            e.preventDefault();
            marcarSugerencia(form, (estado.activo - 1 + total) % total);
        } else if (e.key === 'Enter') {
            e.preventDefault();   // elige, no envía el formulario
            vincularCliente(form, estado.resultados[Math.max(estado.activo, 0)]);
        } else if (e.key === 'Escape') {
            e.preventDefault();
            e.stopPropagation();   // no cerrar el modal que contiene el formulario
            cerrarSugerencias(form);
        }
    }, true);

    // mousedown (no click) para elegir antes de que el input pierda el foco
    document.addEventListener('mousedown', e => {
        const opcion = e.target.closest && e.target.closest('.pf-cliente-sugerencias .list-group-item');
        if (!opcion || !inputClienteActivo) return;
        e.preventDefault();
        const form = inputClienteActivo.closest('form');
        const estado = estadoBusqueda(inputClienteActivo);
        const cliente = estado.resultados[Number(opcion.dataset.indice)];
        if (cliente) vincularCliente(form, cliente);
    });

    document.addEventListener('focusout', e => {
        if (e.target.matches && e.target.matches('.pf-cliente-nombre')) {
            const form = e.target.closest('form');
            setTimeout(() => {
                if (document.activeElement !== e.target) cerrarSugerencias(form);
            }, 150);
        }
    });

    document.addEventListener('click', e => {
        const boton = e.target.closest && e.target.closest('.pf-cliente-desvincular');
        if (boton) desvincularCliente(boton.closest('form'));
    });

    // ---------- Lista de precios (hidden id_lista_precio) ----------
    let listasPrecio = null;   // promesa compartida por todos los formularios de la página

    function obtenerListasPrecio(url) {
        listasPrecio ??= fetch(url, { headers: { Accept: 'application/json' } })
            .then(resp => resp.ok ? resp.json() : null)
            .catch(() => null);
        return listasPrecio;
    }

    function mostrarEtiquetaLista(form, nombre, predeterminada) {
        const etiqueta = form.querySelector('.pf-lista-etiqueta');
        if (!etiqueta) return;
        const visible = !!(form.querySelector('[name="id_lista_precio"]').value && nombre && !predeterminada);
        etiqueta.textContent = visible ? `Precios ${nombre}` : '';
        etiqueta.classList.toggle('d-none', !visible);
    }

    function fijarListaPrecios(form, idLista, nombre = null, predeterminada = false) {
        const hidden = form.querySelector('[name="id_lista_precio"]');
        if (!hidden) return;
        const valor = idLista === null || idLista === undefined ? '' : String(idLista);
        const cambio = hidden.value !== valor;
        hidden.value = valor;
        mostrarEtiquetaLista(form, nombre, predeterminada);
        if (cambio) recotizarFormulario(form);
    }

    // En edición solo llega el id: el nombre sale del catálogo de listas.
    function configurarListaPrecios(form) {
        const hidden = form.querySelector('[name="id_lista_precio"]');
        if (!hidden?.value || !hidden.dataset.urlListas) return;
        const idInicial = hidden.value;
        obtenerListasPrecio(hidden.dataset.urlListas).then(datos => {
            if (hidden.value !== idInicial) return;
            const lista = datos?.listas?.find(item => String(item.id_lista) === idInicial);
            if (lista) mostrarEtiquetaLista(form, lista.nombre, lista.predeterminada);
        });
    }

    function activarBuscadorFila(fila) {
        if (!puedeUsarProductos(fila) || !window.BuscadorProductos) return;
        const input = fila.querySelector('[name="descripcion[]"]');
        if (!input || input.getAttribute('data-buscador-activo') === '1') return;
        const form = fila.closest('form');
        window.BuscadorProductos.activar(input, {
            fila,
            campoCantidad: fila.querySelector('[name="cantidad[]"]'),
            campoPrecio: fila.querySelector('[name="precio_unitario[]"]'),
            obtenerLista: () => form.querySelector('[name="id_lista_precio"]')?.value || null,
            alCotizar: ({ campoPrecio }) => {
                calcularSubtotal(campoPrecio);
                form.dispatchEvent(new Event('input', { bubbles: true }));
            },
        });
    }

    function recotizarFormulario(form) {
        form.querySelectorAll('.tabla-productos-dinamica tbody tr').forEach(fila => {
            window.BuscadorProductos?.recotizar(fila);
        });
    }

    function normalizarPrecio(input) {
        const valor = input.value.trim();
        if (!valor) return;
        const precio = parseFloat(valor.replace(',', '.'));
        if (Number.isFinite(precio)) {
            input.value = precio.toFixed(2);
            calcularSubtotal(input);
        }
    }

    document.addEventListener('focusout', e => {
        if (e.target.matches && e.target.matches('.pf-form [name="precio_unitario[]"]')) normalizarPrecio(e.target);
    });
    document.addEventListener('submit', e => {
        const form = e.target;
        if (form.matches && form.matches('.pf-form form')) {
            form.querySelectorAll('[name="precio_unitario[]"]').forEach(normalizarPrecio);
        }
    }, true);

    // Exponer para los atributos onclick/oninput
    Object.assign(window, {
        agregarFila, eliminarFilaProducto, calcularSubtotal,
        fijarFechaEntrega, fijarHoraEntrega,
    });

    function configurarAutollenadoFiscal(form) {
        const root = form.closest('.pf-form');
        const urlBase = root?.dataset.urlFiscalClientes;
        if (!root || root.dataset.fiscalInicializado) return;
        root.dataset.fiscalInicializado = '1';

        const checkbox = form.querySelector('[name="requiere_factura"]');
        const idCliente = form.querySelector('[name="id_cliente"]');
        const campos = root.querySelector('.campos-factura');
        const resumen = campos?.querySelector('[data-fiscal-resumen]');
        const inputs = campos?.querySelector('[data-fiscal-inputs]');
        const botonEditarWrap = campos?.querySelector('[data-fiscal-editar-wrap]');
        const error = campos?.querySelector('[data-fiscal-error]');
        if (!checkbox || !idCliente || !campos || !resumen || !inputs) return;

        const mapeo = [
            ['rfc', 'rfc', 'rfc'],
            ['razon_social', 'razon_social', 'razon_social'],
            ['regimen', 'regimen_fiscal', 'regimen'],
            ['cp', 'cp_fiscal', 'cp'],
            ['uso_cfdi', 'uso_cfdi', 'uso_cfdi'],
            ['correo', 'correo_factura', 'correo'],
        ];
        let ultimoCliente = idCliente.value.trim();
        let solicitud = 0;

        function campoPorNombre(nombre) {
            return form.querySelector(`[name="${nombre}"]`);
        }

        function limpiarAutocompletados() {
            for (const [, nombre] of mapeo) {
                const campo = campoPorNombre(nombre);
                if (campo?.dataset.fiscalAuto === '1') {
                    campo.value = '';
                    delete campo.dataset.fiscalAuto;
                }
            }
            resumen.classList.add('d-none');
            botonEditarWrap?.classList.add('d-none');
            inputs.classList.remove('d-none');
        }

        function actualizarResumen() {
            for (const [, nombre, claveResumen] of mapeo) {
                const campo = campoPorNombre(nombre);
                const valor = campo instanceof HTMLSelectElement
                    ? campo.selectedOptions[0]?.textContent.trim()
                    : campo?.value.trim();
                const destino = resumen.querySelector(`[data-fiscal-resumen-campo="${claveResumen}"]`);
                if (destino) destino.textContent = valor || '—';
            }
        }

        async function consultarFiscal(id) {
            if (!urlBase || !checkbox.checked || !id) return;
            const idSolicitud = ++solicitud;
            if (error) {
                error.textContent = '';
                error.classList.add('d-none');
            }
            try {
                const url = urlBase.replace(/\/0\/fiscal$/, `/${encodeURIComponent(id)}/fiscal`);
                const respuesta = await fetch(url, {
                    headers: { 'X-Requested-With': 'XMLHttpRequest' },
                    cache: 'no-store',
                });
                const datos = await respuesta.json().catch(() => null);
                if (!respuesta.ok || !datos) {
                    throw new Error(datos?.mensaje || 'No se pudieron consultar los datos fiscales.');
                }
                if (idSolicitud !== solicitud || id !== idCliente.value.trim() || !checkbox.checked) return;
                const hayDatos = mapeo.some(([clave]) => String(datos[clave] ?? '').trim());
                if (!hayDatos) return;

                for (const [clave, nombre] of mapeo) {
                    const campo = campoPorNombre(nombre);
                    const valor = String(datos[clave] ?? '').trim();
                    if (!campo || !valor || campo.value.trim() || campo.dataset.fiscalTocado) continue;
                    if (campo instanceof HTMLSelectElement
                        && ![...campo.options].some(opcion => opcion.value === valor)) continue;
                    campo.value = valor;
                    campo.dataset.fiscalAuto = '1';
                }
                actualizarResumen();
                resumen.classList.remove('d-none');
                inputs.classList.add('d-none');
                botonEditarWrap?.classList.remove('d-none');
            } catch (consultaError) {
                if (idSolicitud !== solicitud) return;
                if (error) {
                    error.textContent = consultaError.message || 'No se pudieron consultar los datos fiscales.';
                    error.classList.remove('d-none');
                }
            }
        }

        function revisarCliente() {
            const id = idCliente.value.trim();
            if (id === ultimoCliente) return;
            limpiarAutocompletados();
            ultimoCliente = id;
            if (id && checkbox.checked) consultarFiscal(id);
            else solicitud++;
        }

        checkbox.addEventListener('change', () => {
            if (checkbox.checked && idCliente.value.trim()) {
                consultarFiscal(idCliente.value.trim());
            } else {
                solicitud++;
            }
        });
        form.addEventListener('input', evento => {
            const campo = evento.target.closest('.dato-factura');
            if (campo) {
                campo.dataset.fiscalTocado = '1';
                delete campo.dataset.fiscalAuto;
            }
            if (evento.target.matches('.pf-cliente-nombre')) setTimeout(revisarCliente, 0);
        });
        form.addEventListener('change', evento => {
            if (evento.target === idCliente) revisarCliente();
            if (evento.target.matches('.pf-cliente-nombre')) setTimeout(revisarCliente, 0);
            const campo = evento.target.closest('.dato-factura');
            if (campo) {
                campo.dataset.fiscalTocado = '1';
                delete campo.dataset.fiscalAuto;
            }
        });
        root.addEventListener('click', evento => {
            if (evento.target.closest('[data-fiscal-editar]')) {
                resumen.classList.add('d-none');
                botonEditarWrap?.classList.add('d-none');
                inputs.classList.remove('d-none');
            }
            if (evento.target.closest('.pf-cliente-buscador')) setTimeout(revisarCliente, 0);
        });
        root.addEventListener('keydown', evento => {
            if (evento.target.closest('.pf-cliente-buscador')) setTimeout(revisarCliente, 0);
        });

        if (checkbox.checked && ultimoCliente) consultarFiscal(ultimoCliente);
    }

    function inicializarFormulario(form) {
        if (form.dataset.pfInicializado === 'true' || !form.isConnected) return;
        form.dataset.pfInicializado = 'true';
        configurarAutollenadoFiscal(form);
        form.querySelector('.pf-cliente-nombre')?.setAttribute('data-buscador-activo', '1');
        configurarListaPrecios(form);
        form.querySelectorAll('.tabla-productos-dinamica tbody tr').forEach(activarBuscadorFila);
        configurarPago(form);
        configurarAsignarTodos(form);
        configurarVistaPrevia(form);
        calcularGranTotal(form);
    }

    // También sirve para formularios insertados después de cargar la página.
    window.inicializarFormularioPedido = function (form) {
        if (puedeUsarProductos(form)) window.cargarBuscadorProductos(() => inicializarFormulario(form));
        else inicializarFormulario(form);
    };

    // Libera lo que el formulario registró fuera de su propio DOM; llamar antes de quitarlo.
    window.destruirFormularioPedido = function (form) {
        limpiezasFormulario.get(form)?.forEach(limpiar => limpiar());
        limpiezasFormulario.delete(form);
    };

    function iniciarFormularios() {
        document.querySelectorAll('form').forEach(form => {
            if (form.querySelector('.tabla-productos-dinamica') && form.querySelector('.gran-total-display')) {
                window.inicializarFormularioPedido(form);
            }
        });
    }
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', iniciarFormularios);
    else iniciarFormularios();
})();
