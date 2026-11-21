// Impresión de etiquetas desde el tablero y la ficha del pedido.
// Requiere static/css/etiquetas.css para los avisos.

let avisoEtiquetaActual = null;
let avisoEtiquetaTimer = null;

function cerrarAvisoEtiqueta(aviso) {
  if (avisoEtiquetaActual === aviso) {
    clearTimeout(avisoEtiquetaTimer);
    avisoEtiquetaActual = null;
  }
  aviso.classList.remove('visible');
  setTimeout(() => aviso.remove(), 200);
}

function mostrarToastError(mensaje, clase = 'text-bg-danger') {
  let contenedor = document.getElementById('toastsEtiquetas');
  if (!contenedor) {
    contenedor = document.createElement('div');
    contenedor.id = 'toastsEtiquetas';
    contenedor.className = 'toast-container position-fixed top-0 end-0 p-3';
    contenedor.style.zIndex = '1090';
    document.body.appendChild(contenedor);
  }

  // Solo un aviso a la vez: el anterior se quita al instante
  clearTimeout(avisoEtiquetaTimer);
  if (avisoEtiquetaActual) avisoEtiquetaActual.remove();

  const colores = {
    'text-bg-primary': 'azul',
    'text-bg-warning': 'amarillo',
    'text-bg-success': 'verde',
    'text-bg-secondary': 'gris',
    'text-bg-danger': 'rojo'
  };
  const color = colores[clase] || 'rojo';

  const aviso = document.createElement('div');
  aviso.className = `aviso-etiqueta aviso-etiqueta--${color}`;
  aviso.setAttribute('role', 'alert');
  aviso.setAttribute('aria-live', 'assertive');
  aviso.setAttribute('aria-atomic', 'true');
  aviso.innerHTML =
    '<div class="aviso-etiqueta__texto"></div>' +
    `<button type="button" class="btn-close${color === 'amarillo' ? '' : ' btn-close-white'}" aria-label="Cerrar"></button>`;
  aviso.querySelector('.aviso-etiqueta__texto').textContent = mensaje;
  aviso.querySelector('.btn-close').addEventListener('click', () => cerrarAvisoEtiqueta(aviso));

  contenedor.appendChild(aviso);
  void aviso.offsetHeight;   // forzar reflow para que corra la transición de entrada
  aviso.classList.add('visible');

  avisoEtiquetaActual = aviso;
  avisoEtiquetaTimer = setTimeout(() => cerrarAvisoEtiqueta(aviso), 30000);
}

async function imprimirEtiquetaCard(btn) {
  const textoOriginal = btn.innerHTML;
  const estiloOriginal = btn.getAttribute('style');
  const restaurar = () => {
    if (estiloOriginal === null) btn.removeAttribute('style');
    else btn.setAttribute('style', estiloOriginal);
    btn.innerHTML = textoOriginal;
    btn.disabled = false;
  };
  const esperar = ms => new Promise(r => setTimeout(r, ms));
  const spinner = texto =>
    `<span class="spinner-border spinner-border-sm me-1"></span> ${texto}`;

  btn.disabled = true;
  btn.innerHTML = spinner('Enviando…');

  let folio = btn.dataset.folio || 'este pedido';
  mostrarToastError(`Enviando etiqueta de ${folio} a la impresora…`, 'text-bg-primary');
  let avisoEspera = false;
  try {
    const respuesta = await fetch(btn.dataset.url, {
      method: 'POST',
      headers: { 'X-Requested-With': 'XMLHttpRequest' }
    });
    const datos = await respuesta.json().catch(() => ({}));
    if (!respuesta.ok || !datos.success || !datos.trabajo?.id) {
      throw new Error(
        datos.error || `El servidor respondió con error HTTP ${respuesta.status}.`
      );
    }

    const idTrabajo = datos.trabajo.id;
    folio = datos.trabajo.folio || folio;
    btn.innerHTML = spinner('En espera…');

    const limite = Date.now() + 90000;
    while (Date.now() < limite) {
      await esperar(1500);

      let trabajo;
      try {
        const r = await fetch(`/pedidos/etiquetas/cola/${encodeURIComponent(idTrabajo)}`, {
          headers: { 'X-Requested-With': 'XMLHttpRequest' }
        });
        const d = await r.json().catch(() => ({}));
        if (r.status === 404) throw new Error('El trabajo de impresión ya no existe en la cola.');
        if (!r.ok || !d.success) continue;   // error temporal: seguir consultando
        trabajo = d.trabajo;
      } catch (e) {
        if (e.message.startsWith('El trabajo')) throw e;
        continue;
      }

      if (trabajo.estado === 'Imprimiendo') {
        btn.innerHTML = spinner('Imprimiendo…');
      } else if (trabajo.estado === 'Impreso') {
        btn.style.backgroundColor = '#198754';
        btn.style.borderColor = '#198754';
        btn.style.color = '#fff';
        if (trabajo.modo === 'prueba') {
          btn.innerHTML = '<i class="bi bi-check-lg me-1"></i>Lista';
          mostrarToastError(`Etiqueta de ${folio} lista (la impresora no está activada).`, 'text-bg-secondary');
        } else {
          btn.innerHTML = '<i class="bi bi-check-lg me-1"></i>Impresa ✓';
          mostrarToastError(`Etiqueta de ${folio} impresa ✓`, 'text-bg-success');
        }
        await esperar(3000);
        restaurar();
        return;
      } else if (trabajo.estado === 'Error') {
        restaurar();
        console.error('Error al imprimir etiqueta', folio, trabajo.mensaje);
        mostrarToastError(
          `No se pudo imprimir la etiqueta de ${folio}. Revisa que la impresora esté encendida y vuelve a intentar.`
        );
        return;
      } else if (trabajo.estado === 'En cola') {
        btn.innerHTML = trabajo.cola_pausada
          ? '<i class="bi bi-pause-circle me-1"></i>En espera'
          : spinner('En espera…');
        const antes = (trabajo.posicion || 1) - 1;
        if (!avisoEspera && antes > 0) {
          avisoEspera = true;
          mostrarToastError(`Etiqueta de ${folio} en espera. Hay ${antes} antes.`, 'text-bg-warning');
        }
      } else {
        // Descartado u otro estado final
        restaurar();
        return;
      }
    }

    restaurar();
    console.error('Etiqueta sin confirmar después de 90 s', folio);
    mostrarToastError(
      `La etiqueta de ${folio} está tardando. Revisa si salió antes de volver a mandarla.`,
      'text-bg-warning'
    );
  } catch (error) {
    restaurar();
    console.error('Error al imprimir etiqueta', folio, error);
    mostrarToastError(
      error instanceof TypeError
        ? 'No se pudo conectar con el sistema. Intenta de nuevo.'
        : `No se pudo imprimir la etiqueta de ${folio}. Revisa que la impresora esté encendida y vuelve a intentar.`
    );
  }
}
