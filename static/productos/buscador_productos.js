(function () {
    if (window.BuscadorProductos) return;

    const estados = new WeakMap();
    let listaCompartida = null;
    let estadoActivo = null;
    let estadoSolicitudBusqueda = null;
    let controladorBusqueda = null;
    let secuenciaBusqueda = 0;
    let eventosGlobalesInstalados = false;
    const cacheBusquedas = new Map();
    const CAMPOS_FORMA = { lineal: ['largo'], m2: ['ancho', 'alto'], rollo: ['ancho', 'alto'], tiempo: ['minutos'] };
    const NOMBRES_FORMA = {
        fijo: 'pieza', paquete: 'paquete', lineal: 'metro lineal', m2: 'm²',
        rollo: 'rollo', tiempo: 'tiempo', libre: 'precio libre',
    };

    function instalarEstilos() {
        if (document.getElementById('buscador-productos-estilos')) return;
        const style = document.createElement('style');
        style.id = 'buscador-productos-estilos';
        style.textContent = `
            .pf-producto-sugerencias { position: fixed; z-index: 1060; min-width: 280px; max-height: 280px; overflow-y: auto; border-radius: 16px; }
            .pf-producto-pastilla { margin-top: 4px !important; }
            .pf-producto-pastilla .badge { max-width: 100%; padding: 2px 8px; background-color: #E8F5E9 !important; color: #1b5e20 !important; font-size: 12px; font-weight: 500; }
            .pf-producto-pastilla .pf-producto-desligar { color: #1b5e20 !important; }
            .pf-producto-produccion { color: #6c757d; font-size: .75rem; }
            .pf-producto-medidas { display: flex; flex-wrap: nowrap; align-items: center; gap: .25rem; white-space: nowrap; font-size: .8rem; }
            .pf-producto-medidas .form-control { width: 64px; min-width: 64px; padding: .15rem .5rem; font-size: .8rem; }
            .pf-producto-avisos { margin-top: .25rem; }
            .pf-producto-sugerencias .list-group-item.active { color: inherit; background: #e8f0ff; border-color: #e8f0ff; }
        `;
        document.head.appendChild(style);
    }

    function obtenerListaCompartida() {
        if (!listaCompartida) {
            listaCompartida = document.createElement('div');
            listaCompartida.className = 'list-group shadow pf-producto-sugerencias d-none';
            listaCompartida.setAttribute('role', 'listbox');
            listaCompartida.id = 'pf-producto-sugerencias';
        }
        return listaCompartida;
    }

    function inputEnVista(estado) {
        const rect = estado.input.getBoundingClientRect();
        const enViewport = estado.input.isConnected && estado.input.getClientRects().length > 0
            && rect.width > 0 && rect.height > 0
            && rect.bottom > 0 && rect.top < window.innerHeight
            && rect.right > 0 && rect.left < window.innerWidth;
        if (!enViewport) return false;
        for (let elemento = estado.input.parentElement; elemento; elemento = elemento.parentElement) {
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

    function posicionar(estado) {
        const lista = estado.lista;
        if (!lista || lista.classList.contains('d-none') || estadoActivo !== estado) return;
        if (!inputEnVista(estado)) { cerrar(estado); return; }
        const modal = estado.input.closest('.modal.show');
        const host = modal || document.body;
        if (lista.parentElement !== host) host.appendChild(lista);
        const caja = estado.input.getBoundingClientRect();
        lista.style.left = `${caja.left}px`;
        lista.style.top = `${caja.bottom + 4}px`;
        lista.style.width = `${caja.width}px`;
    }

    function instalarEventosGlobales() {
        if (eventosGlobalesInstalados) return;
        eventosGlobalesInstalados = true;
        const lista = obtenerListaCompartida();
        const actualizar = () => {
            if (estadoActivo) posicionar(estadoActivo);
        };
        document.addEventListener('scroll', actualizar, true);
        window.addEventListener('resize', actualizar);
        lista.addEventListener('mousedown', event => {
            const boton = event.target.closest?.('.list-group-item');
            if (!boton || !estadoActivo) return;
            event.preventDefault();
            seleccionar(estadoActivo, estadoActivo.resultados[Number(boton.dataset.indice)]);
        });
        document.addEventListener('click', event => {
            if (!estadoActivo) return;
            if (!estadoActivo.input.contains(event.target) && !estadoActivo.lista.contains(event.target)) {
                cerrar(estadoActivo);
            }
        });
        document.addEventListener('hidden.bs.modal', event => {
            if (estadoSolicitudBusqueda?.input.closest('.modal') === event.target) cerrar(estadoSolicitudBusqueda);
            if (estadoActivo?.input.closest('.modal') === event.target) cerrar(estadoActivo);
        });
    }

    function escaparHtml(texto) {
        return String(texto ?? '').replace(/[&<>"']/g,
            caracter => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[caracter]));
    }

    function leerConfiguracion(fila) {
        try {
            const campo = fila.querySelector('[name="configuracion[]"]');
            const valor = JSON.parse(campo?.value || '{}');
            return valor && typeof valor === 'object' && !Array.isArray(valor) ? valor : {};
        } catch {
            return {};
        }
    }

    function escribirConfiguracion(estado, configuracion) {
        estado.configuracion = configuracion;
        const campo = estado.fila.querySelector('[name="configuracion[]"]');
        if (campo) campo.value = JSON.stringify(configuracion);
    }

    function crearExtras(input) {
        const celda = input.closest('td') || input.parentElement;
        if (!celda.querySelector('.pf-producto-pastilla')) {
            celda.insertAdjacentHTML('beforeend', `<div class="mt-1 pf-producto-pastilla d-none">
                    <span class="badge rounded-pill d-inline-flex align-items-center gap-2">
                        <span class="text-truncate pf-producto-pastilla-texto"></span>
                        <button type="button" class="btn btn-link p-0 text-decoration-none lh-1 pf-producto-desligar" title="Quitar del catálogo" aria-label="Quitar del catálogo">✕</button>
                    </span>
                </div>
                <div class="small text-muted mt-1 pf-producto-produccion d-none"></div>
                <div class="mt-1 pf-producto-medidas d-none"></div>
                <div class="small mt-1 pf-producto-avisos d-none"></div>`);
        }
        return celda;
    }

    function ligada(estado) {
        const idProducto = estado.fila.querySelector('[name="producto_id[]"]')?.value;
        const idPresentacion = estado.fila.querySelector('[name="presentacion_id[]"]')?.value;
        return !!((idProducto && idPresentacion) || estado.presentacion?.id_presentacion);
    }

    function cerrar(estado) {
        clearTimeout(estado.temporizador);
        estado.resultados = [];
        estado.activo = -1;
        estado.input.setAttribute('aria-expanded', 'false');
        if (estadoActivo === estado || estadoSolicitudBusqueda === estado) {
            controladorBusqueda?.abort();
            controladorBusqueda = null;
            secuenciaBusqueda++;
            if (estadoSolicitudBusqueda === estado) estadoSolicitudBusqueda = null;
            if (estadoActivo === estado) {
                estado.lista.innerHTML = '';
                estado.lista.classList.add('d-none');
                estadoActivo = null;
            }
        }
    }

    function avisosResultado(resultado) {
        return [
            ...(Array.isArray(resultado?.avisos) ? resultado.avisos : []),
            ...(resultado?.aplico_minimo ? ['Se aplicó el mínimo de cobro'] : []),
        ];
    }

    function mostrarAvisos(estado, avisos, error = '') {
        const caja = estado.avisos;
        const textos = error ? [error] : avisos;
        caja.textContent = textos.join(' · ');
        caja.classList.toggle('text-danger', !!error);
        caja.classList.toggle('text-muted', !error);
        caja.classList.toggle('d-none', !textos.length);
    }

    function mostrarProduccion(estado, detalle) {
        estado.produccion.textContent = detalle || '';
        estado.produccion.classList.toggle('d-none', !detalle);
    }

    function htmlMedidas(forma, configuracion) {
        const campo = (medida, etiqueta) => `<input type="number" step="any" min="0" inputmode="decimal"
            class="form-control form-control-sm pf-medida" data-medida="${medida}" aria-label="${etiqueta}"
            value="${escaparHtml(configuracion[medida] ?? '')}">`;
        if (forma === 'lineal') {
            return `<span class="text-muted">Largo</span>${campo('largo', 'Largo en metros')}<span class="text-muted">m</span>`;
        }
        if (forma === 'm2' || forma === 'rollo') {
            return `<span class="text-muted">Ancho × Alto</span>${campo('ancho', 'Ancho en metros')}<span>×</span>`
                + `${campo('alto', 'Alto en metros')}<span class="text-muted">m</span>`;
        }
        if (forma === 'tiempo') return `<span class="text-muted">Minutos</span>${campo('minutos', 'Minutos')}`;
        return '';
    }

    function pintar(estado, configuracion) {
        const idProducto = estado.fila.querySelector('[name="producto_id[]"]')?.value
            || estado.producto?.id_producto;
        const clave = configuracion.producto_clave || `P-${String(idProducto || '').padStart(4, '0')}`;
        estado.pastilla.querySelector('.pf-producto-pastilla-texto').textContent =
            `${clave} · ${configuracion.presentacion_nombre || 'Catálogo'}`;
        estado.pastilla.classList.remove('d-none');
        mostrarProduccion(estado, configuracion.detalle_produccion);
        estado.medidas.innerHTML = htmlMedidas(configuracion.forma, configuracion);
        estado.medidas.classList.toggle('d-none', !estado.medidas.innerHTML);
        mostrarAvisos(estado, avisosResultado(configuracion.resultado));
    }

    function leerMedidas(estado) {
        const medidas = { ancho: null, alto: null, largo: null, minutos: null };
        estado.medidas.querySelectorAll('.pf-medida').forEach(campo => {
            const valor = parseFloat(campo.value);
            medidas[campo.dataset.medida] = valor > 0 ? valor : null;
        });
        return medidas;
    }

    function leerPrecio(campo) {
        return parseFloat(String(campo.value).replace(',', '.')) || 0;
    }

    function marcarManual(estado) {
        estado.precioManual = true;
        clearTimeout(estado.temporizador);
        estado.consulta++;
    }

    function renderSugerencias(estado, productos) {
        const opciones = productos.flatMap(producto =>
            (producto.presentaciones || []).map(presentacion => ({ producto, presentacion })));
        if (!opciones.length) { cerrar(estado); return; }
        estado.resultados = opciones;
        estado.activo = -1;
        const modal = estado.input.closest('.modal.show');
        (modal || document.body).appendChild(estado.lista);
        estado.lista.innerHTML = opciones.map(({ producto, presentacion }, indice) => `
            <button type="button" class="list-group-item list-group-item-action py-2" role="option"
                    aria-selected="false" data-indice="${indice}" tabindex="-1">
                <span class="fw-semibold">${escaparHtml(producto.nombre)} · ${escaparHtml(presentacion.nombre)}</span>
                <span class="small text-muted ms-1">${escaparHtml(NOMBRES_FORMA[presentacion.forma] || presentacion.forma)}</span>
            </button>`).join('');
        estadoActivo = estado;
        estado.lista.classList.remove('d-none');
        estado.input.setAttribute('aria-expanded', 'true');
        posicionar(estado);
    }

    async function buscar(estado, consulta) {
        controladorBusqueda?.abort();
        const controlador = new AbortController();
        controladorBusqueda = controlador;
        const numero = ++secuenciaBusqueda;
        const claveCache = `${estado.urlBuscar}|${consulta.toLocaleLowerCase()}`;
        const cacheado = cacheBusquedas.get(claveCache);
        if (cacheado && Date.now() - cacheado.timestamp < 60000) {
            if (numero === secuenciaBusqueda && document.activeElement === estado.input) {
                renderSugerencias(estado, cacheado.productos);
            }
            if (estadoSolicitudBusqueda === estado) estadoSolicitudBusqueda = null;
            return;
        }
        if (cacheado) cacheBusquedas.delete(claveCache);
        try {
            const respuesta = await fetch(`${estado.urlBuscar}?q=${encodeURIComponent(consulta)}`,
                { headers: { 'X-Requested-With': 'XMLHttpRequest' }, signal: controlador.signal });
            const datos = respuesta.ok ? await respuesta.json() : [];
            const productos = Array.isArray(datos) ? datos : [];
            if (respuesta.ok) {
                const ahora = Date.now();
                cacheBusquedas.forEach((valor, clave) => {
                    if (ahora - valor.timestamp >= 60000) cacheBusquedas.delete(clave);
                });
                cacheBusquedas.set(claveCache, { timestamp: ahora, productos });
            }
            if (numero !== secuenciaBusqueda || document.activeElement !== estado.input) return;
            renderSugerencias(estado, productos);
        } catch (error) {
            if (error.name !== 'AbortError' && numero === secuenciaBusqueda) cerrar(estado);
        } finally {
            if (estadoSolicitudBusqueda === estado && numero === secuenciaBusqueda) {
                estadoSolicitudBusqueda = null;
            }
        }
    }

    function seleccionar(estado, opcion) {
        const { producto, presentacion } = opcion;
        clearTimeout(estado.temporizador);
        estado.consulta++;
        estado.precioManual = false;
        estado.producto = producto;
        estado.presentacion = presentacion;
        const lista = estado.obtenerLista?.() || null;
        const configuracion = {
            id_lista: lista,
            forma: presentacion.forma,
            cantidad: parseFloat(estado.campoCantidad.value) || null,
            ancho: null, alto: null, largo: null, minutos: null,
            producto_clave: producto.clave,
            presentacion_nombre: presentacion.nombre,
        };
        const idProducto = estado.fila.querySelector('[name="producto_id[]"]');
        const idPresentacion = estado.fila.querySelector('[name="presentacion_id[]"]');
        if (idProducto) idProducto.value = producto.id_producto;
        if (idPresentacion) idPresentacion.value = presentacion.id_presentacion;
        escribirConfiguracion(estado, configuracion);
        estado.input.value = `${producto.nombre} · ${presentacion.nombre}`;
        cerrar(estado);
        pintar(estado, configuracion);
        estado.fila.closest('form')?.dispatchEvent(new Event('input', { bubbles: true }));
        const primeraMedida = estado.medidas.querySelector('.pf-medida');
        if (primeraMedida) primeraMedida.focus();
        else cotizar(estado);
    }

    async function cotizar(estado) {
        const presentacionId = estado.fila.querySelector('[name="presentacion_id[]"]')?.value
            || estado.presentacion?.id_presentacion;
        if (!estado.urlCotizar || !presentacionId || estado.precioManual) return;
        clearTimeout(estado.temporizador);
        const consulta = ++estado.consulta;
        const base = leerConfiguracion(estado.fila);
        const forma = base.forma || estado.presentacion?.forma;
        const cantidad = parseFloat(estado.campoCantidad.value);
        const medidas = leerMedidas(estado);
        const faltanMedidas = (CAMPOS_FORMA[forma] || []).some(campo => !medidas[campo]);
        if (!(cantidad > 0) || faltanMedidas) {
            mostrarAvisos(estado, []);
            return;
        }
        const cuerpo = {
            id_presentacion: Number(presentacionId), cantidad, ...medidas,
            id_lista: estado.obtenerLista?.() || null,
            id_cliente: estado.fila.closest('form')?.querySelector('[name="id_cliente"]')?.value || null,
        };
        if (forma === 'libre') cuerpo.precio_libre = leerPrecio(estado.campoPrecio);
        let datos;
        try {
            const respuesta = await fetch(estado.urlCotizar, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json', Accept: 'application/json',
                    'X-Requested-With': 'XMLHttpRequest',
                },
                body: JSON.stringify(cuerpo),
            });
            datos = await respuesta.json().catch(() => ({}));
            if (!respuesta.ok) throw new Error(datos.error || 'No se pudo cotizar.');
        } catch (error) {
            if (consulta === estado.consulta) {
                mostrarAvisos(estado, [], error instanceof TypeError ? 'No se pudo cotizar.' : error.message);
            }
            return;
        }
        if (consulta !== estado.consulta || !ligada(estado) || estado.precioManual) return;

        const resultado = {
            precio_unitario: datos.precio_unitario,
            subtotal: datos.subtotal,
            medida_por_pieza: datos.medida_por_pieza,
            medida_total: datos.medida_total,
            medida_cobrada: datos.medida_cobrada,
            precio_medida: datos.precio_medida,
            detalle_produccion: datos.detalle_produccion,
            unidad_medida: datos.unidad_medida,
            tramos: datos.tramos,
            ancho_tramo: datos.ancho_tramo,
            girada: datos.girada,
            ancho_rollo: datos.rollo?.ancho_rollo ?? null,
            descripcion_medida: datos.descripcion_medida,
            avisos: datos.avisos || [],
            aplico_minimo: !!datos.aplico_minimo,
        };
        const configuracion = {
            ...base,
            id_lista: datos.id_lista ?? cuerpo.id_lista,
            forma, cantidad,
            ancho: medidas.ancho, alto: medidas.alto, largo: medidas.largo, minutos: medidas.minutos,
            resultado,
        };
        escribirConfiguracion(estado, configuracion);
        if (datos.descripcion) estado.input.value = datos.descripcion;
        estado.campoPrecio.value = (Number(datos.precio_unitario) || 0).toFixed(2);
        mostrarAvisos(estado, avisosResultado(resultado));
        estado.alCotizar?.({ datos, fila: estado.fila, inputDescripcion: estado.input,
            campoCantidad: estado.campoCantidad, campoPrecio: estado.campoPrecio, configuracion, resultado });
    }

    function programarCotizacion(estado, espera = 300) {
        clearTimeout(estado.temporizador);
        estado.temporizador = setTimeout(() => cotizar(estado), espera);
    }

    function activar(inputDescripcion, opciones) {
        if (inputDescripcion.getAttribute('data-buscador-activo') === '1') return;
        inputDescripcion.setAttribute('data-buscador-activo', '1');
        instalarEstilos();
        instalarEventosGlobales();
        const fila = opciones.fila;
        const celda = crearExtras(inputDescripcion);
        const estado = {
            input: inputDescripcion,
            fila,
            campoCantidad: opciones.campoCantidad,
            campoPrecio: opciones.campoPrecio,
            obtenerLista: opciones.obtenerLista,
            alCotizar: opciones.alCotizar,
            lista: obtenerListaCompartida(),
            pastilla: celda.querySelector('.pf-producto-pastilla'),
            produccion: celda.querySelector('.pf-producto-produccion'),
            medidas: celda.querySelector('.pf-producto-medidas'),
            avisos: celda.querySelector('.pf-producto-avisos'),
            urlBuscar: inputDescripcion.dataset.urlBuscar
                || fila.closest('.tabla-productos-dinamica')?.dataset.urlBuscar
                || fila.closest('form')?.dataset.urlBuscar,
            urlCotizar: inputDescripcion.dataset.urlCotizar
                || fila.closest('.tabla-productos-dinamica')?.dataset.urlCotizar
                || fila.closest('form')?.dataset.urlCotizar,
            consulta: 0, temporizador: null, resultados: [], activo: -1, precioManual: false,
            configuracion: leerConfiguracion(fila),
        };
        if (estados.has(inputDescripcion)) return;
        estados.set(inputDescripcion, estado);
        inputDescripcion.classList.add('pf-producto-descripcion');
        inputDescripcion.setAttribute('autocomplete', 'off');
        inputDescripcion.setAttribute('role', 'combobox');
        inputDescripcion.setAttribute('aria-autocomplete', 'list');
        inputDescripcion.setAttribute('aria-expanded', 'false');

        const idProducto = fila.querySelector('[name="producto_id[]"]')?.value;
        const idPresentacion = fila.querySelector('[name="presentacion_id[]"]')?.value;
        if (idProducto && idPresentacion) {
            estado.producto = { id_producto: idProducto, clave: estado.configuracion.producto_clave };
            estado.presentacion = { id_presentacion: idPresentacion, forma: estado.configuracion.forma };
            pintar(estado, estado.configuracion);
            const cotizado = estado.configuracion.resultado?.precio_unitario;
            if (cotizado !== undefined && cotizado !== null
                && Math.abs(leerPrecio(estado.campoPrecio) - Number(cotizado)) > 0.005) {
                estado.precioManual = true;
            }
        }

        inputDescripcion.addEventListener('input', () => {
            clearTimeout(estado.temporizador);
            if (estadoSolicitudBusqueda) {
                clearTimeout(estadoSolicitudBusqueda.temporizador);
                estadoSolicitudBusqueda = null;
            }
            controladorBusqueda?.abort();
            controladorBusqueda = null;
            secuenciaBusqueda++;
            if (estadoActivo) cerrar(estadoActivo);
            const consulta = inputDescripcion.value.trim();
            if (!estado.urlBuscar || ligada(estado) || consulta.length < 2) {
                estado.consulta++;
                cerrar(estado);
                return;
            }
            estadoSolicitudBusqueda = estado;
            estado.temporizador = setTimeout(() => buscar(estado, consulta), 250);
        });
        fila.addEventListener('keydown', event => {
            if (event.target.matches?.('.pf-medida')) {
                if (event.key === 'Enter') { event.preventDefault(); cotizar(estado); }
                return;
            }
            if (event.target !== inputDescripcion || estado.lista.classList.contains('d-none')) return;
            const total = estado.resultados.length;
            if (!total) return;
            if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
                event.preventDefault();
                estado.activo = (estado.activo + (event.key === 'ArrowDown' ? 1 : -1) + total) % total;
                estado.lista.querySelectorAll('.list-group-item').forEach((item, indice) => {
                    item.classList.toggle('active', indice === estado.activo);
                    item.setAttribute('aria-selected', indice === estado.activo ? 'true' : 'false');
                    if (indice === estado.activo) item.scrollIntoView({ block: 'nearest' });
                });
            } else if (event.key === 'Enter') {
                event.preventDefault();
                seleccionar(estado, estado.resultados[Math.max(estado.activo, 0)]);
            } else if (event.key === 'Escape') {
                event.preventDefault(); event.stopPropagation(); cerrar(estado);
            }
        }, true);
        fila.addEventListener('input', event => {
            if (event.target.matches('.pf-medida')) {
                if (ligada(estado)) programarCotizacion(estado);
            } else if (event.target === estado.campoCantidad) {
                if (ligada(estado)) programarCotizacion(estado);
            } else if (event.target === estado.campoPrecio && event.isTrusted && ligada(estado)) {
                marcarManual(estado);
            }
        });
        celda.addEventListener('click', event => {
            if (event.target.closest?.('.pf-producto-desligar')) {
                clearTimeout(estado.temporizador);
                estado.consulta++;
                estado.producto = null;
                estado.presentacion = null;
                estado.precioManual = false;
                ['producto_id[]', 'presentacion_id[]', 'configuracion[]'].forEach(nombre => {
                    const campo = fila.querySelector(`[name="${nombre}"]`);
                    if (campo) campo.value = '';
                });
                estado.configuracion = {};
                estado.medidas.innerHTML = '';
                estado.medidas.classList.add('d-none');
                estado.pastilla.classList.add('d-none');
                mostrarProduccion(estado, '');
                mostrarAvisos(estado, []);
                fila.closest('form')?.dispatchEvent(new Event('input', { bubbles: true }));
            }
        });
        inputDescripcion.addEventListener('focusout', () => setTimeout(() => cerrar(estado), 150));
        estado.campoPrecio.addEventListener('blur', () => {
            const valor = estado.campoPrecio.value.trim();
            const precio = parseFloat(valor.replace(',', '.'));
            if (valor && Number.isFinite(precio)) estado.campoPrecio.value = precio.toFixed(2);
        });
        fila.closest('form')?.addEventListener('reset', () => setTimeout(() => {
            estado.configuracion = leerConfiguracion(fila);
            const productoId = fila.querySelector('[name="producto_id[]"]')?.value;
            const presentacionId = fila.querySelector('[name="presentacion_id[]"]')?.value;
            estado.producto = productoId ? { id_producto: productoId, clave: estado.configuracion.producto_clave } : null;
            estado.presentacion = presentacionId
                ? { id_presentacion: presentacionId, forma: estado.configuracion.forma } : null;
            estado.precioManual = false;
            cerrar(estado);
            if (estado.presentacion) pintar(estado, estado.configuracion);
            else {
                estado.medidas.innerHTML = '';
                estado.medidas.classList.add('d-none');
                estado.pastilla.classList.add('d-none');
                mostrarProduccion(estado, '');
                mostrarAvisos(estado, []);
            }
            estado.consulta++;
        }, 0));
    }

    function recotizar(fila) {
        const input = fila.querySelector('.pf-producto-descripcion');
        const estado = input && estados.get(input);
        if (estado && ligada(estado) && !estado.precioManual) cotizar(estado);
    }

    window.BuscadorProductos = { activar, recotizar };
})();
