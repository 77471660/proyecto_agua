(function () {
    const searchBlocks = document.querySelectorAll('[data-client-search]');

    if (!searchBlocks.length) {
        return;
    }

    function escapeHtml(text) {
        return String(text || '')
            .replaceAll('&', '&amp;')
            .replaceAll('<', '&lt;')
            .replaceAll('>', '&gt;')
            .replaceAll('"', '&quot;')
            .replaceAll("'", '&#039;');
    }

    searchBlocks.forEach((block) => {
        const input = block.querySelector('[data-client-input]');
        const hidden = block.querySelector('[data-client-hidden]');
        const suggestions = block.querySelector('[data-client-suggestions]');
        const error = block.querySelector('[data-client-error]');
        const summarySelector = block.dataset.summaryTarget;
        const selectedSelector = block.dataset.selectedTarget;
        const summary = summarySelector ? document.querySelector(summarySelector) : null;
        const selected = selectedSelector ? document.querySelector(selectedSelector) : null;
        const form = input ? input.closest('form') : null;
        const searchUrl = block.dataset.searchUrl || '/clientes/buscar/';

        if (!input || !hidden || !suggestions || !form) {
            return;
        }

        let searchTimer = null;
        let activeController = null;
        let currentResults = [];

        function hideSuggestions() {
            suggestions.classList.add('d-none');
            suggestions.innerHTML = '';
            currentResults = [];
        }

        function clearSelectedClient() {
            hidden.value = '';

            if (summary) {
                summary.classList.add('d-none');
            }

            if (selected) {
                selected.classList.add('d-none');
                selected.innerHTML = '';
            }
        }

        function renderSummary(client) {
            if (summary) {
                const fields = {
                    resumen_telefono: client.telefono || 'No registrado',
                    resumen_direccion: client.direccion || 'No registrada',
                    resumen_estado: client.estado || 'Sin estado',
                    resumen_pedidos: client.pedidos_entregados || '0',
                    resumen_ultima: client.ultima_compra || 'Sin compras',
                    resumen_total: client.total_gastado || '0.00',
                };

                Object.entries(fields).forEach(([id, value]) => {
                    const element = document.getElementById(id);

                    if (element) {
                        element.textContent = value;
                    }
                });

                summary.classList.remove('d-none');
            }

            if (selected) {
                selected.innerHTML = `
                    <strong>${escapeHtml(client.nombre)}</strong><br>
                    <span>${escapeHtml(client.telefono)}</span><br>
                    <small>${escapeHtml(client.direccion || 'Sin dirección registrada')}</small>
                `;
                selected.classList.remove('d-none');
            }
        }

        function selectClient(client) {
            hidden.value = client.id;
            input.value = `${client.nombre} - ${client.telefono}`;

            if (error) {
                error.classList.add('d-none');
            }

            renderSummary(client);
            hideSuggestions();
        }

        function renderSuggestions(clients) {
            suggestions.innerHTML = '';
            currentResults = clients;

            if (!clients.length) {
                suggestions.innerHTML = `
                    <div class="p-3 text-muted">
                        No se encontraron clientes.
                    </div>
                `;
                suggestions.classList.remove('d-none');
                return;
            }

            clients.forEach((client) => {
                const button = document.createElement('button');
                const reference = client.referencia ? ` - ${client.referencia}` : '';

                button.type = 'button';
                button.className = 'client-suggestion';
                button.innerHTML = `
                    <div class="fw-semibold">${escapeHtml(client.nombre)} - ${escapeHtml(client.telefono)}</div>
                    <small class="text-muted">${escapeHtml((client.direccion || 'Sin dirección registrada') + reference)}</small>
                `;
                button.addEventListener('click', () => selectClient(client));
                suggestions.appendChild(button);
            });

            suggestions.classList.remove('d-none');
        }

        function showLoading() {
            suggestions.innerHTML = `
                <div class="p-3 text-muted">
                    Buscando clientes...
                </div>
            `;
            suggestions.classList.remove('d-none');
        }

        input.addEventListener('input', function () {
            clearSelectedClient();

            if (error) {
                error.classList.add('d-none');
            }

            clearTimeout(searchTimer);

            if (activeController) {
                activeController.abort();
            }

            const text = this.value.trim();

            if (text.length < 2) {
                hideSuggestions();
                return;
            }

            showLoading();
            activeController = new AbortController();

            searchTimer = setTimeout(() => {
                fetch(`${searchUrl}?q=${encodeURIComponent(text)}`, {
                    signal: activeController.signal,
                })
                    .then((response) => response.ok ? response.json() : { results: [] })
                    .then((data) => renderSuggestions(data.results || []))
                    .catch((errorResponse) => {
                        if (errorResponse.name !== 'AbortError') {
                            hideSuggestions();
                        }
                    });
            }, 180);
        });

        input.addEventListener('keydown', function (event) {
            if (event.key === 'Enter' && !hidden.value && currentResults.length) {
                event.preventDefault();
                selectClient(currentResults[0]);
            }

            if (event.key === 'Escape') {
                hideSuggestions();
            }
        });

        document.addEventListener('click', function (event) {
            if (!block.contains(event.target)) {
                hideSuggestions();
            }
        });

        form.addEventListener('submit', function (event) {
            if (!hidden.value) {
                event.preventDefault();

                if (error) {
                    error.classList.remove('d-none');
                }

                input.focus();
            }
        });
    });
})();
