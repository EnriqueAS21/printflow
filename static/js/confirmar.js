// Confirmación y avisos del sistema (reemplazan window.confirm / window.alert).
//
//   const ok = await pfConfirmar({ titulo, mensaje, aceptar, cancelar, peligro });  // true | false
//   pfAvisar('Texto', 'danger' | 'success' | 'warning' | 'info');
(function () {
    const escapar = valor => String(valor ?? '').replace(/[&<>"']/g,
        c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

    let pendiente = null;   // { resolver, resultado }

    window.pfConfirmar = function (opciones = {}) {
        const modal = document.getElementById('modalConfirmar');
        if (!modal || !window.bootstrap?.Modal) {
            // Sin el modal en la página no hay forma de preguntar: se cancela en vez de actuar a ciegas.
            return Promise.resolve(false);
        }
        if (pendiente) pendiente.resolver(false);
        return new Promise(resolver => {
            const instancia = bootstrap.Modal.getOrCreateInstance(modal, { backdrop: 'static' });
            const estado = { resolver, resultado: false };
            pendiente = estado;
            modal.classList.toggle('pf-confirmar-peligro', opciones.peligro === true);
            document.getElementById('confirmarTitulo').textContent = opciones.titulo || 'Confirmar';
            document.getElementById('confirmarMensaje').textContent = opciones.mensaje || '';
            document.getElementById('confirmarAceptar').textContent = opciones.aceptar || 'Aceptar';
            document.getElementById('confirmarCancelar').textContent = opciones.cancelar || 'Cancelar';

            const aceptar = () => { estado.resultado = true; instancia.hide(); };
            const cancelar = () => { instancia.hide(); };
            const botonAceptar = document.getElementById('confirmarAceptar');
            const botonCancelar = document.getElementById('confirmarCancelar');
            const teclas = evento => {
                if (evento.key === 'Escape') { evento.preventDefault(); cancelar(); }
            };
            const alOcultar = () => {
                botonAceptar.removeEventListener('click', aceptar);
                botonCancelar.removeEventListener('click', cancelar);
                modal.removeEventListener('keydown', teclas);
                modal.removeEventListener('hidden.bs.modal', alOcultar);
                if (pendiente === estado) pendiente = null;
                resolver(estado.resultado);
            };
            botonAceptar.addEventListener('click', aceptar);
            botonCancelar.addEventListener('click', cancelar);
            modal.addEventListener('keydown', teclas);
            modal.addEventListener('hidden.bs.modal', alOcultar);
            modal.addEventListener('shown.bs.modal', () => botonCancelar.focus(), { once: true });
            instancia.show();
        });
    };

    window.pfAvisar = function (mensaje, tipo = 'danger') {
        let contenedor = document.querySelector('.pf-toasts');
        if (!contenedor) {
            contenedor = document.createElement('div');
            contenedor.className = 'toast-container pf-toasts position-fixed bottom-0 end-0 p-3';
            contenedor.style.zIndex = '1090';
            document.body.appendChild(contenedor);
        }
        const color = { danger: 'text-bg-danger', success: 'text-bg-success', warning: 'text-bg-warning', info: 'text-bg-primary' }[tipo] || 'text-bg-danger';
        const el = document.createElement('div');
        el.className = `toast align-items-center border-0 ${color}`;
        el.setAttribute('role', tipo === 'danger' ? 'alert' : 'status');
        el.setAttribute('aria-live', tipo === 'danger' ? 'assertive' : 'polite');
        el.setAttribute('aria-atomic', 'true');
        el.innerHTML = `<div class="d-flex"><div class="toast-body">${escapar(mensaje)}</div>
            <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast" aria-label="Cerrar"></button></div>`;
        contenedor.appendChild(el);
        el.addEventListener('hidden.bs.toast', () => el.remove());
        bootstrap.Toast.getOrCreateInstance(el, { delay: tipo === 'danger' ? 6000 : 3500 }).show();
    };
})();
