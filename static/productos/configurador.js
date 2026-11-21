(() => {
  const api = window.ConfiguradorProductos || {};
  let active = null;

  const money = (value) => {
    const number = Number(value);
    return Number.isFinite(number)
      ? new Intl.NumberFormat('es-MX', { style: 'currency', currency: 'MXN' }).format(number)
      : '—';
  };

  const displayValue = (value) => {
    if (value === null || value === undefined || value === '') return '';
    if (typeof value === 'object') return JSON.stringify(value);
    return String(value);
  };

  const readNumber = (root, id) => {
    const value = root.querySelector(`#${id}`).value.trim();
    if (value === '') return null;
    const number = Number(value);
    return Number.isFinite(number) ? number : null;
  };

  const init = () => {
    const modal = document.getElementById('modalConfiguradorProductos');
    if (!modal || modal.dataset.configuradorReady === 'true') return;
    modal.dataset.configuradorReady = 'true';

    const $ = (selector) => modal.querySelector(selector);
    const bootstrapModal = window.bootstrap?.Modal?.getOrCreateInstance(modal);
    const searchInput = $('#configuradorBusqueda');
    const searchResults = $('#configuradorResultadosBusqueda');
    const presentationSelect = $('#configuradorPresentacion');
    const fields = $('#configuradorCampos');
    const addButton = $('#configuradorAgregar');
    const errorBox = $('#configuradorError');
    const summary = $('#configuradorResumen');
    let product = null;
    let presentation = null;
    let quote = null;
    let searchTimer = null;
    let quoteTimer = null;
    let searchSequence = 0;
    let quoteSequence = 0;
    let quoteController = null;

    const fieldIds = {
      cantidad: 'configuradorCantidad',
      largo: 'configuradorLargo',
      ancho: 'configuradorAncho',
      alto: 'configuradorAlto',
      minutos: 'configuradorMinutos',
      precio_libre: 'configuradorPrecioLibre',
    };

    const clearQuote = () => {
      quote = null;
      addButton.disabled = true;
      summary.hidden = true;
    };

    const showError = (message) => {
      errorBox.textContent = message || '';
      errorBox.hidden = !message;
    };

    const resetInputFields = () => {
      Object.values(fieldIds).forEach((id) => { $(`#${id}`).value = ''; });
    };

    const setVisibleFields = (forma) => {
      const visible = {
        fijo: ['cantidad'],
        paquete: ['cantidad'],
        lineal: ['cantidad', 'largo'],
        m2: ['cantidad', 'ancho', 'alto'],
        rollo: ['cantidad', 'largo'],
        tiempo: ['minutos'],
        libre: ['precio_libre'],
      }[forma] || [];
      fields.hidden = !visible.length;
      fields.querySelectorAll('[data-campo]').forEach((element) => {
        const name = element.dataset.campo;
        const isVisible = visible.includes(name);
        element.hidden = !isVisible;
        const input = element.querySelector('input');
        input.required = isVisible;
      });
      if (visible.includes('cantidad')) $('#configuradorCantidad').value = '1';
      if (visible.includes('largo')) $('#configuradorLargo').value = '1';
      if (visible.includes('ancho')) $('#configuradorAncho').value = '1';
      if (visible.includes('alto')) $('#configuradorAlto').value = '1';
      if (visible.includes('minutos')) $('#configuradorMinutos').value = '1';
      if (visible.includes('precio_libre')) $('#configuradorPrecioLibre').value = '';
    };

    const renderQuote = (data) => {
      const unitario = data.precio_unitario ?? data.unitario ?? data.precio;
      const subtotal = data.subtotal ?? data.total;
      const hasUnitario = unitario !== null && unitario !== undefined && unitario !== '' && Number.isFinite(Number(unitario));
      const hasSubtotal = subtotal !== null && subtotal !== undefined && subtotal !== '' && Number.isFinite(Number(subtotal));
      if (!hasUnitario || !hasSubtotal) {
        throw new Error('La cotización no devolvió un precio válido.');
      }

      $('#configuradorUnitario').textContent = money(unitario);
      $('#configuradorSubtotal').textContent = money(subtotal);
      const detailParts = [];
      if (data.rollo !== undefined && data.rollo !== null) detailParts.push(`Rollo: ${displayValue(data.rollo)}`);
      if (data.tramos !== undefined && data.tramos !== null) detailParts.push(`Tramos: ${displayValue(data.tramos)}`);
      $('#configuradorDetalles').textContent = detailParts.join(' · ');

      const minimum = data.minimo_aplicado;
      const minimumBox = $('#configuradorMinimo');
      minimumBox.hidden = !minimum;
      minimumBox.textContent = typeof minimum === 'number' || typeof minimum === 'string'
        ? `Mínimo aplicado: ${money(minimum)}`
        : (minimum ? 'Mínimo aplicado' : '');

      const warnings = Array.isArray(data.avisos)
        ? data.avisos
        : (data.avisos ? [data.avisos] : []);
      const warningsList = $('#configuradorAvisos');
      warningsList.replaceChildren(...warnings.map((warning) => {
        const item = document.createElement('li');
        item.textContent = displayValue(warning);
        return item;
      }));
      warningsList.hidden = warnings.length === 0;
      summary.hidden = false;
      addButton.disabled = false;
    };

    const getConfiguration = () => ({
      id_lista: active?.id_lista ?? null,
      forma: presentation?.forma || null,
      cantidad: readNumber(modal, fieldIds.cantidad) ?? 1,
      ancho: readNumber(modal, fieldIds.ancho),
      alto: readNumber(modal, fieldIds.alto),
      largo: readNumber(modal, fieldIds.largo),
      minutos: readNumber(modal, fieldIds.minutos),
      precio_libre: readNumber(modal, fieldIds.precio_libre),
    });

    const cotizarAhora = async (sequence) => {
      if (!presentation || sequence !== quoteSequence) return;
      const configuration = getConfiguration();
      const visibleInputs = [...fields.querySelectorAll('input[required]')];
      if (visibleInputs.some((input) => !input.value || !input.checkValidity())) {
        clearQuote();
        showError('Completa los datos de la presentación para cotizar.');
        return;
      }

      showError('');
      quoteController?.abort();
      quoteController = new AbortController();
      try {
        const response = await fetch(modal.dataset.urlCotizar, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
          body: JSON.stringify({
            id_presentacion: presentation.id_presentacion,
            id_lista: configuration.id_lista,
            id_cliente: active?.id_cliente ?? null,
            cantidad: configuration.cantidad ?? 1,
            ancho: configuration.ancho,
            alto: configuration.alto,
            largo: configuration.largo,
            minutos: configuration.minutos,
            precio_libre: configuration.precio_libre,
          }),
          signal: quoteController.signal,
        });
        const data = await response.json();
        if (sequence !== quoteSequence) return;
        if (!response.ok) throw new Error(data.error || 'No se pudo cotizar el producto.');
        renderQuote(data);
        quote = data;
      } catch (error) {
        if (error.name === 'AbortError' || sequence !== quoteSequence) return;
        clearQuote();
        showError(error.message || 'No se pudo cotizar el producto.');
      }
    };

    const scheduleQuote = () => {
      clearTimeout(quoteTimer);
      quoteSequence += 1;
      const sequence = quoteSequence;
      clearQuote();
      if (!presentation) return;
      quoteTimer = setTimeout(() => cotizarAhora(sequence), 300);
    };

    const selectProduct = (selectedProduct, idPresentacion) => {
      product = selectedProduct;
      presentation = null;
      searchInput.value = `${selectedProduct.clave} · ${selectedProduct.nombre}`;
      searchResults.replaceChildren();
      presentationSelect.replaceChildren(new Option('Selecciona una presentación', ''));
      selectedProduct.presentaciones.forEach((item) => {
        const option = new Option(`${item.nombre} (${item.forma})`, item.id_presentacion);
        option.dataset.forma = item.forma;
        presentationSelect.add(option);
      });
      presentationSelect.disabled = selectedProduct.presentaciones.length === 0;
      presentationSelect.value = idPresentacion ? String(idPresentacion) : '';
      if (presentationSelect.value) {
        presentation = selectedProduct.presentaciones.find((item) => String(item.id_presentacion) === presentationSelect.value) || null;
      }
      fields.hidden = !presentation;
      if (presentation) setVisibleFields(presentation.forma);
      else resetInputFields();
      clearQuote();
      showError('');
      if (presentation) scheduleQuote();
    };

    const runSearch = async (query) => {
      const sequence = ++searchSequence;
      if (query.length < 2) {
        searchResults.replaceChildren();
        $('#configuradorBusquedaAyuda').textContent = 'Escribe al menos 2 caracteres.';
        return [];
      }
      $('#configuradorBusquedaAyuda').textContent = 'Buscando…';
      try {
        const url = new URL(modal.dataset.urlBuscar, window.location.href);
        url.searchParams.set('q', query);
        const response = await fetch(url, { headers: { Accept: 'application/json' } });
        if (!response.ok) throw new Error('No se pudo buscar el producto.');
        const products = await response.json();
        if (sequence !== searchSequence) return [];
        searchResults.replaceChildren();
        products.forEach((item) => {
          const button = document.createElement('button');
          button.type = 'button';
          button.className = 'list-group-item list-group-item-action';
          button.setAttribute('role', 'option');
          button.textContent = `${item.clave} · ${item.nombre}${item.categoria ? ` — ${item.categoria}` : ''}`;
          button.addEventListener('click', () => selectProduct(item));
          searchResults.append(button);
        });
        $('#configuradorBusquedaAyuda').textContent = products.length ? 'Selecciona un producto.' : 'No se encontraron productos.';
        return products;
      } catch (error) {
        if (sequence === searchSequence) {
          $('#configuradorBusquedaAyuda').textContent = error.message || 'Error al buscar productos.';
        }
        return [];
      }
    };

    searchInput.addEventListener('input', () => {
      searchSequence += 1;
      product = null;
      presentation = null;
      presentationSelect.disabled = true;
      presentationSelect.replaceChildren(new Option('Selecciona un producto primero', ''));
      fields.hidden = true;
      clearQuote();
      showError('');
      clearTimeout(searchTimer);
      const query = searchInput.value.trim();
      searchTimer = setTimeout(() => runSearch(query), 250);
    });

    presentationSelect.addEventListener('change', () => {
      presentation = product?.presentaciones.find((item) => String(item.id_presentacion) === presentationSelect.value) || null;
      if (presentation) setVisibleFields(presentation.forma);
      else fields.hidden = true;
      showError('');
      scheduleQuote();
    });

    fields.addEventListener('input', scheduleQuote);
    fields.addEventListener('change', scheduleQuote);

    addButton.addEventListener('click', () => {
      if (!quote || !product || !presentation || addButton.disabled) return;
      const configuration = {
        ...getConfiguration(),
        resultado: quote,
      };
      const resultado = {
        id_producto: product.id_producto,
        id_presentacion: presentation.id_presentacion,
        presentacion_nombre: presentation.nombre,
        cantidad: configuration.cantidad ?? 1,
        descripcion: quote.descripcion || [product.nombre, presentation.nombre].filter(Boolean).join(' · '),
        precio_unitario: quote.precio_unitario ?? quote.unitario ?? quote.precio,
        configuracion: configuration,
      };
      const callback = active?.alAgregar;
      bootstrapModal?.hide();
      if (typeof callback === 'function') callback(resultado);
    });

    const reset = () => {
      clearTimeout(searchTimer);
      clearTimeout(quoteTimer);
      quoteController?.abort();
      searchSequence += 1;
      quoteSequence += 1;
      active = null;
      product = null;
      presentation = null;
      searchInput.value = '';
      searchResults.replaceChildren();
      presentationSelect.replaceChildren(new Option('Selecciona un producto primero', ''));
      presentationSelect.disabled = true;
      resetInputFields();
      fields.hidden = true;
      clearQuote();
      showError('');
      $('#configuradorBusquedaAyuda').textContent = 'Escribe al menos 2 caracteres.';
    };
    modal.addEventListener('hidden.bs.modal', reset);

    api.abrir = (options = {}, alAgregar) => {
      if (!bootstrapModal) throw new Error('Bootstrap Modal no está disponible en esta página.');
      reset();
      const previo = options.previo;
      const previoConfig = previo?.configuracion || previo || {};
      active = {
        id_lista: options.id_lista ?? previoConfig.id_lista ?? null,
        id_cliente: options.id_cliente ?? null,
        alAgregar,
      };
      bootstrapModal.show();
      if (previo) {
        const idProducto = previo.id_producto ?? previoConfig.id_producto;
        const idPresentacion = previo.id_presentacion ?? previoConfig.id_presentacion;
        if (idProducto) {
          const query = `P-${idProducto}`;
          searchInput.value = query;
          clearTimeout(searchTimer);
          searchTimer = setTimeout(async () => {
            const products = await runSearch(query);
            const match = products.find((item) => String(item.id_producto) === String(idProducto));
            if (!match) return;
            selectProduct(match, idPresentacion);
            for (const key of ['cantidad', 'ancho', 'alto', 'largo', 'minutos', 'precio_libre']) {
              const value = previoConfig[key];
              if (value !== undefined && value !== null && fieldIds[key]) $(`#${fieldIds[key]}`).value = value;
            }
            if (presentation) scheduleQuote();
          }, 0);
        }
      }
    };

    window.ConfiguradorProductos = api;
  };

  window.ConfiguradorProductos = api;
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init, { once: true });
  } else {
    init();
  }
})();
