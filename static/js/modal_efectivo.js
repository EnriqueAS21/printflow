// Cobro en efectivo: window.cobrarEfectivo(monto) abre el modal y devuelve una Promise
// que se resuelve con {recibido, cambio} al confirmar o con null al cancelar.
// Solo calcula el cambio: guardar el pago y abrir el cajón le corresponde a quien la llama.
(function () {
    const modal = document.getElementById('modalEfectivo');
    if (!modal) return;

    const campoRecibido = document.getElementById('efectivoRecibido');
    const textoTotal = document.getElementById('efectivoTotal');
    const textoPagado = document.getElementById('efectivoPagado');
    const textoResultado = document.getElementById('efectivoResultado');
    const tarjetaCambio = document.getElementById('efectivoTarjetaCambio');
    const etiquetaCambio = document.getElementById('efectivoEtiquetaCambio');
    const botonConfirmar = document.getElementById('efectivoConfirmar');

    // Cobro en curso: { totalCentavos, resolver, foco, listo, resultado }.
    let cobro = null;

    // Todo se calcula en centavos enteros para evitar errores de punto flotante.
    const aCentavos = valor => Math.max(Math.round((parseFloat(valor) || 0) * 100), 0);
    const dinero = centavos => '$' + (centavos / 100).toLocaleString('es-MX', {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
    });
    const recibidoCentavos = () => aCentavos(campoRecibido.value);

    function actualizar() {
        const recibido = recibidoCentavos();
        const diferencia = recibido - cobro.totalCentavos;
        const alcanza = diferencia >= 0;
        textoPagado.textContent = dinero(recibido);
        etiquetaCambio.textContent = alcanza ? 'Cambio' : 'Faltan';
        textoResultado.textContent = dinero(Math.abs(diferencia));
        tarjetaCambio.classList.toggle('pf-efectivo-falta', !alcanza);
        botonConfirmar.disabled = !alcanza;
    }

    // Fija el monto y, si se estaba escribiendo en la tarjeta, regresa a mostrar el texto.
    function fijarRecibido(centavos) {
        campoRecibido.hidden = true;
        textoPagado.hidden = false;
        campoRecibido.value = centavos % 100 ? (centavos / 100).toFixed(2) : String(centavos / 100);
        actualizar();
    }

    function abrirEdicion() {
        textoPagado.hidden = true;
        campoRecibido.hidden = false;
        campoRecibido.focus();
    }

    function aplicarEdicion() {
        if (cobro && !campoRecibido.hidden) fijarRecibido(recibidoCentavos());
    }

    function confirmar() {
        // Bootstrap ignora hide() durante la animación de apertura: se espera a shown.
        if (!cobro?.listo || cobro.resultado) return;
        const recibido = recibidoCentavos();
        if (recibido < cobro.totalCentavos) return;
        cobro.resultado = { recibido: recibido / 100, cambio: (recibido - cobro.totalCentavos) / 100 };
        bootstrap.Modal.getOrCreateInstance(modal).hide();
    }

    modal.addEventListener('click', evento => {
        if (!cobro) return;
        const billete = evento.target.closest('[data-efectivo-suma]');
        if (billete) fijarRecibido(recibidoCentavos() + Number(billete.dataset.efectivoSuma) * 100);
        else if (evento.target.closest('#efectivoExacto')) fijarRecibido(cobro.totalCentavos);
        else if (evento.target.closest('#efectivoReiniciar')) fijarRecibido(0);
        else if (evento.target.closest('#efectivoConfirmar')) confirmar();
        else if (evento.target.closest('#efectivoTarjetaPago') && campoRecibido.hidden) abrirEdicion();
    });

    campoRecibido.addEventListener('input', () => { if (cobro) actualizar(); });
    campoRecibido.addEventListener('focus', () => campoRecibido.select());
    campoRecibido.addEventListener('blur', aplicarEdicion);

    // En document, porque el foco puede no estar dentro del modal.
    document.addEventListener('keydown', evento => {
        if (!cobro) return;
        // Dentro del modal, Esc lo resuelve Bootstrap: cierra sin resultado.
        if (evento.key === 'Escape' && !modal.contains(evento.target)) {
            bootstrap.Modal.getOrCreateInstance(modal).hide();
            return;
        }
        if (evento.key !== 'Enter' || evento.target.closest('[data-bs-dismiss], #efectivoPagado')) return;
        // Enter confirma aunque el foco haya quedado en un billete, para no sumarlo otra vez.
        evento.preventDefault();
        if (evento.target === campoRecibido) {
            aplicarEdicion();
            modal.focus({ preventScroll: true });
        } else if (!evento.repeat) {
            confirmar();
        }
    });

    modal.addEventListener('shown.bs.modal', () => {
        cobro.listo = true;
        modal.focus({ preventScroll: true });
    });

    // Se resuelve al terminar de cerrar, para que quien llama no compita con la animación.
    modal.addEventListener('hidden.bs.modal', () => {
        const { resolver, resultado, foco } = cobro;
        cobro = null;
        // Bootstrap quita modal-open aunque siga abierto el modal de abajo.
        if (document.querySelector('.modal.show')) document.body.classList.add('modal-open');
        if (foco?.isConnected) foco.focus({ preventScroll: true });
        resolver(resultado ?? null);
    });

    window.cobrarEfectivo = function (monto) {
        // Un segundo envío con el modal abierto (doble clic) no debe guardar ni abrir otro cobro.
        if (cobro) return Promise.resolve(null);
        const totalCentavos = aCentavos(monto);
        if (!totalCentavos) return Promise.resolve({ recibido: 0, cambio: 0 });

        return new Promise(resolver => {
            cobro = { totalCentavos, resolver, foco: document.activeElement, listo: false, resultado: null };
            textoTotal.textContent = dinero(totalCentavos);
            fijarRecibido(0);
            bootstrap.Modal.getOrCreateInstance(modal).show();
            // Bootstrap agrega el fondo de forma síncrona y no permite asignarle una clase propia.
            const fondos = document.querySelectorAll('.modal-backdrop');
            fondos[fondos.length - 1]?.classList.add('pf-efectivo-backdrop');
        });
    };
})();
