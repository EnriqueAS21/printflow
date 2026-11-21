(() => {
    const form = document.getElementById('formProducto');
    if (!form) return;

    const listas = JSON.parse(form.dataset.listas || '[]');
    const inicial = JSON.parse(form.dataset.inicial || '{}');
    const contenedor = document.getElementById('presentaciones');
    const errorEl = document.getElementById('errorPresentaciones');
    const formas = [
        ['fijo', 'Fijo'], ['paquete', 'Paquete'], ['lineal', 'Metro lineal'],
        ['m2', 'm²'], ['rollo', 'Rollo'], ['tiempo', 'Tiempo'], ['libre', 'Libre'],
    ];
    const escapar = valor => String(valor ?? '').replace(/[&<>"']/g,
        caracter => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[caracter]));
    const numero = valor => valor === '' || valor == null ? null : Number(valor);
    let contadorPrueba = 0;

    function enfocarNombre(tarjeta) {
        tarjeta.scrollIntoView({ behavior: 'smooth', block: 'center' });
        const nombre = tarjeta.querySelector('.nombre-presentacion');
        nombre.focus({ preventScroll: true });
        nombre.select();
    }

    function rangosPresentacion(presentacion) {
        if (Array.isArray(presentacion.rangos) && presentacion.rangos.length) {
            return presentacion.rangos.map(rango => ({
                desde: rango.desde,
                precios: rango.precios || {},
            }));
        }
        const primero = { desde: 1, precios: {} };
        const porDesde = new Map();
        const rangos = [primero];
        (presentacion.precios || []).forEach(precio => {
            const cantidad = Number(precio.cantidad_minima);
            if (cantidad <= 0) {
                primero.precios[String(precio.id_lista)] = precio.precio;
                return;
            }
            const desde = cantidad;
            if (!porDesde.has(desde)) {
                const rango = { desde, precios: {} };
                porDesde.set(desde, rango);
                rangos.push(rango);
            }
            porDesde.get(desde).precios[String(precio.id_lista)] = precio.precio;
        });
       return rangos.sort((a, b) => Number(a.desde) - Number(b.desde));
    }

    function unidadPrecio(forma) {
        return ({
            fijo: '$ por pieza', paquete: '$ por paquete', lineal: '$ por metro',
            m2: '$ por m²', rollo: '$ por m²', tiempo: '$ por minuto', libre: 'Precio libre',
        })[forma] || '$ por unidad';
    }

    function unidadResumen(forma) {
        return ({
            fijo: '/pieza', paquete: '/paquete', lineal: '/m', m2: '/m²',
            rollo: '/m²', tiempo: '/min', libre: '/unidad',
        })[forma] || '/unidad';
    }

    function actualizarResumen(tarjeta) {
        const forma = tarjeta.querySelector('.forma-presentacion').value;
        const filas = [...tarjeta.querySelectorAll('.fila-rango')];
        const rangos = filas.length;
        const resumen = listas.map(lista => {
            const precio = filas[0]?.querySelector(`.rango-precio[data-lista="${CSS.escape(String(lista.id_lista))}"]`)?.value;
            if (precio === '' || precio == null || !Number.isFinite(Number(precio))) return null;
            const monto = `$${Number(precio).toLocaleString('es-MX', { maximumFractionDigits: 2 })}`;
            return rangos > 1
                ? `${lista.nombre} desde ${monto} · ${rangos} rangos`
                : `${lista.nombre} ${monto}${unidadResumen(forma)}`;
        }).filter(Boolean);
        tarjeta.querySelector('.presentacion-precio-resumen').textContent = resumen.join(' · ') || 'Sin precios configurados';
        const opcion = formas.find(([valor]) => valor === forma);
        tarjeta.querySelector('.presentacion-forma-resumen').textContent = opcion?.[1] || forma;
    }

    function establecerCompacta(tarjeta, compacta) {
        tarjeta.querySelector('.presentacion-edicion').classList.toggle('d-none', compacta);
        tarjeta.classList.toggle('presentacion-compacta', compacta);
        const botonEdicion = tarjeta.querySelector('.alternar-edicion');
        botonEdicion.textContent = compacta ? 'Editar' : 'Listo';
        botonEdicion.classList.toggle('btn-outline-primary', compacta);
        botonEdicion.classList.toggle('btn-primary', !compacta);
    }

    function abrirPresentacion(tarjeta, enfocar = false) {
        contenedor.querySelectorAll('.presentacion-card').forEach(otra => establecerCompacta(otra, otra !== tarjeta));
        if (enfocar) enfocarNombre(tarjeta);
    }

    function compactarPresentacion(tarjeta) {
        establecerCompacta(tarjeta, true);
    }

    function filaRango(rango = {}, indice = 0, forma = 'fijo') {
        const decimal = ['lineal', 'm2', 'rollo'].includes(forma);
        const desde = indice === 0 ? 1 : (rango.desde ?? '');
        return `<tr class="fila-rango">
            <td>${indice === 0
                ? '<span class="rango-desde-fijo">1</span>'
                : `<input class="form-control rango-desde" type="number" min="1" step="${decimal ? 'any' : '1'}" required value="${escapar(desde)}" aria-label="Desde">`}</td>
            <td class="rango-hasta text-nowrap">${indice === 0 ? 'en adelante' : '—'}</td>
            ${listas.map(lista => {
                const esMaquila = String(lista.nombre).toLowerCase().includes('maquila');
                return `<td><input class="form-control rango-precio" type="number" min="0" step="0.01" ${esMaquila ? 'placeholder="= Público"' : ''} aria-label="Precio ${escapar(lista.nombre)} desde ${escapar(desde)}" data-lista="${escapar(lista.id_lista)}" value="${escapar(rango.precios?.[String(lista.id_lista)] ?? '')}"></td>`;
            }).join('')}
            <td class="celda-quitar-rango">${indice === 0 ? '' : '<button type="button" class="btn btn-sm btn-outline-danger quitar-rango" aria-label="Quitar rango">×</button>'}</td>
        </tr>`;
    }

    function armarTarjeta(presentacion = {}) {
        const tarjeta = document.createElement('section');
        const pruebaId = `prueba-precio-${++contadorPrueba}`;
        tarjeta.className = 'card border-0 shadow-sm rounded-4 presentacion-card';
        tarjeta.dataset.idPresentacion = presentacion.id_presentacion || '';
        tarjeta.innerHTML = `
            <div class="card-body">
                <div class="presentacion-resumen-linea" role="button" tabindex="0" aria-label="Editar presentación">
                    <div class="presentacion-resumen-texto">
                        <h6 class="presentacion-titulo-compacto">${escapar(String(presentacion.nombre || '').trim() || 'Nueva presentación')}</h6>
                        <span class="badge bg-light text-secondary presentacion-forma-resumen"></span>
                    </div>
                    <span class="presentacion-precio-resumen"></span>
                    <div class="presentacion-acciones-compactas">
                        <button type="button" class="btn btn-sm btn-light mover-arriba" title="Antes" aria-label="Antes"><i class="bi bi-arrow-up" aria-hidden="true"></i></button>
                        <button type="button" class="btn btn-sm btn-light mover-abajo" title="Después" aria-label="Después"><i class="bi bi-arrow-down" aria-hidden="true"></i></button>
                        <button type="button" class="btn btn-sm btn-light duplicar-presentacion" title="Duplicar" aria-label="Duplicar"><i class="bi bi-files" aria-hidden="true"></i></button>
                        <button type="button" class="btn btn-sm btn-light quitar-presentacion text-danger" title="Quitar" aria-label="Quitar"><i class="bi bi-trash" aria-hidden="true"></i></button>
                        <button type="button" class="btn btn-sm btn-outline-primary alternar-edicion">Editar</button>
                    </div>
                </div>
                <div class="presentacion-edicion d-none">
                <div class="row g-2">
                    <div class="col-6">
                        <label class="form-label">Nombre *</label>
                        <input class="form-control nombre-presentacion" type="text" maxlength="80" required value="${escapar(presentacion.nombre)}">
                    </div>
                    <div class="col-6">
                        <label class="form-label">Forma *</label>
                        <select class="form-select forma-presentacion" required>
                            ${formas.map(([valor, etiqueta]) => `<option value="${valor}" ${presentacion.forma === valor ? 'selected' : ''}>${etiqueta}</option>`).join('')}
                        </select>
                    </div>
                    <div class="col-md-4 campo-paquete d-none">
                        <label class="form-label">Piezas por paquete</label>
                        <input class="form-control piezas-paquete" type="number" min="1" step="1" value="${escapar(presentacion.piezas_paquete)}">
                    </div>
                    <div class="col-md-4 campo-rollo d-none">
                        <label class="form-label">Ancho del rollo (m)</label>
                        <input class="form-control ancho-rollo" type="number" min="0.001" step="0.001" value="${escapar(presentacion.ancho_rollo)}">
                    </div>
                    <div class="col-md-4 campo-rollo d-none">
                        <label class="form-label">Margen del rollo (m)</label>
                        <input class="form-control margen-rollo" type="number" min="0" step="0.001" value="${escapar(presentacion.margen_rollo ?? 0.05)}">
                    </div>
                    <div class="col-md-4 campo-rollo d-none">
                        <label class="form-label">Cargo por unión ($)</label>
                        <input class="form-control cargo-union" type="number" min="0" step="0.01" value="${escapar(presentacion.cargo_union ?? 0)}">
                    </div>
                    <div class="col-md-4 campo-tiempo d-none">
                        <label class="form-label">Minutos mínimos</label>
                        <input class="form-control minutos-minimos" type="number" min="0" step="0.01" value="${escapar(presentacion.minutos_minimos)}">
                    </div>
                    <div class="col-md-4 campo-minimo-cobro">
                        <label class="form-label">Mínimo de cobro ($)</label>
                        <input class="form-control minimo-cobro" type="number" min="0" step="0.01" value="${escapar(presentacion.minimo_cobro)}">
                        <small class="form-text text-muted">Si el total del renglón queda abajo de este monto, se cobra el mínimo. Déjalo vacío si no aplica.</small>
                    </div>
                </div>
                <div class="text-muted subtitulo-rangos">Precios por rango</div>
                <div class="table-responsive">
                    <table class="table table-bordered table-sm align-middle mb-0 tabla-rangos">
                        <thead><tr><th>Desde</th><th>Hasta</th>${listas.map(lista => `<th>${escapar(lista.nombre)}${lista.predeterminada ? ' *' : ''}<small class="unidad-columna-precio d-block">${escapar(unidadPrecio(presentacion.forma || 'fijo'))}</small></th>`).join('')}<th></th></tr></thead>
                        <tbody class="filas-rangos">${rangosPresentacion(presentacion).map((rango, indice) => filaRango(rango, indice, presentacion.forma)).join('')}</tbody>
                    </table>
                </div>
                <div class="d-flex justify-content-between align-items-center">
                    <button type="button" class="btn btn-outline-secondary agregar-rango">+ Agregar rango</button>
                    <button type="button" class="btn btn-link p-0 text-decoration-none alternar-prueba" style="font-size: .8rem" aria-expanded="false" aria-controls="${pruebaId}">Probar precio ▸</button>
                </div>
                <div class="prueba-precio">
                    <div class="border rounded-3 p-3 mt-2 prueba-contenido d-none" id="${pruebaId}">
                    <div class="row g-2 align-items-end">
                        <div class="col-6 col-md-2">
                            <label class="form-label">Cantidad</label>
                            <input class="form-control prueba-cantidad" type="number" min="0.01" step="0.01" value="1">
                        </div>
                        <div class="col-6 col-md-3">
                            <label class="form-label">Lista de precios</label>
                            <select class="form-select prueba-lista">
                                ${listas.map((lista, indice) => `<option value="${escapar(lista.id_lista)}" ${indice === 0 ? 'selected' : ''}>${escapar(lista.nombre)}</option>`).join('')}
                            </select>
                        </div>
                        <div class="col-6 col-md-2 prueba-largo d-none">
                            <label class="form-label">Largo (m)</label>
                            <input class="form-control prueba-medida-largo" type="number" min="0.001" step="0.001">
                        </div>
                        <div class="col-6 col-md-2 prueba-dimensiones d-none">
                            <label class="form-label">Ancho (m)</label>
                            <input class="form-control prueba-medida-ancho" type="number" min="0.001" step="0.001">
                        </div>
                        <div class="col-6 col-md-2 prueba-dimensiones d-none">
                            <label class="form-label">Alto (m)</label>
                            <input class="form-control prueba-medida-alto" type="number" min="0.001" step="0.001">
                        </div>
                        <div class="col-6 col-md-2 prueba-minutos d-none">
                            <label class="form-label">Minutos</label>
                            <input class="form-control prueba-medida-minutos" type="number" min="0.01" step="0.01">
                        </div>
                        <div class="col-6 col-md-2 prueba-libre d-none">
                            <label class="form-label">Precio unitario ($)</label>
                            <input class="form-control prueba-medida-libre" type="number" min="0" step="0.01">
                        </div>
                        <div class="col-12 col-md-auto">
                            <button type="button" class="btn btn-outline-primary probar-precio" ${listas.length ? '' : 'disabled'}>Probar precio</button>
                        </div>
                    </div>
                    <div class="resultado-prueba small mt-3" role="status" aria-live="polite"></div>
                    </div>
                </div>
                </div>
            </div>`;
        enlazarTarjeta(tarjeta);
        actualizarCampos(tarjeta);
        actualizarResumen(tarjeta);
        establecerCompacta(tarjeta, true);
        return tarjeta;
    }

    function actualizarHasta(tarjeta) {
        const filas = [...tarjeta.querySelectorAll('.fila-rango')];
        filas.forEach((fila, indice) => {
            const hasta = fila.querySelector('.rango-hasta');
            if (indice === filas.length - 1) {
                hasta.textContent = 'en adelante';
                return;
            }
            const siguiente = filas[indice + 1].querySelector('.rango-desde')?.value;
            const numeroSiguiente = Number(siguiente);
            hasta.textContent = siguiente !== '' && Number.isFinite(numeroSiguiente)
                ? String(numeroSiguiente - 1)
                : '—';
        });
    }

    function actualizarCampos(tarjeta) {
        const forma = tarjeta.querySelector('.forma-presentacion').value;
        tarjeta.querySelector('.campo-paquete').classList.toggle('d-none', forma !== 'paquete');
        tarjeta.querySelectorAll('.campo-rollo').forEach(campo => campo.classList.toggle('d-none', forma !== 'rollo'));
        tarjeta.querySelector('.campo-tiempo').classList.toggle('d-none', forma !== 'tiempo');
        tarjeta.querySelector('.campo-minimo-cobro').classList.toggle('d-none', !['lineal', 'm2', 'rollo', 'tiempo'].includes(forma));
        tarjeta.querySelector('.subtitulo-rangos').textContent = 'Precios por rango';
        tarjeta.querySelectorAll('.rango-desde').forEach(input => {
            input.step = ['lineal', 'm2', 'rollo'].includes(forma) ? 'any' : '1';
        });
        tarjeta.querySelectorAll('.unidad-columna-precio').forEach(unidad => {
            unidad.textContent = unidadPrecio(forma);
        });
        tarjeta.querySelector('.prueba-largo').classList.toggle('d-none', forma !== 'lineal');
        tarjeta.querySelectorAll('.prueba-dimensiones').forEach(campo => campo.classList.toggle('d-none', !['m2', 'rollo'].includes(forma)));
        tarjeta.querySelector('.prueba-minutos').classList.toggle('d-none', forma !== 'tiempo');
        tarjeta.querySelector('.prueba-libre').classList.toggle('d-none', forma !== 'libre');
        actualizarHasta(tarjeta);
        document.getElementById('configCobroRollo').classList.toggle('d-none', ![...contenedor.querySelectorAll('.forma-presentacion')].some(select => select.value === 'rollo'));
    }

    function enlazarTarjeta(tarjeta) {
        tarjeta.querySelector('.nombre-presentacion').addEventListener('input', evento => {
            const nombre = evento.currentTarget.value.trim() || 'Nueva presentación';
            tarjeta.querySelector('.presentacion-titulo-compacto').textContent = nombre;
        });
        tarjeta.querySelector('.forma-presentacion').addEventListener('change', () => {
            actualizarCampos(tarjeta);
            actualizarResumen(tarjeta);
        });
        tarjeta.addEventListener('input', () => actualizarResumen(tarjeta));
        tarjeta.addEventListener('change', () => actualizarResumen(tarjeta));
        tarjeta.querySelectorAll('.quitar-presentacion').forEach(boton => boton.addEventListener('click', () => {
            tarjeta.remove();
            actualizarCobroRollo();
        }));
        tarjeta.querySelectorAll('.duplicar-presentacion').forEach(boton => boton.addEventListener('click', () => {
            const indice = [...contenedor.querySelectorAll('.presentacion-card')].indexOf(tarjeta);
            const copiaDatos = recopilar().presentaciones[indice];
            copiaDatos.id_presentacion = null;
            copiaDatos.nombre = `${copiaDatos.nombre} (copia)`;
            const copia = armarTarjeta(copiaDatos);
            tarjeta.after(copia);
            actualizarCobroRollo();
            abrirPresentacion(copia, true);
        }));
        tarjeta.querySelectorAll('.mover-arriba').forEach(boton => boton.addEventListener('click', () => {
            const anterior = tarjeta.previousElementSibling;
            if (anterior?.matches('.presentacion-card')) contenedor.insertBefore(tarjeta, anterior);
        }));
        tarjeta.querySelectorAll('.mover-abajo').forEach(boton => boton.addEventListener('click', () => {
            const siguiente = tarjeta.nextElementSibling;
            if (siguiente?.matches('.presentacion-card')) contenedor.insertBefore(siguiente, tarjeta);
        }));
        tarjeta.querySelector('.alternar-edicion').addEventListener('click', () => {
            if (tarjeta.classList.contains('presentacion-compacta')) abrirPresentacion(tarjeta);
            else compactarPresentacion(tarjeta);
        });
        tarjeta.querySelectorAll('.presentacion-acciones-compactas button').forEach(boton => {
            boton.addEventListener('click', evento => evento.stopPropagation());
        });
        tarjeta.querySelector('.presentacion-resumen-linea').addEventListener('click', evento => {
            if (!evento.target.closest('button')) abrirPresentacion(tarjeta);
        });
        tarjeta.querySelector('.presentacion-resumen-linea').addEventListener('keydown', evento => {
            if (evento.target.closest('button')) return;
            if (evento.key === 'Enter' || evento.key === ' ') {
                evento.preventDefault();
                abrirPresentacion(tarjeta);
            }
        });
        tarjeta.querySelector('.alternar-prueba').addEventListener('click', evento => {
            const boton = evento.currentTarget;
            const contenido = tarjeta.querySelector('.prueba-contenido');
            const expandido = boton.getAttribute('aria-expanded') === 'true';
            boton.setAttribute('aria-expanded', String(!expandido));
            boton.textContent = `Probar precio ${expandido ? '▸' : '▾'}`;
            contenido.classList.toggle('d-none', expandido);
        });
        tarjeta.querySelector('.agregar-rango').addEventListener('click', evento => {
            const cuerpo = tarjeta.querySelector('.filas-rangos');
            const indice = cuerpo.children.length;
            cuerpo.insertAdjacentHTML('beforeend', filaRango({}, indice, tarjeta.querySelector('.forma-presentacion').value));
            actualizarHasta(tarjeta);
            actualizarResumen(tarjeta);
            cuerpo.lastElementChild.querySelector('.rango-desde').focus();
        });
        tarjeta.querySelector('.filas-rangos').addEventListener('input', evento => {
            if (evento.target.matches('.rango-desde')) actualizarHasta(tarjeta);
        });
        tarjeta.querySelector('.probar-precio').addEventListener('click', async evento => {
            const boton = evento.currentTarget;
            const resultadoEl = tarjeta.querySelector('.resultado-prueba');
            const presentaciones = recopilar().presentaciones;
            const indice = [...contenedor.children].indexOf(tarjeta);
            const prueba = {
                cantidad: numero(tarjeta.querySelector('.prueba-cantidad').value),
                id_lista: numero(tarjeta.querySelector('.prueba-lista').value),
                presentacion: presentaciones[indice],
                ancho: numero(tarjeta.querySelector('.prueba-medida-ancho').value),
                alto: numero(tarjeta.querySelector('.prueba-medida-alto').value),
                largo: numero(tarjeta.querySelector('.prueba-medida-largo').value),
                minutos: numero(tarjeta.querySelector('.prueba-medida-minutos').value),
                precio_libre: numero(tarjeta.querySelector('.prueba-medida-libre').value),
                cobro_rollo: document.getElementById('cobroRollo').value,
            };
            boton.disabled = true;
            resultadoEl.textContent = 'Calculando…';
            try {
                const respuesta = await fetch(form.dataset.cotizarUrl, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
                    body: JSON.stringify(prueba),
                });
                const cotizacion = await respuesta.json();
                if (!respuesta.ok) throw new Error(cotizacion.error || 'No se pudo cotizar.');
                const moneda = valor => new Intl.NumberFormat('es-MX', { style: 'currency', currency: 'MXN' }).format(Number(valor));
                const detalleRollo = cotizacion.rollo
                    ? ` Rollo: ${Number(cotizacion.rollo.ancho_rollo).toFixed(2)} m.`
                    : '';
                const detalleTramos = cotizacion.tramos != null ? ` Tramos: ${cotizacion.tramos}.` : '';
                const avisos = cotizacion.avisos?.length ? ` Avisos: ${cotizacion.avisos.join(' · ')}` : '';
                resultadoEl.textContent = `Unitario: ${moneda(cotizacion.precio_unitario)} · Subtotal: ${moneda(cotizacion.subtotal)}.${detalleRollo}${detalleTramos}${avisos}`;
            } catch (error) {
                resultadoEl.textContent = error.message || 'No se pudo cotizar.';
            } finally {
                boton.disabled = false;
            }
        });
        tarjeta.addEventListener('click', evento => {
            const quitar = evento.target.closest('.quitar-rango');
            if (quitar) {
                quitar.closest('.fila-rango').remove();
                actualizarHasta(tarjeta);
                actualizarResumen(tarjeta);
            }
        });
    }

    // ── Ruta de producción ──────────────────────────────────────────────
    const seccionRuta = document.getElementById('seccionRuta');
    // Firma independiente del orden de las claves: el servidor las entrega ordenadas alfabéticamente.
    const firmaRuta = ruta => {
        const pasos = ((ruta && ruta.pasos) || []).map(p => `${Number(p.id_proceso)}:${p.opcional === true ? 1 : 0}`).join(',');
        const principal = ruta && ruta.id_proceso_principal != null && ruta.id_proceso_principal !== '' ? Number(ruta.id_proceso_principal) : '';
        return `${pasos}|${principal}`;
    };
    const rutaGuardadaFirma = firmaRuta(inicial.ruta);
    // Lee la respuesta como JSON; si no lo es (sesión vencida, error del servidor) da un mensaje claro.
    async function leerJson(respuesta, mensajeError) {
        const tipo = respuesta.headers.get('content-type') || '';
        if (!tipo.includes('application/json')) {
            throw new Error(respuesta.ok
                ? 'El servidor respondió con un formato inesperado. Recarga la página e inténtalo de nuevo.'
                : `${mensajeError} (HTTP ${respuesta.status}).`);
        }
        let cuerpo;
        try { cuerpo = await respuesta.json(); }
        catch { throw new Error('La respuesta del servidor no es válida.'); }
        if (!respuesta.ok) throw new Error(cuerpo.error || `${mensajeError} (HTTP ${respuesta.status}).`);
        return cuerpo;
    }
    const botonAplicar = document.getElementById('aplicarRutaCategoria');
    const estadoAplicar = document.getElementById('aplicarRutaEstado');
    const cabecerasAjax = { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' };

    function actualizarAplicar() {
        if (!botonAplicar) return;
        const cambiada = firmaRuta(editorRuta.obtener()) !== rutaGuardadaFirma;
        botonAplicar.disabled = cambiada;
        estadoAplicar.textContent = cambiada ? 'Hay cambios en la ruta sin guardar: guarda el producto para aplicarla.' : '';
        estadoAplicar.className = 'small mt-2 text-muted';
    }

    const editorRuta = EditorRuta.crear(document.getElementById('editorRuta'), {
        procesos: JSON.parse(seccionRuta.dataset.procesos || '[]'),
        datos: inicial.ruta,
        maxPasos: 8,
        alCambiar: actualizarAplicar,
    });
    actualizarAplicar();

    const buscarCopia = document.getElementById('copiarRutaBuscar');
    const resultadosCopia = document.getElementById('copiarRutaResultados');
    const estadoCopia = document.getElementById('copiarRutaEstado');
    let temporizadorCopia = null;
    let consultaCopia = 0;

    async function buscarProductosCopia() {
        const q = buscarCopia.value.trim();
        const id = ++consultaCopia;
        if (q.length < 2) { resultadosCopia.classList.add('d-none'); return; }
        try {
            const respuesta = await fetch(`${seccionRuta.dataset.urlBuscar}?q=${encodeURIComponent(q)}`, { headers: { 'X-Requested-With': 'XMLHttpRequest' } });
            const productos = (await leerJson(respuesta, 'No se pudo buscar productos')).filter(p => String(p.id_producto) !== (seccionRuta.dataset.idProducto || ''));
            if (id !== consultaCopia) return;
            resultadosCopia.innerHTML = productos.length
                ? productos.map(p => `<button type="button" class="list-group-item list-group-item-action" data-id="${escapar(p.id_producto)}">${escapar(p.clave)} · ${escapar(p.nombre)}</button>`).join('')
                : '<div class="list-group-item text-muted small">Sin resultados</div>';
            resultadosCopia.classList.remove('d-none');
        } catch (error) {
            estadoCopia.textContent = error.message || 'No se pudo buscar productos.';
        }
    }
    buscarCopia.addEventListener('input', () => {
        clearTimeout(temporizadorCopia);
        temporizadorCopia = setTimeout(buscarProductosCopia, 250);
    });
    buscarCopia.addEventListener('keydown', evento => { if (evento.key === 'Enter') evento.preventDefault(); });
    resultadosCopia.addEventListener('click', async evento => {
        const boton = evento.target.closest('button[data-id]');
        if (!boton) return;
        resultadosCopia.classList.add('d-none');
        try {
            const url = seccionRuta.dataset.urlRuta.replace(/\/0\/ruta$/, `/${boton.dataset.id}/ruta`);
            const respuesta = await fetch(url, { headers: { 'X-Requested-With': 'XMLHttpRequest' } });
            const datos = await leerJson(respuesta, 'No se pudo copiar la ruta');
            editorRuta.establecer(datos);
            actualizarAplicar();
            buscarCopia.value = '';
            estadoCopia.textContent = `Ruta copiada de «${datos.nombre}». Pulsa Guardar para conservarla.`
                + (datos.omitidos ? ` Se omitieron ${datos.omitidos} proceso(s) inactivo(s).` : '');
        } catch (error) {
            estadoCopia.textContent = error.message;
        }
    });

    if (botonAplicar) {
        botonAplicar.addEventListener('click', async () => {
            const modo = document.querySelector('input[name="modoAplicar"]:checked').value;
            const mostrar = (texto, tipo) => { estadoAplicar.textContent = texto; estadoAplicar.className = `small mt-2 ${{ error: 'text-danger', ok: 'text-success', info: 'text-muted' }[tipo] || 'text-muted'}`; };
            botonAplicar.disabled = true;
            estadoAplicar.textContent = '';
            try {
                const resConteo = await fetch(seccionRuta.dataset.urlAplicar, { headers: { 'X-Requested-With': 'XMLHttpRequest' } });
                const conteo = await leerJson(resConteo, 'No se pudo calcular el conteo');
                const destino = modo === 'sin_ruta' ? conteo.sin_ruta : conteo.total;
                const resumen = `${conteo.sin_ruta} sin ruta · ${conteo.total} en total`;
                if (!destino) { mostrar(`No hay productos que actualizar en «${conteo.categoria}» (${resumen}).`, 'info'); return; }
                const texto = modo === 'sin_ruta'
                    ? `Se copiará la ruta a ${destino} producto(s) sin ruta de «${conteo.categoria}» (${resumen}).`
                    : `Se SOBRESCRIBIRÁ la ruta de ${destino} producto(s) de «${conteo.categoria}» (${resumen}).`;
                const confirmado = await pfConfirmar({
                    titulo: modo === 'sin_ruta' ? 'Aplicar ruta a la categoría' : 'Sobrescribir rutas de la categoría',
                    mensaje: texto,
                    aceptar: modo === 'sin_ruta' ? 'Aplicar' : 'Sobrescribir',
                    peligro: modo !== 'sin_ruta',
                });
                if (!confirmado) return;
                const respuesta = await fetch(seccionRuta.dataset.urlAplicar, { method: 'POST', headers: cabecerasAjax, body: JSON.stringify({ modo }) });
                const resultado = await leerJson(respuesta, 'No se pudo aplicar la ruta');
                mostrar(`Ruta aplicada a ${resultado.aplicados} producto(s) de «${resultado.categoria}».`, 'ok');
            } catch (error) {
                mostrar(error.message, 'error');
            } finally {
                botonAplicar.disabled = firmaRuta(editorRuta.obtener()) !== rutaGuardadaFirma;
            }
        });
    }

    function actualizarCobroRollo() {
        const tieneRollo = [...contenedor.querySelectorAll('.forma-presentacion')].some(select => select.value === 'rollo');
        document.getElementById('configCobroRollo').classList.toggle('d-none', !tieneRollo);
    }

    function recopilar() {
        return {
            nombre: document.getElementById('nombreProducto').value.trim(),
            categoria: document.getElementById('categoriaProducto').value.trim(),
            descripcion: document.getElementById('descripcionProducto').value.trim(),
            cobro_rollo: document.getElementById('cobroRollo').value,
            ruta: editorRuta.obtener(),
            presentaciones: [...contenedor.querySelectorAll('.presentacion-card')].map(tarjeta => {
                const forma = tarjeta.querySelector('.forma-presentacion').value;
                const precios = [];
                const rangos = [...tarjeta.querySelectorAll('.fila-rango')].map((fila, indice) => {
                    const desde = indice === 0 ? 1 : numero(fila.querySelector('.rango-desde').value);
                    const preciosRango = {};
                    fila.querySelectorAll('.rango-precio').forEach(input => {
                        if (input.value !== '') {
                            const idLista = input.dataset.lista;
                            const monto = numero(input.value);
                            preciosRango[idLista] = monto;
                            precios.push({
                                id_lista: Number(idLista),
                                cantidad_minima: indice === 0 ? 0 : desde,
                                precio: monto,
                            });
                        }
                    });
                    return { desde, precios: preciosRango };
                });
                return {
                    id_presentacion: numero(tarjeta.dataset.idPresentacion),
                    nombre: tarjeta.querySelector('.nombre-presentacion').value.trim(),
                    forma,
                    piezas_paquete: forma === 'paquete' ? numero(tarjeta.querySelector('.piezas-paquete').value) : null,
                    ancho_rollo: forma === 'rollo' ? numero(tarjeta.querySelector('.ancho-rollo').value) : null,
                    margen_rollo: forma === 'rollo' ? numero(tarjeta.querySelector('.margen-rollo').value) : null,
                    cargo_union: forma === 'rollo' ? numero(tarjeta.querySelector('.cargo-union').value) : null,
                    minutos_minimos: forma === 'tiempo' ? numero(tarjeta.querySelector('.minutos-minimos').value) : null,
                    minimo_cobro: ['lineal', 'm2', 'rollo', 'tiempo'].includes(forma)
                        ? numero(tarjeta.querySelector('.minimo-cobro').value)
                        : null,
                    rangos,
                    precios,
                };
            }),
        };
    }

    form.addEventListener('submit', evento => {
        const datos = recopilar();
        const errores = [];
        const tarjetasConError = new Set();
        if (!datos.nombre) errores.push('El nombre del producto es obligatorio.');
        if (!datos.presentaciones.length) errores.push('Agrega al menos una presentación.');
        errores.push(...editorRuta.validar());
        datos.presentaciones.forEach((presentacion, indice) => {
            const tarjeta = contenedor.children[indice];
            const agregarError = mensaje => {
                errores.push(mensaje);
                tarjetasConError.add(tarjeta);
            };
            if (!presentacion.nombre) agregarError(`La presentación ${indice + 1} requiere un nombre.`);
            if (presentacion.forma === 'paquete' && (!presentacion.piezas_paquete || presentacion.piezas_paquete < 1)) agregarError(`Indica las piezas por paquete en la presentación ${indice + 1}.`);
            if (presentacion.forma === 'rollo' && (!presentacion.ancho_rollo || presentacion.ancho_rollo <= 0)) agregarError(`Indica un ancho de rollo válido en la presentación ${indice + 1}.`);
            const listaPredeterminada = listas.find(lista => lista.predeterminada);
            const primerRango = tarjeta.querySelector('.fila-rango');
            const precioPredeterminado = listaPredeterminada
                ? primerRango?.querySelector(`.rango-precio[data-lista="${CSS.escape(String(listaPredeterminada.id_lista))}"]`)
                : null;
            if (!listaPredeterminada || !precioPredeterminado || precioPredeterminado.value === '') {
                agregarError(`El precio de la lista predeterminada es obligatorio en la presentación ${indice + 1}.`);
            }
            const decimal = ['lineal', 'm2', 'rollo'].includes(presentacion.forma);
            let desdeAnterior = 1;
            presentacion.rangos.forEach((rango, indiceRango) => {
                if (indiceRango > 0) {
                    const desde = rango.desde;
                    if (desde == null || !Number.isFinite(desde) || desde <= desdeAnterior || (!decimal && !Number.isInteger(desde))) {
                        agregarError(`El Desde del rango ${indiceRango + 1} de la presentación ${indice + 1} debe ser mayor al anterior${decimal ? '' : ' y entero'}.`);
                    } else {
                        desdeAnterior = desde;
                    }
                }
                Object.values(rango.precios).forEach(precio => {
                    if (precio == null || !Number.isFinite(precio) || precio < 0) {
                        agregarError(`Los precios de los rangos de la presentación ${indice + 1} deben ser iguales o mayores que cero.`);
                    }
                });
            });
        });
        contenedor.querySelectorAll('.presentacion-card').forEach(tarjeta => {
            tarjeta.classList.toggle('presentacion-con-error', tarjetasConError.has(tarjeta));
        });
        errorEl.textContent = errores.join(' ');
        if (errores.length) {
            evento.preventDefault();
            const primeraTarjetaConError = [...tarjetasConError][0];
            if (primeraTarjetaConError) {
                abrirPresentacion(primeraTarjetaConError);
                primeraTarjetaConError.scrollIntoView({ behavior: 'smooth', block: 'center' });
            }
            errorEl.scrollIntoView({ behavior: 'smooth', block: 'center' });
            return;
        }
        document.getElementById('datosProducto').value = JSON.stringify(datos);
        document.getElementById('guardarProducto').disabled = true;
    });

    document.getElementById('agregarPresentacion').addEventListener('click', () => {
        const tarjeta = armarTarjeta({ forma: 'fijo', margen_rollo: 0.05, cargo_union: 0 });
        contenedor.insertBefore(tarjeta, document.getElementById('agregarPresentacion'));
        actualizarCobroRollo();
        abrirPresentacion(tarjeta, true);
    });

    (inicial.presentaciones || []).forEach(presentacion => contenedor.insertBefore(armarTarjeta(presentacion), document.getElementById('agregarPresentacion')));
    if (!contenedor.querySelector('.presentacion-card')) contenedor.insertBefore(armarTarjeta({ forma: 'fijo', margen_rollo: 0.05, cargo_union: 0 }), document.getElementById('agregarPresentacion'));
    actualizarCobroRollo();
})();
