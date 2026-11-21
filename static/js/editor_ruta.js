/* Editor de ruta de producción, reutilizable (producto, reglas de clasificación).
 *
 *   const editor = EditorRuta.crear(contenedor, {
 *     procesos: [{ id_proceso, nombre, activo }],   // sin "Sin clasificar"
 *     datos: { pasos: [{ id_proceso, opcional }], id_proceso_principal },
 *     maxPasos: 8,
 *     alCambiar: () => {},
 *   });
 *   editor.obtener()   -> { pasos: [{ id_proceso, opcional }], id_proceso_principal }
 *   editor.establecer(datos)
 *   editor.validar()   -> [mensajes]
 */
(() => {
    const escapar = valor => String(valor ?? '').replace(/[&<>"']/g,
        c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

    function crear(contenedor, opciones = {}) {
        const procesos = opciones.procesos || [];
        const maxPasos = opciones.maxPasos || 8;
        const porId = new Map(procesos.map(p => [Number(p.id_proceso), p]));
        let principal = null;

        contenedor.classList.add('editor-ruta');
        contenedor.innerHTML = `
            <div class="er-vista vacia" aria-live="polite"></div>
            <div class="er-lista" role="list"></div>
            <div class="er-agregar">
                <select class="form-select er-agregar-select" aria-label="Proceso a agregar"></select>
                <button type="button" class="btn btn-outline-primary er-agregar-boton">+ Agregar paso</button>
            </div>
            <div class="er-error" role="alert"></div>
            <div class="mt-3">
                <label class="form-label small mb-1">Proceso principal <span class="text-muted">(se usa si la ruta queda vacía)</span></label>
                <select class="form-select er-principal"></select>
            </div>`;
        const vista = contenedor.querySelector('.er-vista');
        const lista = contenedor.querySelector('.er-lista');
        const selectAgregar = contenedor.querySelector('.er-agregar-select');
        const botonAgregar = contenedor.querySelector('.er-agregar-boton');
        const selectPrincipal = contenedor.querySelector('.er-principal');
        const errorEl = contenedor.querySelector('.er-error');

        const filas = () => [...lista.querySelectorAll('.er-fila')];
        const idDe = fila => Number(fila.dataset.id);
        const nombreDe = id => porId.get(id)?.nombre || `Proceso ${id}`;
        const activoDe = id => porId.get(id)?.activo === true;

        function crearFila(id, opcional) {
            const fila = document.createElement('div');
            fila.className = 'er-fila';
            fila.setAttribute('role', 'listitem');
            fila.dataset.id = id;
            fila.innerHTML = `
                <div class="er-arrastre" aria-hidden="true">⋮⋮</div>
                <div class="er-nombre">${escapar(nombreDe(id))}${activoDe(id) ? '' : ' <span class="badge text-bg-secondary ms-1">Inactivo</span>'}</div>
                <label class="er-opcional"><input class="form-check-input er-opcional-check" type="checkbox" ${opcional ? 'checked' : ''}> Opcional</label>
                <div class="er-menu">
                    <div class="dropdown">
                        <button class="btn btn-link dropdown-toggle no-caret" type="button" data-bs-toggle="dropdown"
                                aria-expanded="false" aria-label="Acciones para ${escapar(nombreDe(id))}">⋯</button>
                        <ul class="dropdown-menu dropdown-menu-end">
                            <li><button type="button" class="dropdown-item er-subir">Subir</button></li>
                            <li><button type="button" class="dropdown-item er-bajar">Bajar</button></li>
                            <li><button type="button" class="dropdown-item text-danger er-quitar">Quitar</button></li>
                        </ul>
                    </div>
                </div>`;
            return fila;
        }

        function opcionesAgregar() {
            const usados = new Set(filas().map(idDe));
            const libres = procesos.filter(p => p.activo && !usados.has(Number(p.id_proceso)));
            selectAgregar.innerHTML = libres.length
                ? libres.map(p => `<option value="${escapar(p.id_proceso)}">${escapar(p.nombre)}</option>`).join('')
                : '<option value="">No quedan procesos</option>';
            const lleno = filas().length >= maxPasos;
            selectAgregar.disabled = botonAgregar.disabled = !libres.length || lleno;
            if (lleno) selectAgregar.innerHTML = `<option value="">Máximo ${maxPasos} pasos</option>`;
        }

        function opcionesPrincipal() {
            const base = ['<option value="">Ninguno</option>'];
            procesos.forEach(p => {
                if (p.activo || Number(p.id_proceso) === principal) {
                    base.push(`<option value="${escapar(p.id_proceso)}">${escapar(p.nombre)}${p.activo ? '' : ' (inactivo)'}</option>`);
                }
            });
            selectPrincipal.innerHTML = base.join('');
            selectPrincipal.value = principal == null ? '' : String(principal);
        }

        function actualizarVista() {
            const pasos = filas().map(fila => nombreDe(idDe(fila)).toUpperCase()
                + (fila.querySelector('.er-opcional-check').checked ? '*' : ''));
            if (pasos.length) {
                vista.classList.remove('vacia');
                vista.textContent = pasos.join(' → ') + (pasos.some(p => p.endsWith('*')) ? '   (* = opcional)' : '');
            } else {
                vista.classList.add('vacia');
                vista.textContent = principal != null
                    ? `Sin pasos: se usará solo el proceso principal (${nombreDe(principal).toUpperCase()}).`
                    : 'Sin ruta. Agrega pasos o elige un proceso principal.';
            }
        }

        function marcarInvalidas() {
            filas().forEach(fila => fila.classList.toggle('invalida', !activoDe(idDe(fila))));
        }

        function refrescar(notificar = true) {
            opcionesAgregar();
            actualizarVista();
            marcarInvalidas();
            if (!errorEl.dataset.fijo) errorEl.textContent = '';
            if (notificar && typeof opciones.alCambiar === 'function') opciones.alCambiar();
        }

        function establecer(datos = {}) {
            lista.innerHTML = '';
            (datos.pasos || []).forEach(paso => lista.appendChild(crearFila(Number(paso.id_proceso), paso.opcional === true)));
            principal = datos.id_proceso_principal == null || datos.id_proceso_principal === '' ? null : Number(datos.id_proceso_principal);
            opcionesPrincipal();
            refrescar(false);
        }

        function obtener() {
            return {
                pasos: filas().map(fila => ({
                    id_proceso: idDe(fila),
                    opcional: fila.querySelector('.er-opcional-check').checked,
                })),
                id_proceso_principal: principal,
            };
        }

        function validar() {
            const errores = [];
            const ids = filas().map(idDe);
            if (ids.length > maxPasos) errores.push(`La ruta no puede tener más de ${maxPasos} pasos.`);
            ids.forEach((id, i) => {
                if (!activoDe(id)) errores.push(`El proceso «${nombreDe(id)}» está inactivo: quítalo de la ruta.`);
                if (ids.indexOf(id) !== i) errores.push(`El proceso «${nombreDe(id)}» está repetido.`);
            });
            if (principal != null && !activoDe(principal)) errores.push('El proceso principal está inactivo.');
            return [...new Set(errores)];
        }

        botonAgregar.addEventListener('click', () => {
            const id = Number(selectAgregar.value);
            if (!id || filas().length >= maxPasos) return;
            lista.appendChild(crearFila(id, false));
            refrescar();
        });
        selectPrincipal.addEventListener('change', () => {
            principal = selectPrincipal.value === '' ? null : Number(selectPrincipal.value);
            refrescar();
        });
        lista.addEventListener('change', evento => {
            if (evento.target.matches('.er-opcional-check')) refrescar();
        });
        lista.addEventListener('click', evento => {
            const fila = evento.target.closest('.er-fila');
            if (!fila) return;
            if (evento.target.closest('.er-quitar')) { fila.remove(); refrescar(); }
            else if (evento.target.closest('.er-subir') && fila.previousElementSibling) {
                fila.previousElementSibling.before(fila); refrescar();
            } else if (evento.target.closest('.er-bajar') && fila.nextElementSibling) {
                fila.nextElementSibling.after(fila); refrescar();
            }
        });
        if (window.Sortable) {
            new Sortable(lista, { animation: 150, handle: '.er-arrastre', draggable: '.er-fila', onEnd: () => refrescar() });
        }

        establecer(opciones.datos);
        return { obtener, establecer, validar, elemento: contenedor };
    }

    window.EditorRuta = { crear };
})();
