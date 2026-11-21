// Actualiza el tablero sin recargar: pide a /dashboard/cambios lo que cambió desde
// la última versión y reemplaza solo esas tarjetas.
(function () {
  const urlCambios = document.currentScript.dataset.urlCambios;
  const urlEntregados = document.currentScript.dataset.urlEntregados;
  const INTERVALO_MS = 3000;
  const ESPERA_MAXIMA_MS = 30000;
  const TIEMPO_LIMITE_MS = 10000;
  const MAX_PAGINAS_SIN_VISIBLES = 5;   // con filtros activos, páginas seguidas que no aportan tarjetas visibles
  const DURACION_RESALTADO_MS = 2000;
  const CABECERAS = { 'X-Requested-With': 'XMLHttpRequest' };
  const ERROR_CAMBIO_ESTADO = 'No se pudo cambiar el estado. Intenta de nuevo.';

  let version = parseInt(document.body.dataset.versionTablero, 10) || 0;
  let espera = INTERVALO_MS;
  let temporizador = null;
  let consultando = false;
  let repetirConsulta = false;
  let detenido = false;

  const pendientes = new Map();   // id de pedido -> función que aplica su último cambio retenido
  const pendientesHistorial = new Map();   // igual, para las tarjetas de la sección de historial
  const forzadas = new Set();     // pedidos que el propio usuario acaba de cambiar de estado
  let folioPorPintar = null;      // folio sugerido en espera de que se cierre "Crear nota"

  // ------------------------------------------------------------ DOM
  // Solo el tablero: la sección de historial se maneja aparte y sus tarjetas no cambian de lugar.
  const tarjetaPorId = id =>
    document.querySelector(`.columna-kanban .card-pedido[data-id-pedido="${Number(id)}"]`);
  const contenedorDe = columna =>
    document.querySelector(`.columna-kanban[data-col="${columna}"] .contenedor-tarjetas`);
  const contenedorHistorial = document.getElementById('historialTarjetas');
  const tarjetaHistorial = id =>
    contenedorHistorial?.querySelector(`.card-pedido[data-id-pedido="${Number(id)}"]`);

  function idEnEdicion() {
    const modal = document.getElementById('modalEditarPedido');
    if (!modal || !modal.classList.contains('show')) return null;
    const campo = modal.querySelector('input[name="pedido_id"]');
    return campo ? Number(campo.value) : null;
  }

  function protegida(tarjeta) {
    if (tarjeta.querySelector('.card-extra.show, .card-extra.collapsing')) return true;
    if (Number(tarjeta.dataset.idPedido) === idEnEdicion()) return true;
    // La flecha conserva el foco después de cerrar la tarjeta; eso ya no es estar trabajando en ella.
    const activo = document.activeElement;
    return tarjeta.contains(activo) && !activo.matches('.btn-expandir');
  }

  function retirar(tarjeta) {
    const extra = tarjeta.querySelector('.card-extra');
    bootstrap.Collapse.getInstance(extra)?.dispose();
    tarjeta.remove();
  }

  // ------------------------------------------------------------ orden por columna
  const ORDEN_PREDETERMINADO = {
    'en-proceso': 'entrega', terminado: 'espera', entregado: 'reciente',
    // Tablero de Producción (columnas por fecha)
    vencidos: 'entrega', hoy: 'entrega', proximos: 'entrega', 'terminados-hoy': 'terminado-reciente',
  };
  const clavesOrden = {
    entrega: r => [r.orden],
    espera: r => [r.terminado],
    'terminado-reciente': r => [r.terminado],
    folio: r => { const m = /^(\D*)(\d*)/.exec(r.folio); return [m[1], Number(m[2] || 0), r.folio]; },
    reciente: r => [r.entregado],
    antiguo: r => [r.entregado],
  };
  const ordenActual = { ...ORDEN_PREDETERMINADO };
  const claveAlmacen = columna => `tablero_orden_${columna}`;

  const registro = t => ({
    id: Number(t.dataset.idPedido), orden: t.dataset.orden, entregado: t.dataset.entregado, terminado: t.dataset.terminado, folio: t.dataset.folio,
  });

  // Negativo si `a` va antes que `b` con el orden de la columna; el id desempata (igual que el servidor).
  function comparar(a, b, orden) {
    const ka = clavesOrden[orden](a);
    const kb = clavesOrden[orden](b);
    let c = 0;
    for (let i = 0; i < ka.length && !c; i++) c = ka[i] < kb[i] ? -1 : ka[i] > kb[i] ? 1 : 0;
    if (!c) c = a.id - b.id;
    return orden === 'reciente' || orden === 'terminado-reciente' ? -c : c;
  }

  const tarjetasDe = contenedor => [...contenedor.querySelectorAll(':scope > .card-pedido')];

  function insertarOrdenada(nueva, contenedor, columna) {
    const reg = registro(nueva);
    const orden = ordenActual[columna];
    const siguiente = tarjetasDe(contenedor).find(t => comparar(registro(t), reg, orden) > 0);
    if (siguiente) siguiente.before(nueva);
    else if (columna === 'entregado') entregados.final.before(nueva);
    else contenedor.append(nueva);
  }

  function ordenarColumna(columna) {
    const contenedor = contenedorDe(columna);
    const orden = ordenActual[columna];
    tarjetasDe(contenedor)
      .sort((a, b) => comparar(registro(a), registro(b), orden))
      .forEach(t => contenedor.append(t));
    contenedor.scrollTop = 0;
  }

  // ------------------------------------------------------------ Entregado paginado
  const contenedorEntregados = contenedorDe('entregado');
  const entregados = {
    cursor: null,        // { entregado, id } de la última tarjeta cargada
    hayMas: false,
    cargando: false,
    pausado: false,      // sin carga automática: buscador con texto o muchas páginas sin tarjetas visibles
    sinVisibles: 0,
    controlador: null,
    final: contenedorEntregados?.querySelector('.entregados-mas'),
    boton: document.getElementById('entregadosMasBoton'),
  };
  const estadoEntregados = entregados.final?.querySelector('.entregados-mas-estado');
  const buscador = document.getElementById('buscadorGeneral');

  function publicarEntregados(total, hayMas) {
    if (total !== undefined) contenedorEntregados.dataset.total = total;
    if (hayMas !== undefined) {
      entregados.hayMas = hayMas;
      contenedorEntregados.dataset.hayMas = hayMas ? '1' : '0';
    }
  }

  function fijarCursor() {
    const ultima = tarjetasDe(contenedorEntregados).pop();
    entregados.cursor = ultima ? { entregado: ultima.dataset.entregado, id: Number(ultima.dataset.idPedido) } : null;
  }

  // ¿Cae la tarjeta dentro de lo ya cargado? Si no, llegará con la paginación.
  function dentroDeLoCargado(nueva) {
    if (!entregados.hayMas) return true;
    if (!entregados.cursor) return false;
    return comparar(registro(nueva), { id: entregados.cursor.id, entregado: entregados.cursor.entregado }, ordenActual.entregado) <= 0;
  }

  function mostrarPie() {
    if (!entregados.final) return;
    entregados.final.hidden = !entregados.hayMas && !entregados.cargando;
    estadoEntregados.textContent = entregados.cargando ? 'Cargando…' : '';
    entregados.boton.hidden = !(entregados.hayMas && entregados.pausado && !entregados.cargando);
  }

  const observadorFinal = 'IntersectionObserver' in window && entregados.final
    ? new IntersectionObserver(entradas => {
      if (entradas.some(e => e.isIntersecting)) cargarEntregados(false);
    })
    : null;

  // Volver a observar vuelve a avisar si el pie sigue a la vista (la columna aún no se llena).
  function vigilarFinal() {
    if (!observadorFinal) return;
    observadorFinal.unobserve(entregados.final);
    if (entregados.hayMas && !entregados.pausado) observadorFinal.observe(entregados.final);
  }

  async function cargarEntregados(manual, reiniciar = false) {
    if (!contenedorEntregados || (!reiniciar && (entregados.cargando || !entregados.hayMas))) return;
    if (!manual && !reiniciar && buscador?.value.trim()) {   // con búsqueda, el historial ya cubre todo
      entregados.pausado = true;
      mostrarPie();
      return;
    }
    entregados.controlador?.abort();
    const controlador = new AbortController();
    entregados.controlador = controlador;
    entregados.cargando = true;
    entregados.pausado = false;
    mostrarPie();

    const parametros = new URLSearchParams({ orden: ordenActual.entregado });
    if (!reiniciar && entregados.cursor) {
      parametros.set('despues_orden', entregados.cursor.entregado);
      parametros.set('despues_id', entregados.cursor.id);
    }
    try {
      const respuesta = await fetch(`${urlEntregados}?${parametros}`, {
        headers: CABECERAS, cache: 'no-store', signal: controlador.signal,
      });
      if (respuesta.redirected) {
        location.reload();
        return;
      }
      if (!respuesta.ok) throw new Error(`HTTP ${respuesta.status}`);
      const datos = await respuesta.json();
      if (entregados.controlador !== controlador) return;

      const nuevas = [];
      datos.tarjetas.forEach(({ id, html }) => {
        const previa = tarjetaPorId(id);
        if (previa && contenedorEntregados.contains(previa)) return;   // la sincronización ya la trajo
        if (previa) retirar(previa);
        const tarjeta = tarjetaDesdeHtml(html);
        if (!tarjeta) return;
        entregados.final.before(tarjeta);
        nuevas.push(tarjeta);
      });
      const ultima = datos.tarjetas[datos.tarjetas.length - 1];
      if (ultima) entregados.cursor = { entregado: ultima.orden, id: ultima.id };
      publicarEntregados(datos.total, datos.hay_mas);
      entregados.cargando = false;
      if (nuevas.length) window.actualizarFechasTarjetas?.(nuevas);
      terminarAplicacion();

      const hayVisible = nuevas.some(t => t.style.display !== 'none');
      entregados.sinVisibles = hayVisible || !nuevas.length ? 0 : entregados.sinVisibles + 1;
      if (entregados.sinVisibles >= MAX_PAGINAS_SIN_VISIBLES) {
        entregados.pausado = true;
        entregados.sinVisibles = 0;
      }
    } catch (error) {
      if (error.name === 'AbortError') return;
      entregados.pausado = true;   // se reintenta con el botón
      estadoEntregados.textContent = 'No se pudo cargar. Intenta de nuevo.';
    } finally {
      if (entregados.controlador === controlador) {
        entregados.cargando = false;
        entregados.controlador = null;
        const aviso = estadoEntregados.textContent;
        mostrarPie();
        if (aviso.startsWith('No se pudo')) estadoEntregados.textContent = aviso;
        vigilarFinal();
      }
    }
  }

  function reiniciarEntregados() {
    tarjetasDe(contenedorEntregados).forEach(retirar);
    entregados.cursor = null;
    entregados.sinVisibles = 0;
    publicarEntregados(undefined, true);
    contenedorEntregados.scrollTop = 0;
    cargarEntregados(true, true);
  }

  // ------------------------------------------------------------ elegir el orden
  function cambiarOrden(columna, valor, guardar) {
    ordenActual[columna] = valor;
    if (guardar) {
      try { localStorage.setItem(claveAlmacen(columna), valor); } catch (e) { /* sin almacenamiento: no se recuerda */ }
    }
    if (columna === 'entregado') reiniciarEntregados();
    else {
      ordenarColumna(columna);
      window.aplicarFiltrosTablero?.();
    }
  }

  function iniciarOrden() {
    // Primero Entregado: un orden guardado distinto del predeterminado lo reinicia desde el servidor.
    if (contenedorEntregados) {
      fijarCursor();
      publicarEntregados(undefined, contenedorEntregados.dataset.hayMas === '1');
      entregados.boton?.addEventListener('click', () => cargarEntregados(true));
      buscador?.addEventListener('input', () => {
        if (!buscador.value.trim() && entregados.pausado) {
          entregados.pausado = false;
          mostrarPie();
          vigilarFinal();
        }
      });
      mostrarPie();
      vigilarFinal();
    }
    document.querySelectorAll('.orden-columna').forEach(select => {
      const columna = select.dataset.col;
      let guardado = null;
      try { guardado = localStorage.getItem(claveAlmacen(columna)); } catch (e) { /* se usa el predeterminado */ }
      const valido = [...select.options].some(o => o.value === guardado);
      select.value = valido ? guardado : ORDEN_PREDETERMINADO[columna];
      select.addEventListener('change', () => cambiarOrden(columna, select.value, true));
      if (valido && guardado !== ORDEN_PREDETERMINADO[columna]) cambiarOrden(columna, guardado, false);
    });
  }
  document.addEventListener('DOMContentLoaded', iniciarOrden);

  // ------------------------------------------------------------ el scroll no se mueve
  // Una columna con scroll (escritorio) o la página (móvil) conserva lo que el usuario ve:
  // se ancla a las primeras tarjetas visibles y se corrige el desplazamiento tras los cambios.
  function vistaDe(contenedor) {
    if (!contenedor.offsetParent) return null;   // columna oculta
    const propio = getComputedStyle(contenedor).overflowY === 'auto';
    if (propio) {
      const caja = contenedor.getBoundingClientRect();
      return { arriba: caja.top, abajo: caja.bottom, scroll: contenedor.scrollTop, mover: d => { contenedor.scrollTop += d; } };
    }
    return { arriba: 0, abajo: window.innerHeight, scroll: window.scrollY, mover: d => window.scrollBy(0, d) };
  }

  function capturarScroll() {
    return [...document.querySelectorAll('.columna-kanban .contenedor-tarjetas')].map(contenedor => {
      const vista = vistaDe(contenedor);
      if (!vista || vista.scroll <= 0) return null;   // al inicio de la lista no hay nada que conservar
      const anclas = [];
      for (const t of tarjetasDe(contenedor)) {
        if (!t.offsetParent) continue;
        const caja = t.getBoundingClientRect();
        if (caja.bottom <= vista.arriba) continue;
        if (caja.top >= vista.abajo || anclas.length >= 5) break;
        anclas.push({ id: t.dataset.idPedido, distancia: caja.top - vista.arriba });
      }
      return { contenedor, anclas };
    });
  }

  function restaurarScroll(estados) {
    estados.forEach(estado => {
      if (!estado) return;
      const vista = vistaDe(estado.contenedor);
      if (!vista) return;
      for (const { id, distancia } of estado.anclas) {
        const tarjeta = estado.contenedor.querySelector(`:scope > .card-pedido[data-id-pedido="${Number(id)}"]`);
        if (!tarjeta || !tarjeta.offsetParent) continue;
        const delta = (tarjeta.getBoundingClientRect().top - vista.arriba) - distancia;
        if (delta) vista.mover(delta);
        return;
      }
    });
  }

  function conservandoScroll(cambio) {
    const estados = capturarScroll();
    try {
      cambio();
    } finally {
      restaurarScroll(estados);
    }
  }

  function resaltar(tarjeta) {
    tarjeta.classList.add('tarjeta-actualizada');
    setTimeout(() => tarjeta.classList.remove('tarjeta-actualizada'), DURACION_RESALTADO_MS);
  }

  function tarjetaDesdeHtml(html) {
    const plantilla = document.createElement('template');
    plantilla.innerHTML = html;
    return plantilla.content.querySelector('.card-pedido');
  }

  // Copia sin lo que el navegador agrega al usar la tarjeta: filtros, resaltado, Bootstrap.
  function sinEstadoDeInterfaz(tarjeta) {
    const copia = tarjeta.cloneNode(true);
    copia.classList.remove('tarjeta-actualizada');
    copia.removeAttribute('style');
    copia.className = [...copia.classList].join(' ');
    const flecha = copia.querySelector('.btn-expandir');
    if (flecha) {
      flecha.className = 'btn-expandir';
      flecha.setAttribute('aria-expanded', 'false');
    }
    const extra = copia.querySelector('.card-extra');
    if (extra) {
      extra.className = 'collapse card-extra';
      extra.removeAttribute('style');
    }
    copia.querySelectorAll('.wa-copy-status').forEach(estado => { estado.textContent = ''; });
    // El texto y el color de la fecha los calcula el navegador según la hora (actualizarFechasTarjetas);
    // el servidor manda otro valor inicial. No cuentan como cambio de la tarjeta (data-entrega sí).
    copia.querySelectorAll('.pc-fecha').forEach(el => el.classList.remove('pc-vencido', 'pc-hoy', 'pc-gris'));
    copia.querySelectorAll('.pc-fecha-txt').forEach(el => { el.textContent = ''; });
    return copia;
  }

  function tarjetaSinCambios({ id, columna, html }) {
    const actual = tarjetaPorId(id);
    const recibida = tarjetaDesdeHtml(html);
    return !!actual && !!recibida && contenedorDe(columna)?.contains(actual)
      && sinEstadoDeInterfaz(actual).isEqualNode(sinEstadoDeInterfaz(recibida));
  }

  // ------------------------------------------------------------ sección de historial
  const CLASE_ESTATUS = {
    'En proceso': 'est-proceso', Terminado: 'est-terminado', Entregado: 'est-entregado', Cancelado: 'est-cancelado',
  };

  function tarjetaParaHistorial(html) {
    const tarjeta = tarjetaDesdeHtml(html);
    if (!tarjeta) return null;
    // El mismo pedido puede estar también en el tablero: su colapsable necesita otro id.
    const extra = tarjeta.querySelector('.card-extra');
    const flecha = tarjeta.querySelector('.btn-expandir');
    if (extra && flecha) {
      extra.id = `historial-${extra.id}`;
      flecha.dataset.bsTarget = `#${extra.id}`;
      flecha.setAttribute('aria-controls', extra.id);
    }
    const etiqueta = document.createElement('span');
    etiqueta.className = `badge-estatus etiqueta-historial ${CLASE_ESTATUS[tarjeta.dataset.estatus] || ''}`;
    etiqueta.textContent = tarjeta.dataset.estatus;
    tarjeta.prepend(etiqueta);
    return tarjeta;
  }

  function vaciarHistorial() {
    pendientesHistorial.clear();
    contenedorHistorial?.querySelectorAll('.card-pedido').forEach(retirar);
  }

  window.pintarHistorial = tarjetas => {
    vaciarHistorial();
    const nuevas = [];
    tarjetas.forEach(({ html }) => {
      const tarjeta = tarjetaParaHistorial(html);
      if (tarjeta) {
        contenedorHistorial.appendChild(tarjeta);
        nuevas.push(tarjeta);
      }
    });
    if (nuevas.length) window.actualizarFechasTarjetas?.(nuevas);
  };
  window.vaciarHistorial = vaciarHistorial;

  function aplicarEnHistorial(id, nueva) {
    const actual = tarjetaHistorial(id);
    if (!actual) return;
    const teniaFoco = actual.contains(document.activeElement);
    actual.before(nueva);
    retirar(actual);
    window.actualizarFechasTarjetas?.([nueva]);
    if (teniaFoco) nueva.querySelector('.btn-expandir')?.focus({ preventScroll: true });
    resaltar(nueva);
  }

  function retenerEnHistorial(id, tarjeta, aplicar) {
    if (protegida(tarjeta) && !forzadas.has(id)) {
      pendientesHistorial.set(id, aplicar);
      return;
    }
    pendientesHistorial.delete(id);
    aplicar();
  }

  // El pedido se actualiza donde está; nunca entra ni sale de la sección por un cambio ajeno.
  function actualizarEnHistorial({ id, html }) {
    const actual = tarjetaHistorial(id);
    const nueva = actual && tarjetaParaHistorial(html);
    if (!nueva) return;
    if (sinEstadoDeInterfaz(actual).isEqualNode(sinEstadoDeInterfaz(nueva))) {
      pendientesHistorial.delete(id);   // repaso: ya la tiene igual
      return;
    }
    retenerEnHistorial(id, actual, () => aplicarEnHistorial(id, nueva));
  }

  function quitarDeHistorial(id) {
    const actual = tarjetaHistorial(id);
    if (actual) retenerEnHistorial(id, actual, () => { if (tarjetaHistorial(id)) retirar(tarjetaHistorial(id)); });
  }

  function pintarFolioSugerido() {
    const modal = document.getElementById('modalCrearNota');
    if (folioPorPintar === null || !modal || modal.classList.contains('show')) return;
    const folio = modal.querySelector('[data-folio-sugerido]');
    if (folio) folio.textContent = folioPorPintar;
    folioPorPintar = null;
  }

  function aplicarTarjeta({ id, columna, html }) {
    const nueva = tarjetaDesdeHtml(html);
    const contenedor = contenedorDe(columna);
    if (!nueva || !contenedor) return;

    const actual = tarjetaPorId(id);
    const teniaFoco = actual?.contains(document.activeElement);
    if (actual && contenedor.contains(actual) && comparar(registro(actual), registro(nueva), ordenActual[columna]) === 0) {
      actual.before(nueva);   // misma posición según el orden activo
      retirar(actual);
    } else {
      if (actual) retirar(actual);
      if (columna === 'entregado' && !dentroDeLoCargado(nueva)) return;
      insertarOrdenada(nueva, contenedor, columna);
    }
    window.actualizarFechasTarjetas?.([nueva]);   // solo la tarjeta que llegó
    if (teniaFoco) nueva.querySelector('.btn-expandir')?.focus({ preventScroll: true });
    resaltar(nueva);
  }

  function quitarTarjeta(id) {
    const tarjeta = tarjetaPorId(id);
    if (tarjeta) retirar(tarjeta);
  }

  function terminarAplicacion() {
    document.querySelectorAll('.contenedor-tarjetas').forEach(contenedor => {
      const vacio = contenedor.querySelector('.mensaje-columna-vacia');
      if (vacio) vacio.hidden = !!contenedor.querySelector('.card-pedido');
    });
    window.aplicarFiltrosTablero?.();   // filtros y chips, una sola vez por lote
  }

  // ------------------------------------------------------------ cambios recibidos
  function aplicarORetener(id, aplicar) {
    const tarjeta = tarjetaPorId(id);
    if (tarjeta && protegida(tarjeta) && !forzadas.has(id)) {
      pendientes.set(id, aplicar);
      return false;
    }
    forzadas.delete(id);
    pendientes.delete(id);
    aplicar();
    return true;
  }

  function aplicarPendientes() {
    pendientesHistorial.forEach((aplicar, id) => {
      const tarjeta = tarjetaHistorial(id);
      if (tarjeta && protegida(tarjeta)) return;
      pendientesHistorial.delete(id);
      aplicar();
    });
    let aplicados = false;
    conservandoScroll(() => {
      pendientes.forEach((aplicar, id) => {
        const tarjeta = tarjetaPorId(id);
        if (tarjeta && protegida(tarjeta)) return;
        pendientes.delete(id);
        aplicar();
        aplicados = true;
      });
      if (aplicados) terminarAplicacion();
    });
  }

  function recibirCambios(datos) {
    version = datos.version;
    // Ciclo vacío (lo normal): no hay nada que aplicar, ni recorrer el DOM ni recalcular chips.
    // Las fechas y el chip "Vencido" dependen de la hora y los refresca el temporizador de 60 s.
    if (!datos.tarjetas.length && !datos.quitar.length && !(datos.canceladas || []).length
        && typeof datos.total_entregados !== 'number' && typeof datos.por_cobrar !== 'number'
        && !datos.folio_sugerido) {
      window.pfContar?.('cicloVacioOmitido');
      return;
    }
    if (typeof datos.por_cobrar === 'number') window.actualizarPorCobrar?.(datos.por_cobrar);   // solo llega con caja.ver
    if (datos.folio_sugerido) {
      folioPorPintar = datos.folio_sugerido;
      pintarFolioSugerido();
    }

    // Antes que el tablero: aplicarORetener consume la marca de `forzadas`.
    const canceladas = datos.canceladas || [];
    const conHtml = new Set(canceladas.map(t => t.id));
    [...datos.tarjetas, ...canceladas].forEach(actualizarEnHistorial);
    datos.quitar.filter(id => !conHtml.has(id)).forEach(quitarDeHistorial);

    let aplicados = false;
    conservandoScroll(() => {
      datos.tarjetas.forEach(t => {
        if (tarjetaSinCambios(t)) pendientes.delete(t.id);   // repaso: ya la tiene igual
        else if (aplicarORetener(t.id, () => aplicarTarjeta(t))) aplicados = true;
      });
      datos.quitar.forEach(id => {
        if (tarjetaPorId(id) && aplicarORetener(id, () => quitarTarjeta(id))) aplicados = true;
      });
      if (typeof datos.total_entregados === 'number' && contenedorEntregados
          && String(datos.total_entregados) !== contenedorEntregados.dataset.total) {
        publicarEntregados(datos.total_entregados);
        aplicados = true;   // el badge de Entregado depende del total
      }
      if (aplicados) terminarAplicacion();   // repaso sin diferencias: no se recalcula nada
    });
  }

  // ------------------------------------------------------------ consulta periódica
  function programarSiguiente() {
    clearTimeout(temporizador);
    if (!detenido && !document.hidden) temporizador = setTimeout(consultar, espera);
  }

  async function pedirCambios() {
    window.pfContar?.('sondeoCambios');
    const controlador = new AbortController();
    const limite = setTimeout(() => controlador.abort(), TIEMPO_LIMITE_MS);
    try {
      const respuesta = await fetch(`${urlCambios}?desde=${version}`, {
        headers: CABECERAS, cache: 'no-store', signal: controlador.signal,
      });
      if (respuesta.redirected) {   // sesión vencida: al recargar, la guardia manda al login
        detenido = true;
        location.reload();
        return;
      }
      if (respuesta.status === 403) {   // sin permiso: reintentar no sirve; el envoltorio de base.html ya avisó
        detenido = true;
        return;
      }
      if (!respuesta.ok) throw new Error(`HTTP ${respuesta.status}`);
      const datos = await respuesta.json();
      if (datos.recargar) {
        detenido = true;
        window.pedirRecargaTablero();
        return;
      }
      recibirCambios(datos);
      espera = INTERVALO_MS;
    } catch (error) {
      espera = Math.min(espera * 2, ESPERA_MAXIMA_MS);   // sin avisar: se reintenta solo
    } finally {
      clearTimeout(limite);
    }
  }

  async function consultar() {
    if (detenido || document.hidden) return;
    if (consultando) {
      repetirConsulta = true;
      return;
    }
    clearTimeout(temporizador);
    consultando = true;
    await pedirCambios();
    consultando = false;
    if (repetirConsulta) {
      repetirConsulta = false;
      consultar();
      return;
    }
    programarSiguiente();
  }

  // ------------------------------------------------------------ cambio de estado propio
  // En captura: se adelanta al guardián de doble envío de base.html, que respeta defaultPrevented.
  document.addEventListener('submit', async evento => {
    const form = evento.target;
    if (!form.matches('.card-pedido form[action*="/cambiar-estado"]')) return;
    evento.preventDefault();
    if (form.dataset.enviando) return;
    form.dataset.enviando = '1';
    const botones = form.querySelectorAll('[type=submit]');
    botones.forEach(boton => { boton.disabled = true; });

    try {
      const respuesta = await fetch(form.action, { method: 'POST', body: new FormData(form), headers: CABECERAS });
      if (respuesta.redirected) {
        location.reload();
        return;
      }
      const datos = await respuesta.json().catch(() => ({}));
      if (datos.success) {
        forzadas.add(datos.pedido_id);   // está expandida, pero su dueño pidió moverla
        if (datos.url_ticket_entrega && window.imprimirTicket) window.imprimirTicket(datos.url_ticket_entrega);
      }
      else mostrarToastError(datos.error || ERROR_CAMBIO_ESTADO);
    } catch (error) {
      mostrarToastError(ERROR_CAMBIO_ESTADO);
    }
    delete form.dataset.enviando;
    botones.forEach(boton => { boton.disabled = false; });
    consultar();
  }, true);

  // ------------------------------------------------------------ arranque
  document.addEventListener('hidden.bs.collapse', aplicarPendientes);
  document.addEventListener('hidden.bs.modal', () => {
    aplicarPendientes();
    pintarFolioSugerido();
  });
  document.addEventListener('focusout', () => {
    if (pendientes.size) setTimeout(aplicarPendientes);   // ya con el nuevo elemento activo
  });
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) clearTimeout(temporizador);
    else consultar();
  });

  programarSiguiente();
})();
