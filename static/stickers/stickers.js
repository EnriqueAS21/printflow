(function () {
    'use strict';

    const app = document.getElementById('stickers-app');
    if (!app) return;

    const API = app.dataset.api;
    const CLAVE = 'pf_stickers_estado';
    const PRESETS = { carta: [215.9, 279.4], tabloide: [304.8, 457.2] };
    const HOJAS_FISICAS = { carta: 'Carta', tabloide: '12×18' };
    const FORMAS = [
        ['rectangulo', 'Rectángulo'], ['redondeado', 'Redondeado'], ['circulo', 'Círculo'], ['silueta', 'Silueta'],
    ];
    const MODOS = [['cubrir', 'Cubrir'], ['ajustar', 'Ajustar'], ['estirar', 'Estirar']];
    const AJUSTE_DEFECTO = {
        cantidad: 1, forma: 'rectangulo', modo: 'cubrir', sangrado_mm: 1.5, padding_mm: 2,
        radio_esquina_mm: 3, borde_mm: 2, color_fondo: null, candado: true,
    };

    const $ = (sel, el = document) => el.querySelector(sel);
    const $$ = (sel, el = document) => Array.from(el.querySelectorAll(sel));

    function estadoInicial() {
        return {
            tipo: 'con_corte',
            plantilla: null,
            ocultar_gris: true,
            hoja: { preset: 'carta', ancho_mm: 215.9, alto_mm: 279.4, margen_mm: 5, orientacion: 'vertical' },
            marcas: {
                activas: true, tipo: 'L', largo_mm: 10, grosor_mm: 0.5, distancia_borde_mm: 10,
                holgura_mm: 3, distancia_inferior_mm: 40, incluir_corte: true,
            },
            separacion_mm: 3,
            rellenar: false,
            disenos: [],
            vista: 0,
        };
    }

    // ---------------------------------------------------------------- estado

    let estado = cargarEstado();
    let plantillasLista = [];
    let resultado = null;
    let resultadoValido = false;
    let temporizador = null;
    let controlador = null;
    const bibCache = new Map();
    const seleccion = new Set();
    let bibPeticion = 0;

    function cargarEstado() {
        const base = estadoInicial();
        try {
            const g = JSON.parse(localStorage.getItem(CLAVE) || 'null');
            if (!g || typeof g !== 'object') return base;
            const estadoRestaurado = Object.assign(base, g, {
                hoja: Object.assign(base.hoja, g.hoja || {}),
                marcas: Object.assign(base.marcas, g.marcas || {}),
            });
            estadoRestaurado.disenos = (Array.isArray(g.disenos) ? g.disenos : [])
                .filter((d) => d && typeof d.id === 'string')
                .map((d) => Object.assign({}, AJUSTE_DEFECTO, d));
            if (!['con_corte', 'sin_corte'].includes(estadoRestaurado.tipo)) estadoRestaurado.tipo = 'con_corte';
            if (!['carta', 'tabloide', 'personalizada'].includes(estadoRestaurado.hoja.preset)) estadoRestaurado.hoja.preset = 'carta';
            if (!Number.isInteger(estadoRestaurado.vista)) estadoRestaurado.vista = 0;
            if (!(Number(estadoRestaurado.marcas.distancia_inferior_mm) >= 40)) estadoRestaurado.marcas.distancia_inferior_mm = 40;
            return estadoRestaurado;
        } catch (e) {
            return base;
        }
    }

    function guardar() {
        try {
            localStorage.setItem(CLAVE, JSON.stringify(estado));
        } catch (e) { /* almacenamiento lleno o bloqueado */ }
    }

    function cambio() {
        guardar();
        programarCalculo();
    }

    function leerRuta(ruta) {
        return ruta.split('.').reduce((o, k) => (o == null ? undefined : o[k]), estado);
    }

    function escribirRuta(ruta, valor) {
        const claves = ruta.split('.');
        const ultima = claves.pop();
        claves.reduce((o, k) => o[k], estado)[ultima] = valor;
    }

    function disenoPorId(id) {
        return estado.disenos.find((d) => d.id === id);
    }

    function plantillaActual() {
        if (estado.tipo !== 'con_corte' || !estado.plantilla) return null;
        return plantillasLista.find((p) => p.slug === estado.plantilla) || null;
    }

    function caso() {
        if (estado.tipo === 'sin_corte') return 'sin_corte';
        return estado.plantilla ? 'plantilla' : 'marcas';
    }

    function hojaDims() {
        const h = estado.hoja;
        if (h.preset === 'personalizada') return [h.ancho_mm, h.alto_mm];
        const [a, b] = PRESETS[h.preset] || PRESETS.carta;
        return h.orientacion === 'horizontal' ? [b, a] : [a, b];
    }

    function payload() {
        const [ancho, alto] = hojaDims();
        const m = estado.marcas;
        return {
            tipo: estado.tipo,
            plantilla: estado.tipo === 'con_corte' ? (estado.plantilla || null) : null,
            ocultar_gris: !!estado.ocultar_gris,
            hoja: { ancho_mm: ancho, alto_mm: alto, margen_mm: estado.hoja.margen_mm },
            marcas: {
                activas: !!m.activas,
                tipo: m.tipo,
                largo_mm: m.largo_mm,
                grosor_mm: m.grosor_mm,
                distancia_borde_mm: m.distancia_borde_mm,
                holgura_mm: m.holgura_mm,
                distancia_inferior_mm: m.distancia_inferior_mm,
            },
            incluir_marcas_corte: !!m.incluir_corte,
            separacion_mm: estado.separacion_mm,
            rellenar: !!estado.rellenar,
            disenos: estado.disenos.map((d) => ({
                id: d.id,
                ancho_mm: d.ancho_mm,
                alto_mm: d.alto_mm,
                cantidad: d.cantidad,
                forma: d.forma,
                modo: d.modo,
                sangrado_mm: d.sangrado_mm,
                padding_mm: d.padding_mm,
                radio_esquina_mm: d.radio_esquina_mm,
                borde_mm: d.borde_mm,
                color_fondo: d.color_fondo || null,
            })),
        };
    }

    // ---------------------------------------------------------------- utilidades

    function esc(texto) {
        return String(texto == null ? '' : texto).replace(/[&<>"']/g, (c) => (
            { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
        ));
    }

    function num(valor, decimales = 1) {
        return Number(valor).toLocaleString('es-MX', { maximumFractionDigits: decimales });
    }

    function redondear(valor, decimales = 1) {
        const f = 10 ** decimales;
        return Math.round(valor * f) / f;
    }

    function toast(mensaje, tipo = 'danger') {
        const iconos = { danger: 'x-circle', warning: 'exclamation-triangle', success: 'check-circle', info: 'info-circle' };
        const el = document.createElement('div');
        el.className = `toast align-items-center text-bg-${tipo} border-0`;
        el.setAttribute('role', 'alert');
        el.setAttribute('aria-live', 'assertive');
        el.setAttribute('aria-atomic', 'true');
        el.innerHTML = `<div class="d-flex"><div class="toast-body"><i class="bi bi-${iconos[tipo] || 'info-circle'} me-2"></i>${esc(mensaje)}</div>`
            + '<button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast" aria-label="Cerrar"></button></div>';
        $('#st-toasts').appendChild(el);
        el.addEventListener('hidden.bs.toast', () => el.remove());
        bootstrap.Toast.getOrCreateInstance(el, { delay: tipo === 'danger' ? 6000 : 4500 }).show();
    }

    async function errorDe(respuesta) {
        if (respuesta.status === 413) return new Error('El archivo supera el límite de subida');
        let mensaje = `Error ${respuesta.status}`;
        try {
            const datos = await respuesta.json();
            if (datos && datos.error) mensaje = datos.error;
        } catch (e) { /* respuesta sin JSON */ }
        return new Error(mensaje);
    }

    async function api(ruta, opciones = {}) {
        const op = Object.assign({}, opciones);
        op.headers = Object.assign({ 'X-Requested-With': 'XMLHttpRequest' }, opciones.headers || {});
        if ('json' in op) {
            op.body = JSON.stringify(op.json);
            op.headers['Content-Type'] = 'application/json';
            delete op.json;
        }
        const respuesta = await fetch(API + ruta, op);
        if (!respuesta.ok) throw await errorDe(respuesta);
        return respuesta;
    }

    async function apiJson(ruta, opciones) {
        const respuesta = await api(ruta, opciones);
        let datos;
        try {
            datos = await respuesta.json();
        } catch (e) {
            throw new Error('Respuesta inesperada del servidor.');
        }
        if (datos && datos.success === false) throw new Error(datos.error || 'Ocurrió un error.');
        return datos;
    }

    function nombreDeDisposicion(cabecera) {
        if (!cabecera) return null;
        const utf = /filename\*\s*=\s*UTF-8''([^;]+)/i.exec(cabecera);
        if (utf) {
            try { return decodeURIComponent(utf[1].trim().replace(/^"|"$/g, '')); } catch (e) { /* sigue */ }
        }
        const simple = /filename\s*=\s*"?([^";]+)"?/i.exec(cabecera);
        return simple ? simple[1].trim() : null;
    }

    // ---------------------------------------------------------------- cálculo

    const previewBox = $('#preview-box');
    const previewSvg = $('#preview-svg');

    function setCargando(activo) {
        previewBox.classList.toggle('cargando', activo);
    }

    function programarCalculo() {
        clearTimeout(temporizador);
        if (!estado.disenos.length) {
            if (controlador) controlador.abort();
            controlador = null;
            resultado = null;
            resultadoValido = false;
            setCargando(false);
            renderResultado();
            return;
        }
        setCargando(true);
        temporizador = setTimeout(calcular, 350);
    }

    async function calcular() {
        if (controlador) controlador.abort();
        const c = new AbortController();
        controlador = c;
        try {
            const datos = await apiJson('calcular', { method: 'POST', json: payload(), signal: c.signal });
            if (c !== controlador) return;
            resultado = datos;
            resultadoValido = true;
            if (estado.vista >= (datos.vistas || []).length || estado.vista < 0) {
                estado.vista = 0;
                guardar();
            }
            renderResultado();
        } catch (e) {
            if (e.name === 'AbortError' || c !== controlador) return;
            resultadoValido = false;
            renderBotones();
            toast(e.message);
        } finally {
            if (c === controlador) {
                controlador = null;
                setCargando(false);
            }
        }
    }

    // ---------------------------------------------------------------- render: controles

    function renderControles() {
        $$('input[name="tipo"]').forEach((r) => { r.checked = r.value === estado.tipo; });
        $$('input[name="preset"]').forEach((r) => { r.checked = r.value === estado.hoja.preset; });
        $$('[data-estado]').forEach((el) => {
            const valor = leerRuta(el.dataset.estado);
            if (el.type === 'checkbox') el.checked = !!valor;
            else el.value = valor == null ? '' : valor;
        });
        renderOrientacion();
    }

    function renderOrientacion() {
        $$('#orientacion [data-orientacion]').forEach((b) => {
            b.classList.toggle('activo', b.dataset.orientacion === estado.hoja.orientacion);
        });
    }

    function renderSelectPlantillas() {
        const sel = $('#sel-plantilla');
        const validas = plantillasLista.filter((p) => p.valida);
        sel.innerHTML = '<option value="">Sin plantilla (marcas PrintFlow)</option>'
            + validas.map((p) => `<option value="${esc(p.slug)}">${esc(p.nombre)}</option>`).join('');
        sel.value = estado.plantilla || '';
    }

    function renderModo() {
        const conCorte = estado.tipo === 'con_corte';
        const p = plantillaActual();
        $('#grupo-plantilla').classList.toggle('d-none', !conCorte);
        $('#info-plantilla').classList.toggle('d-none', !p);
        $('#grupo-hoja').classList.toggle('d-none', conCorte && !!estado.plantilla);
        $('#grupo-marcas').classList.toggle('d-none', !(conCorte && !estado.plantilla));
        $('#hoja-personalizada').classList.toggle('d-none', estado.hoja.preset !== 'personalizada');
        if (p) {
            const [x0, y0, x1, y1] = p.area || [0, 0, 0, 0];
            $('#chip-hoja-fisica').textContent = HOJAS_FISICAS[p.hoja_fisica] || p.hoja_fisica || '—';
            $('#chip-area').textContent = `${num(x1 - x0)} × ${num(y1 - y0)} mm`;
            $('#chip-libre').textContent = p.libre_abajo != null ? `${num(p.libre_abajo)} mm` : '—';
        }
        const actual = caso();
        $$('.pie-set').forEach((s) => {
            const activo = s.dataset.caso === actual;
            s.classList.toggle('inactivo', !activo);
            s.setAttribute('aria-hidden', activo ? 'false' : 'true');
        });
        renderBotones();
    }

    function aplicarOrientacion(orientacion) {
        const h = estado.hoja;
        if (h.preset === 'personalizada') {
            const horizontal = h.ancho_mm > h.alto_mm;
            if ((orientacion === 'horizontal') !== horizontal && h.ancho_mm !== h.alto_mm) {
                [h.ancho_mm, h.alto_mm] = [h.alto_mm, h.ancho_mm];
                $('#hoja-ancho').value = h.ancho_mm;
                $('#hoja-alto').value = h.alto_mm;
            }
        }
        h.orientacion = orientacion;
        renderOrientacion();
        renderSugerida();
        cambio();
    }

    // ---------------------------------------------------------------- render: diseños

    const lista = $('#lista-disenos');

    function filaHtml(d, indice) {
        const formas = FORMAS.map(([v, t]) => {
            const deshabilitada = v === 'silueta' && !d.tiene_alfa;
            return `<option value="${v}"${d.forma === v ? ' selected' : ''}${deshabilitada ? ' disabled' : ''}>${t}${deshabilitada ? ' (sin transparencia)' : ''}</option>`;
        }).join('');
        const modos = MODOS.map(([v, t]) => `<option value="${v}"${d.modo === v ? ' selected' : ''}>${t}</option>`).join('');
        const conColor = !!d.color_fondo;
        return `
        <div class="diseno-fila" data-id="${esc(d.id)}">
            <div class="d-flex align-items-start gap-2">
                <img class="mini" src="${esc(d.url_mini)}" alt="" loading="lazy">
                <div class="flex-grow-1 min-w-0">
                    <div class="d-flex align-items-center gap-1">
                        <div class="nombre text-truncate flex-grow-1" title="${esc(d.nombre)}">${esc(d.nombre)}</div>
                        <button type="button" class="btn-icono" style="width:28px;height:28px" data-bs-toggle="collapse" data-bs-target="#aj-${indice}" aria-expanded="false" title="Ajustes"><i class="bi bi-sliders"></i></button>
                        <button type="button" class="btn-icono" style="width:28px;height:28px" data-accion="quitar" title="Quitar del trabajo"><i class="bi bi-x-lg"></i></button>
                    </div>
                    <div class="d-flex align-items-center gap-1 mt-1 flex-wrap">
                        <input type="number" class="form-control inp-cant" data-campo="cantidad" min="1" max="5000" step="1" value="${d.cantidad}" title="Cantidad">
                        <input type="number" class="form-control inp-mm" data-campo="ancho_mm" min="5" max="1000" step="0.1" value="${d.ancho_mm}" title="Ancho (mm)">
                        <span class="text-muted small">×</span>
                        <input type="number" class="form-control inp-mm" data-campo="alto_mm" min="5" max="1000" step="0.1" value="${d.alto_mm}" title="Alto (mm)">
                        <span class="text-muted small">mm</span>
                        <button type="button" class="btn-icono${d.candado ? ' activo' : ''}" style="width:28px;height:28px" data-accion="candado" title="Mantener proporción"><i class="bi bi-${d.candado ? 'lock-fill' : 'unlock'}"></i></button>
                        <select class="form-select sel-forma" data-campo="forma" title="Forma">${formas}</select>
                    </div>
                </div>
            </div>
            <div class="collapse" id="aj-${indice}">
                <div class="row g-2 ajustes-diseno">
                    <div class="col-6 col-sm-4">
                        <div class="st-label">Modo</div>
                        <select class="form-select" data-campo="modo">${modos}</select>
                    </div>
                    <div class="col-6 col-sm-4">
                        <div class="st-label">Sangrado (mm)</div>
                        <input type="number" class="form-control" data-campo="sangrado_mm" min="0" max="5" step="0.1" value="${d.sangrado_mm}">
                    </div>
                    <div class="col-6 col-sm-4">
                        <div class="st-label">Padding (mm)</div>
                        <input type="number" class="form-control" data-campo="padding_mm" min="0" max="20" step="0.5" value="${d.padding_mm}">
                    </div>
                    <div class="col-6 col-sm-4">
                        <div class="st-label">Radio esquina (mm)</div>
                        <input type="number" class="form-control" data-campo="radio_esquina_mm" min="0" max="500" step="0.5" value="${d.radio_esquina_mm}">
                    </div>
                    <div class="col-6 col-sm-4">
                        <div class="st-label">Borde (mm)</div>
                        <input type="number" class="form-control" data-campo="borde_mm" min="0.5" max="10" step="0.5" value="${d.borde_mm}">
                    </div>
                    <div class="col-6 col-sm-4">
                        <div class="st-label">Color de fondo</div>
                        <div class="d-flex align-items-center gap-2">
                            <input class="form-check-input mt-0" type="checkbox" data-campo="color_activo"${conColor ? ' checked' : ''} title="Usar color de fondo">
                            <input type="color" class="form-control form-control-color" data-campo="color_fondo" value="${esc(d.color_fondo || '#FFFFFF')}"${conColor ? '' : ' disabled'}>
                        </div>
                    </div>
                </div>
            </div>
        </div>`;
    }

    function renderDisenos() {
        if (!estado.disenos.length) {
            lista.innerHTML = '<div class="lista-vacia"><i class="bi bi-images d-block fs-4 mb-1"></i>Aún no hay diseños en el trabajo.</div>';
            return;
        }
        lista.innerHTML = estado.disenos.map(filaHtml).join('');
        $$('.diseno-fila', lista).forEach((fila) => actualizarFila(fila, disenoPorId(fila.dataset.id)));
    }

    function actualizarFila(fila, d) {
        $('[data-campo="sangrado_mm"]', fila).disabled = d.modo === 'ajustar';
        $('[data-campo="radio_esquina_mm"]', fila).disabled = d.forma !== 'redondeado';
        $('[data-campo="borde_mm"]', fila).disabled = d.forma !== 'silueta';
    }

    function actualizarCampo(d, campo, el, fila) {
        const numero = parseFloat(el.value);
        const valido = Number.isFinite(numero);
        switch (campo) {
            case 'cantidad': {
                const entero = parseInt(el.value, 10);
                if (!Number.isInteger(entero) || entero < 1) return false;
                d.cantidad = entero;
                return true;
            }
            case 'ancho_mm':
            case 'alto_mm': {
                if (!valido || numero <= 0) return false;
                const otro = campo === 'ancho_mm' ? 'alto_mm' : 'ancho_mm';
                if (d.candado) {
                    if (!d.proporcion) d.proporcion = d.alto_mm / d.ancho_mm;
                    const nuevo = campo === 'ancho_mm' ? numero * d.proporcion : numero / d.proporcion;
                    d[otro] = redondear(nuevo, 1);
                    $(`[data-campo="${otro}"]`, fila).value = d[otro];
                }
                d[campo] = numero;
                return true;
            }
            case 'forma':
            case 'modo':
                d[campo] = el.value;
                actualizarFila(fila, d);
                return true;
            case 'color_activo': {
                const color = $('[data-campo="color_fondo"]', fila);
                color.disabled = !el.checked;
                d.color_fondo = el.checked ? color.value.toUpperCase() : null;
                return true;
            }
            case 'color_fondo':
                if (!$('[data-campo="color_activo"]', fila).checked) return false;
                d.color_fondo = el.value.toUpperCase();
                return true;
            default:
                if (!valido) return false;
                d[campo] = numero;
                return true;
        }
    }

    lista.addEventListener('input', (e) => {
        const el = e.target;
        const campo = el.dataset.campo;
        if (!campo) return;
        const fila = el.closest('.diseno-fila');
        const d = fila && disenoPorId(fila.dataset.id);
        if (d && actualizarCampo(d, campo, el, fila)) cambio();
    });

    lista.addEventListener('click', (e) => {
        const boton = e.target.closest('[data-accion]');
        if (!boton) return;
        const fila = boton.closest('.diseno-fila');
        const d = fila && disenoPorId(fila.dataset.id);
        if (!d) return;
        if (boton.dataset.accion === 'candado') {
            d.candado = !d.candado;
            if (d.candado) d.proporcion = d.alto_mm / d.ancho_mm;
            boton.classList.toggle('activo', d.candado);
            boton.innerHTML = `<i class="bi bi-${d.candado ? 'lock-fill' : 'unlock'}"></i>`;
            guardar();
        } else if (boton.dataset.accion === 'quitar') {
            estado.disenos = estado.disenos.filter((x) => x.id !== d.id);
            renderDisenos();
            cambio();
        }
    });

    function nuevoDiseno(registro) {
        return Object.assign({}, AJUSTE_DEFECTO, {
            id: registro.id,
            nombre: registro.nombre,
            url_mini: registro.url_mini,
            contenido_px: registro.contenido_px,
            tiene_alfa: !!registro.tiene_alfa,
            ancho_mm: registro.ancho_mm,
            alto_mm: registro.alto_mm,
            proporcion: registro.alto_mm / registro.ancho_mm,
        });
    }

    // ---------------------------------------------------------------- render: resultado

    const btnAvisos = $('#btn-avisos');
    const popAvisos = new bootstrap.Popover(btnAvisos, {
        html: true, trigger: 'click', placement: 'bottom', content: 'Sin avisos', customClass: 'popover-avisos',
    });
    document.addEventListener('click', (e) => {
        if (btnAvisos.contains(e.target) || e.target.closest('.popover-avisos')) return;
        popAvisos.hide();
    });

    function etiquetaHojas(numeros) {
        if (!numeros || !numeros.length) return 'Sin hojas';
        if (numeros.length === 1) return `Hoja ${numeros[0]}`;
        const consecutivas = numeros.every((n, i) => i === 0 || n === numeros[i - 1] + 1);
        const rango = consecutivas ? `${numeros[0]}–${numeros[numeros.length - 1]}` : numeros.join(', ');
        return `Hojas ${rango} (idénticas)`;
    }

    function renderVista() {
        const vistas = (resultado && resultado.vistas) || [];
        const total = vistas.length;
        $('#btn-vista-anterior').disabled = estado.vista <= 0 || total === 0;
        $('#btn-vista-siguiente').disabled = estado.vista >= total - 1 || total === 0;
        if (!estado.disenos.length) {
            previewSvg.innerHTML = '<div class="preview-vacio"><i class="bi bi-grid-3x3-gap"></i>Agrega diseños para ver el acomodo</div>';
            $('#vista-etiqueta').textContent = 'Sin hojas';
            return;
        }
        if (!resultado) return;
        const vista = vistas[estado.vista];
        if (!vista) {
            previewSvg.innerHTML = '<div class="preview-vacio"><i class="bi bi-exclamation-circle"></i>Ningún sticker cabe en la hoja</div>';
            $('#vista-etiqueta').textContent = 'Sin hojas';
            return;
        }
        previewSvg.innerHTML = vista.svg;
        const svg = $('svg', previewSvg);
        if (svg) {
            svg.removeAttribute('width');
            svg.removeAttribute('height');
            if (!svg.getAttribute('preserveAspectRatio')) svg.setAttribute('preserveAspectRatio', 'xMidYMid meet');
        }
        const sufijo = total > 1 ? ` · ${estado.vista + 1} de ${total}` : '';
        $('#vista-etiqueta').textContent = etiquetaHojas(vista.hojas) + sufijo;
    }

    function renderAvisos() {
        const avisos = (resultado && resultado.avisos) || [];
        $('.n', btnAvisos).textContent = avisos.length;
        btnAvisos.classList.toggle('sin-avisos', !avisos.length);
        popAvisos.setContent({
            '.popover-body': avisos.length
                ? `<ul class="mb-0 ps-3">${avisos.map((a) => `<li class="mb-1">${esc(a)}</li>`).join('')}</ul>`
                : 'Sin avisos',
        });
    }

    function renderSugerida() {
        const sugerida = resultado && resultado.orientacion_sugerida;
        $('#kpi-orientacion').textContent = sugerida ? (sugerida === 'horizontal' ? 'Horizontal' : 'Vertical') : '—';
        const boton = $('#btn-aplicar-orientacion');
        boton.disabled = !sugerida || sugerida === estado.hoja.orientacion || caso() === 'plantilla';
        boton.dataset.orientacion = sugerida || '';
    }

    function renderResultado() {
        renderVista();
        const r = resultado;
        $('#kpi-total').textContent = r ? num(r.total, 0) : '—';
        $('#kpi-por-hoja').textContent = r ? num(r.por_pliego_max, 0) : '—';
        $('#kpi-hojas').textContent = r ? num((r.pliegos || []).length, 0) : '—';
        renderSugerida();
        renderAvisos();
        renderCorte();
        renderBotones();
    }

    function renderCorte() {
        const grupos = (resultado && resultado.grupos) || [];
        const etiqueta = (g) => `Corte · ${esc(g.etiqueta)}`;
        let html;
        if (grupos.length > 3) {
            html = `<div class="dropdown dropup">
                <button type="button" class="btn-pf dropdown-toggle" data-bs-toggle="dropdown" data-corte-menu aria-expanded="false"><i class="bi bi-scissors me-1"></i>Corte</button>
                <ul class="dropdown-menu dropdown-menu-end">${grupos.map((g) => `<li><button type="button" class="dropdown-item" data-exportar="corte" data-grupo="${g.indice}">${etiqueta(g)}</button></li>`).join('')}</ul>
            </div>`;
        } else {
            const lista = grupos.length ? grupos : [{ indice: 0, etiqueta: 'hoja 1' }];
            html = lista.map((g, i) => `<button type="button" class="${i === lista.length - 1 ? 'btn-pf' : 'btn-suave'}" data-exportar="corte" data-grupo="${g.indice}"><i class="bi bi-scissors me-1"></i>${etiqueta(g)}</button>`).join('');
        }
        $$('.pie-corte').forEach((c) => { c.innerHTML = html; });
    }

    function renderBotones() {
        const listo = resultadoValido && resultado && resultado.total > 0 && estado.disenos.length > 0;
        $$('[data-exportar], [data-imprimir], [data-corte-menu]').forEach((b) => {
            if (b.dataset.ocupado === '1') return;
            b.disabled = !listo;
        });
    }

    function textoAyuda() {
        if (caso() === 'plantilla') {
            const p = plantillaActual();
            const nombre = esc(p ? p.nombre : 'la plantilla');
            return '<ol class="mb-1 ps-3">'
                + '<li>Imprime el PDF de impresión.</li>'
                + '<li>En SignMaster: Archivo &gt; Importar &gt; PDF de corte del grupo.</li>'
                + `<li>Plantillas &gt; Aplicar plantilla de marcas &gt; ID de ${nombre} &gt; Plantilla de corte.</li>`
                + '</ol><small class="text-muted">El recuadro exterior es el marco de la plantilla: se corta al final y sirve como corte perimetral.</small>';
        }
        return '<ol class="mb-1 ps-3">'
            + '<li>Imprime el PDF de impresión con las marcas PrintFlow.</li>'
            + '<li>Importa en el programa de corte el PDF de corte del grupo.</li>'
            + '<li>Corta cada hoja leyendo las marcas de registro.</li>'
            + '</ol><small class="text-muted">Las hojas de un mismo grupo son idénticas y usan el mismo PDF de corte.</small>';
    }

    const popsAyuda = $$('[data-ayuda]').map((b) => new bootstrap.Popover(b, {
        html: true, trigger: 'click', placement: 'top', title: 'Cómo cortar', content: textoAyuda, customClass: 'popover-ayuda',
    }));
    document.addEventListener('click', (e) => {
        if (e.target.closest('[data-ayuda]') || e.target.closest('.popover-ayuda')) return;
        popsAyuda.forEach((p) => p.hide());
    });

    // ---------------------------------------------------------------- descargas

    async function descargar(boton) {
        if (boton.disabled) return;
        const original = boton.innerHTML;
        boton.dataset.ocupado = '1';
        boton.disabled = true;
        boton.innerHTML = `<span class="spinner-border spinner-border-sm me-1" role="status"></span>${esc(boton.textContent.trim())}`;
        try {
            const grupo = boton.dataset.grupo != null ? `?grupo=${encodeURIComponent(boton.dataset.grupo)}` : '';
            const respuesta = await api(`exportar/${boton.dataset.exportar}${grupo}`, { method: 'POST', json: payload() });
            const blob = await respuesta.blob();
            const nombre = nombreDeDisposicion(respuesta.headers.get('Content-Disposition')) || 'stickers';
            const url = URL.createObjectURL(blob);
            const enlace = document.createElement('a');
            enlace.href = url;
            enlace.download = nombre;
            document.body.appendChild(enlace);
            enlace.click();
            enlace.remove();
            setTimeout(() => URL.revokeObjectURL(url), 1500);
        } catch (e) {
            toast(e.message);
        } finally {
            boton.innerHTML = original;
            delete boton.dataset.ocupado;
            renderBotones();
        }
    }

    async function imprimir(boton) {
        if (boton.disabled) return;
        boton.dataset.ocupado = '1';
        boton.disabled = true;
        toast('En el diálogo elige Escala: Tamaño real (100 %)', 'info');
        try {
            const respuesta = await api('exportar/impresion', { method: 'POST', json: payload() });
            const url = URL.createObjectURL(await respuesta.blob());
            let marco = document.getElementById('pf-print-frame');
            if (!marco) {
                marco = document.createElement('iframe');
                marco.id = 'pf-print-frame';
                marco.style.cssText = 'position:fixed;right:0;bottom:0;width:0;height:0;border:0';
                document.body.appendChild(marco);
            }
            marco.onload = () => {
                try {
                    marco.contentWindow.focus();
                    marco.contentWindow.print();
                } catch (e) {
                    window.open(url, '_blank');
                }
            };
            marco.src = url;
            setTimeout(() => URL.revokeObjectURL(url), 60000);
        } catch (e) {
            toast(e.message);
        } finally {
            delete boton.dataset.ocupado;
            renderBotones();
        }
    }

    document.addEventListener('click', (e) => {
        const boton = e.target.closest('[data-exportar]');
        if (boton) descargar(boton);
        const botonImprimir = e.target.closest('[data-imprimir]');
        if (botonImprimir) imprimir(botonImprimir);
    });

    // ---------------------------------------------------------------- eventos: controles

    $$('input[name="tipo"]').forEach((r) => r.addEventListener('change', () => {
        estado.tipo = r.value;
        estado.vista = 0;
        renderModo();
        renderSugerida();
        cambio();
    }));

    $$('input[name="preset"]').forEach((r) => r.addEventListener('change', () => {
        if (r.value === 'personalizada' && estado.hoja.preset !== 'personalizada') {
            const [ancho, alto] = hojaDims();
            estado.hoja.ancho_mm = ancho;
            estado.hoja.alto_mm = alto;
            $('#hoja-ancho').value = ancho;
            $('#hoja-alto').value = alto;
        }
        estado.hoja.preset = r.value;
        renderModo();
        cambio();
    }));

    $$('#orientacion [data-orientacion]').forEach((b) => b.addEventListener('click', () => {
        aplicarOrientacion(b.dataset.orientacion);
    }));

    $('#btn-aplicar-orientacion').addEventListener('click', (e) => {
        const o = e.currentTarget.dataset.orientacion;
        if (o) aplicarOrientacion(o);
    });

    const efectos = {
        personalizada() {
            const h = estado.hoja;
            if (h.ancho_mm !== h.alto_mm) h.orientacion = h.ancho_mm > h.alto_mm ? 'horizontal' : 'vertical';
            renderOrientacion();
            renderSugerida();
        },
    };

    $$('[data-estado]').forEach((el) => {
        el.addEventListener('input', () => {
            let valor;
            if (el.type === 'checkbox') valor = el.checked;
            else if (el.type === 'number') {
                valor = parseFloat(el.value);
                if (!Number.isFinite(valor)) return;
            } else valor = el.value;
            escribirRuta(el.dataset.estado, valor);
            if (el.dataset.efecto && efectos[el.dataset.efecto]) efectos[el.dataset.efecto]();
            cambio();
        });
    });

    $('#sel-plantilla').addEventListener('change', (e) => {
        estado.plantilla = e.target.value || null;
        estado.vista = 0;
        renderModo();
        renderSugerida();
        cambio();
    });

    $('#btn-vista-anterior').addEventListener('click', () => {
        if (estado.vista > 0) {
            estado.vista -= 1;
            guardar();
            renderVista();
        }
    });

    $('#btn-vista-siguiente').addEventListener('click', () => {
        const total = ((resultado && resultado.vistas) || []).length;
        if (estado.vista < total - 1) {
            estado.vista += 1;
            guardar();
            renderVista();
        }
    });

    // ---------------------------------------------------------------- biblioteca

    const modalBib = document.getElementById('modal-biblioteca');
    const grid = $('#bib-grid');
    const inputArchivos = $('#bib-archivos');
    const zona = $('#zona-subida');
    let temporizadorBusqueda = null;

    $('#btn-abrir-biblioteca').addEventListener('click', () => {
        seleccion.clear();
        $('#bib-subidas').innerHTML = '';
        renderSeleccion();
        bootstrap.Modal.getOrCreateInstance(modalBib).show();
        cargarBiblioteca();
    });

    $('#bib-buscar').addEventListener('input', () => {
        clearTimeout(temporizadorBusqueda);
        temporizadorBusqueda = setTimeout(cargarBiblioteca, 250);
    });
    $('#bib-orden').addEventListener('change', cargarBiblioteca);

    async function cargarBiblioteca() {
        const id = ++bibPeticion;
        const params = new URLSearchParams({ buscar: $('#bib-buscar').value.trim(), orden: $('#bib-orden').value });
        try {
            const datos = await apiJson(`biblioteca?${params}`);
            if (id !== bibPeticion) return;
            datos.disenos.forEach((r) => bibCache.set(r.id, r));
            renderGrid(datos.disenos);
        } catch (e) {
            if (id === bibPeticion) toast(e.message);
        }
    }

    function renderGrid(registros) {
        $('#bib-vacia').hidden = registros.length > 0;
        const enTrabajo = new Set(estado.disenos.map((d) => d.id));
        grid.innerHTML = registros.map((r) => {
            const usado = enTrabajo.has(r.id);
            const usos = Number(r.usos || 0);
            return `
            <div class="bib-card${seleccion.has(r.id) ? ' sel' : ''}${usado ? ' en-trabajo' : ''}" data-id="${esc(r.id)}">
                <div class="bib-mini"><img src="${esc(r.url_mini)}" alt="" loading="lazy"></div>
                <span class="bib-check"><i class="bi bi-check-lg"></i></span>
                <div class="bib-acciones">
                    <button type="button" data-accion="renombrar" title="Renombrar"><i class="bi bi-pencil"></i></button>
                    <button type="button" class="borrar" data-accion="borrar" title="Borrar"><i class="bi bi-trash"></i></button>
                </div>
                <div class="bib-nombre text-truncate" title="${esc(r.nombre)}">${esc(r.nombre)}</div>
                <div class="small text-muted text-truncate">${usado ? 'En el trabajo' : `${usos} ${usos === 1 ? 'uso' : 'usos'}`}</div>
            </div>`;
        }).join('');
    }

    function renderSeleccion() {
        const boton = $('#btn-agregar-seleccion');
        $('.n', boton).textContent = seleccion.size;
        boton.disabled = seleccion.size === 0;
    }

    grid.addEventListener('click', async (e) => {
        const tarjeta = e.target.closest('.bib-card');
        if (!tarjeta) return;
        const id = tarjeta.dataset.id;
        const registro = bibCache.get(id);
        const accion = e.target.closest('[data-accion]');
        if (accion) {
            e.stopPropagation();
            if (accion.dataset.accion === 'renombrar') await renombrar(registro);
            else await borrar(registro);
            return;
        }
        if (tarjeta.classList.contains('en-trabajo')) return;
        if (seleccion.has(id)) seleccion.delete(id);
        else seleccion.add(id);
        tarjeta.classList.toggle('sel', seleccion.has(id));
        renderSeleccion();
    });

    async function renombrar(registro) {
        if (!registro) return;
        const nombre = window.prompt('Nuevo nombre del diseño:', registro.nombre);
        if (nombre == null || !nombre.trim() || nombre.trim() === registro.nombre) return;
        try {
            const datos = await apiJson(`biblioteca/${encodeURIComponent(registro.id)}`, {
                method: 'PATCH', json: { nombre: nombre.trim() },
            });
            bibCache.set(datos.diseno.id, datos.diseno);
            const d = disenoPorId(registro.id);
            if (d) {
                d.nombre = datos.diseno.nombre;
                guardar();
                const fila = $$('.diseno-fila', lista).find((f) => f.dataset.id === d.id);
                if (fila) {
                    const el = $('.nombre', fila);
                    el.textContent = d.nombre;
                    el.title = d.nombre;
                }
            }
            cargarBiblioteca();
        } catch (e) {
            toast(e.message);
        }
    }

    async function borrar(registro) {
        if (!registro) return;
        if (!window.confirm(`¿Borrar "${registro.nombre}" de la biblioteca? Esta acción no se puede deshacer.`)) return;
        try {
            await apiJson(`biblioteca/${encodeURIComponent(registro.id)}`, { method: 'DELETE' });
            bibCache.delete(registro.id);
            seleccion.delete(registro.id);
            renderSeleccion();
            if (disenoPorId(registro.id)) {
                estado.disenos = estado.disenos.filter((d) => d.id !== registro.id);
                renderDisenos();
                cambio();
            }
            toast('Diseño borrado de la biblioteca.', 'success');
            cargarBiblioteca();
        } catch (e) {
            toast(e.message);
        }
    }

    $('#btn-agregar-seleccion').addEventListener('click', () => {
        const existentes = new Set(estado.disenos.map((d) => d.id));
        let agregados = 0;
        seleccion.forEach((id) => {
            const registro = bibCache.get(id);
            if (!registro || existentes.has(id)) return;
            estado.disenos.push(nuevoDiseno(registro));
            agregados += 1;
        });
        seleccion.clear();
        renderSeleccion();
        bootstrap.Modal.getOrCreateInstance(modalBib).hide();
        if (agregados) {
            renderDisenos();
            cambio();
        }
    });

    zona.addEventListener('click', () => inputArchivos.click());
    zona.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            inputArchivos.click();
        }
    });
    ['dragenter', 'dragover'].forEach((tipo) => zona.addEventListener(tipo, (e) => {
        e.preventDefault();
        zona.classList.add('arrastrando');
    }));
    ['dragleave', 'drop'].forEach((tipo) => zona.addEventListener(tipo, (e) => {
        e.preventDefault();
        zona.classList.remove('arrastrando');
    }));
    zona.addEventListener('drop', (e) => subirArchivos(Array.from(e.dataTransfer.files || [])));
    inputArchivos.addEventListener('change', () => {
        subirArchivos(Array.from(inputArchivos.files || []));
        inputArchivos.value = '';
    });

    function itemSubida(nombre) {
        const el = document.createElement('div');
        el.className = 'subida-item';
        el.innerHTML = `<div class="d-flex justify-content-between gap-2"><span class="text-truncate">${esc(nombre)}</span><span class="estado text-muted">0%</span></div>`
            + '<div class="progress mt-1"><div class="progress-bar" style="width:0%"></div></div>';
        $('#bib-subidas').appendChild(el);
        return el;
    }

    function progresoSubida(el, porcentaje) {
        $('.progress-bar', el).style.width = `${porcentaje}%`;
        $('.estado', el).textContent = `${Math.round(porcentaje)}%`;
    }

    function finSubida(el, clase, texto) {
        progresoSubida(el, 100);
        const estadoEl = $('.estado', el);
        estadoEl.className = `estado ${clase}`;
        estadoEl.textContent = texto;
        if (clase === 'text-danger') $('.progress-bar', el).style.background = '#dc3545';
        if (clase === 'text-warning') $('.progress-bar', el).style.background = '#f0ad4e';
    }

    function subirArchivo(archivo) {
        const el = itemSubida(archivo.name);
        return new Promise((resolve) => {
            const xhr = new XMLHttpRequest();
            xhr.open('POST', API + 'biblioteca');
            xhr.setRequestHeader('X-Requested-With', 'XMLHttpRequest');
            xhr.upload.addEventListener('progress', (e) => {
                if (e.lengthComputable) progresoSubida(el, (e.loaded / e.total) * 95);
            });
            xhr.addEventListener('load', () => {
                if (xhr.status === 413) {
                    finSubida(el, 'text-danger', 'El archivo supera el límite de subida');
                    toast('El archivo supera el límite de subida');
                    resolve(null);
                    return;
                }
                let datos = null;
                try { datos = JSON.parse(xhr.responseText); } catch (e) { /* sin JSON */ }
                if (xhr.status < 200 || xhr.status >= 300 || !datos || datos.success === false) {
                    const mensaje = (datos && datos.error) || `Error ${xhr.status}`;
                    finSubida(el, 'text-danger', mensaje);
                    toast(`${archivo.name}: ${mensaje}`);
                    resolve(null);
                    return;
                }
                if (datos.errores && datos.errores.length) {
                    finSubida(el, 'text-danger', datos.errores[0].error);
                    resolve(null);
                    return;
                }
                const registro = (datos.agregados || [])[0];
                if (!registro) {
                    finSubida(el, 'text-danger', 'No se pudo subir');
                    resolve(null);
                    return;
                }
                bibCache.set(registro.id, registro);
                if (registro.duplicado) finSubida(el, 'text-warning', 'Ya estaba en la biblioteca');
                else finSubida(el, 'text-success', 'Subido');
                resolve(registro);
            });
            xhr.addEventListener('error', () => {
                finSubida(el, 'text-danger', 'Error de red');
                resolve(null);
            });
            const datos = new FormData();
            datos.append('archivos', archivo, archivo.name);
            xhr.send(datos);
        });
    }

    async function subirArchivos(archivos) {
        if (!archivos.length) return;
        const enTrabajo = new Set(estado.disenos.map((d) => d.id));
        for (const archivo of archivos) {
            const registro = await subirArchivo(archivo);
            if (registro && !enTrabajo.has(registro.id)) seleccion.add(registro.id);
        }
        renderSeleccion();
        cargarBiblioteca();
    }

    // ---------------------------------------------------------------- plantillas

    const modalPl = document.getElementById('modal-plantillas');
    const listaPl = $('#lista-plantillas');

    async function recargarPlantillas(avisar) {
        try {
            const datos = await apiJson('plantillas');
            plantillasLista = datos.plantillas || [];
        } catch (e) {
            plantillasLista = [];
            toast(e.message);
        }
        if (estado.plantilla && !plantillasLista.some((p) => p.slug === estado.plantilla && p.valida)) {
            if (avisar !== false) toast('La plantilla seleccionada ya no está disponible; se usarán las marcas PrintFlow.', 'warning');
            estado.plantilla = null;
            cambio();
        }
        renderSelectPlantillas();
        renderModo();
        renderListaPlantillas();
    }

    function renderListaPlantillas() {
        if (!plantillasLista.length) {
            listaPl.innerHTML = '<div class="text-center text-muted py-3">No hay plantillas.</div>';
            return;
        }
        listaPl.innerHTML = plantillasLista.map((p, i) => {
            const area = p.area || [0, 0, 0, 0];
            const estadoHtml = p.valida
                ? '<span class="badge rounded-pill text-bg-success">Válida</span>'
                : `<span class="badge rounded-pill text-bg-danger">Error</span><div class="small text-danger mt-1">${esc(p.error || 'Plantilla no válida')}</div>`;
            const campos = ['x0', 'y0', 'x1', 'y1'].map((c, j) => `
                <div class="col-6 col-md-2">
                    <div class="st-label">${c} (mm)</div>
                    <input type="number" class="form-control" step="0.1" min="0" data-area="${c}" value="${area[j]}">
                </div>`).join('');
            return `
            <div class="pl-item" data-slug="${esc(p.slug)}">
                <div class="d-flex align-items-start gap-2">
                    <div class="flex-grow-1 min-w-0">
                        <div class="fw-semibold text-truncate">${esc(p.nombre)}</div>
                        <div class="small text-muted">${esc(HOJAS_FISICAS[p.hoja_fisica] || p.hoja_fisica || '—')} · ${num(p.ancho_mm)} × ${num(p.alto_mm)} mm</div>
                        <div class="mt-1">${estadoHtml}</div>
                    </div>
                    <button type="button" class="btn-suave btn-sm" data-bs-toggle="collapse" data-bs-target="#pl-area-${i}" aria-expanded="false">Corregir área</button>
                    <button type="button" class="btn-icono" data-accion="borrar" title="Borrar"><i class="bi bi-trash"></i></button>
                </div>
                <div class="collapse" id="pl-area-${i}">
                    <div class="row g-2 align-items-end pt-3">
                        ${campos}
                        <div class="col-12 col-md-4"><button type="button" class="btn-pf w-100" data-accion="area">Guardar área</button></div>
                    </div>
                </div>
            </div>`;
        }).join('');
    }

    listaPl.addEventListener('click', async (e) => {
        const boton = e.target.closest('[data-accion]');
        if (!boton) return;
        const item = boton.closest('.pl-item');
        const slug = item.dataset.slug;
        const plantilla = plantillasLista.find((p) => p.slug === slug);
        if (boton.dataset.accion === 'borrar') {
            if (!window.confirm(`¿Borrar la plantilla "${plantilla ? plantilla.nombre : slug}"?`)) return;
            try {
                await apiJson(`plantillas/${encodeURIComponent(slug)}`, { method: 'DELETE' });
                toast('Plantilla borrada.', 'success');
                await recargarPlantillas(false);
            } catch (err) {
                toast(err.message);
            }
        } else if (boton.dataset.accion === 'area') {
            const cuerpo = {};
            for (const input of $$('[data-area]', item)) {
                const valor = parseFloat(input.value);
                if (!Number.isFinite(valor)) {
                    toast(`Valor no válido en ${input.dataset.area}.`);
                    return;
                }
                cuerpo[input.dataset.area] = valor;
            }
            boton.disabled = true;
            try {
                await apiJson(`plantillas/${encodeURIComponent(slug)}/area`, { method: 'POST', json: cuerpo });
                toast('Área actualizada.', 'success');
                await recargarPlantillas(false);
                if (estado.plantilla === slug) programarCalculo();
            } catch (err) {
                toast(err.message);
            } finally {
                boton.disabled = false;
            }
        }
    });

    $('#pl-archivo').addEventListener('change', (e) => {
        const archivo = e.target.files[0];
        const nombre = $('#pl-nombre');
        if (archivo && !nombre.value.trim()) nombre.value = archivo.name.replace(/\.pdf$/i, '');
    });

    $('#form-plantilla').addEventListener('submit', async (e) => {
        e.preventDefault();
        const form = e.currentTarget;
        const boton = $('#btn-subir-plantilla');
        const original = boton.innerHTML;
        boton.disabled = true;
        boton.innerHTML = '<span class="spinner-border spinner-border-sm"></span>';
        try {
            const datos = await apiJson('plantillas', { method: 'POST', body: new FormData(form) });
            form.reset();
            if (datos.plantilla && datos.plantilla.valida) toast('Plantilla subida.', 'success');
            else toast((datos.plantilla && datos.plantilla.error) || 'La plantilla se guardó pero no es válida.', 'warning');
            await recargarPlantillas(false);
        } catch (err) {
            toast(err.message);
        } finally {
            boton.disabled = false;
            boton.innerHTML = original;
        }
    });

    modalPl.addEventListener('show.bs.modal', () => recargarPlantillas(false));

    // ---------------------------------------------------------------- inicio

    async function validarDisenos() {
        if (!estado.disenos.length) return;
        let registros;
        try {
            registros = (await apiJson('biblioteca')).disenos || [];
        } catch (e) {
            toast(e.message);
            return;
        }
        const porId = new Map(registros.map((r) => [r.id, r]));
        registros.forEach((r) => bibCache.set(r.id, r));
        const quitados = estado.disenos.filter((d) => !porId.has(d.id));
        estado.disenos = estado.disenos.filter((d) => porId.has(d.id)).map((d) => {
            const r = porId.get(d.id);
            const actualizado = Object.assign(d, {
                nombre: r.nombre, url_mini: r.url_mini, contenido_px: r.contenido_px, tiene_alfa: !!r.tiene_alfa,
            });
            if (actualizado.forma === 'silueta' && !actualizado.tiene_alfa) actualizado.forma = 'rectangulo';
            return actualizado;
        });
        if (quitados.length) {
            const nombres = quitados.map((d) => d.nombre || d.id).join(', ');
            toast(`Se quitaron del trabajo ${quitados.length === 1 ? 'un diseño que ya no existe' : `${quitados.length} diseños que ya no existen`} en la biblioteca: ${nombres}.`, 'warning');
        }
        guardar();
        renderDisenos();
    }

    async function iniciar() {
        renderControles();
        renderModo();
        renderDisenos();
        renderResultado();
        await Promise.all([recargarPlantillas(true), validarDisenos()]);
        renderSelectPlantillas();
        renderModo();
        programarCalculo();
    }

    iniciar();
})();
