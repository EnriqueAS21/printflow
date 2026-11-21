// Abre WhatsApp y copia la nota PNG del pedido (templates/pedidos/_info_produccion.html).
if (!window.abrirWhatsAppYCopiarNota) {
  window.abrirWhatsAppYCopiarNota = function (enlace) {
    const estado = enlace.parentElement.querySelector('.wa-copy-status');
    if (!navigator.clipboard || !window.ClipboardItem) {
      if (estado) estado.textContent = 'No se pudo copiar la nota; descarga el PDF.';
      return true;
    }
    try {
      const imagen = fetch(enlace.dataset.pngUrl, { credentials: 'same-origin' })
        .then(respuesta => {
          if (!respuesta.ok) throw new Error('No se pudo generar la imagen.');
          return respuesta.blob();
        });
      navigator.clipboard.write([
        new ClipboardItem({ 'image/png': imagen })
      ]).then(() => {
        if (estado) estado.textContent = 'Nota PNG copiada al portapapeles.';
      }).catch(() => {
        if (estado) estado.textContent = 'WhatsApp abierto; no se pudo copiar la nota. Descarga el PDF.';
      });
    } catch (error) {
      if (estado) estado.textContent = 'WhatsApp abierto; no se pudo copiar la nota. Descarga el PDF.';
    }
    return true;
  };
}
