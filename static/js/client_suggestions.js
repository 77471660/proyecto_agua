(function () {
    const root = document.querySelector('[data-client-suggestions]');

    if (!root) {
        return;
    }

    const searchUrl = root.dataset.searchUrl;
    const list = root.querySelector('[data-client-suggestions-list]');
    const fields = Array.from(
        document.querySelectorAll('[data-client-suggestion-field]')
    );

    if (!searchUrl || !list || !fields.length) {
        return;
    }

    let timeoutId = null;
    let controller = null;

    function normalizedPhone(value) {
        return (value || '').replace(/\D/g, '');
    }

    function bestQuery() {
        const values = {};

        fields.forEach(function (field) {
            values[field.dataset.clientSuggestionField] = field.value.trim();
        });

        const phone = normalizedPhone(values.telefono);

        if (phone.length >= 3) {
            return phone;
        }

        if ((values.nombre || '').length >= 3) {
            return values.nombre;
        }

        if ((values.direccion || '').length >= 4) {
            return values.direccion;
        }

        return '';
    }

    function hideSuggestions() {
        root.classList.add('d-none');
        list.innerHTML = '';
    }

    function clientUrl(clientId) {
        return `/cliente/${clientId}/`;
    }

    function newOrderUrl(clientId) {
        return `/registrar-pedido/?cliente=${clientId}`;
    }

    function renderSuggestions(results) {
        list.innerHTML = '';

        if (!results.length) {
            hideSuggestions();
            return;
        }

        results.slice(0, 5).forEach(function (client) {
            const item = document.createElement('div');
            item.className = 'list-group-item px-0';

            const row = document.createElement('div');
            row.className = 'd-flex justify-content-between gap-3 flex-wrap';

            const details = document.createElement('div');
            details.className = 'min-w-0';

            const name = document.createElement('div');
            name.className = 'fw-semibold';
            name.textContent = client.nombre || 'Cliente sin nombre';

            const phone = document.createElement('div');
            phone.className = 'small text-muted';
            phone.textContent = client.telefono || 'Sin telefono';

            const address = document.createElement('div');
            address.className = 'small text-muted';
            address.textContent = client.direccion || 'Sin direccion';

            details.appendChild(name);
            details.appendChild(phone);
            details.appendChild(address);

            const actions = document.createElement('div');
            actions.className = 'd-flex gap-2 align-items-start flex-wrap';

            const viewLink = document.createElement('a');
            viewLink.className = 'btn btn-sm btn-outline-primary';
            viewLink.href = clientUrl(client.id);
            viewLink.textContent = 'Ver';

            const useLink = document.createElement('a');
            useLink.className = 'btn btn-sm btn-outline-success';
            useLink.href = newOrderUrl(client.id);
            useLink.textContent = 'Usar este cliente';

            actions.appendChild(viewLink);
            actions.appendChild(useLink);
            row.appendChild(details);
            row.appendChild(actions);
            item.appendChild(row);

            list.appendChild(item);
        });

        root.classList.remove('d-none');
    }

    function fetchSuggestions() {
        const query = bestQuery();

        if (!query) {
            hideSuggestions();
            return;
        }

        if (controller) {
            controller.abort();
        }

        controller = new AbortController();

        fetch(`${searchUrl}?q=${encodeURIComponent(query)}`, {
            headers: {
                'X-Requested-With': 'XMLHttpRequest'
            },
            signal: controller.signal
        })
            .then(function (response) {
                if (!response.ok) {
                    return { results: [] };
                }

                return response.json();
            })
            .then(function (data) {
                renderSuggestions(data.results || []);
            })
            .catch(function (error) {
                if (error.name !== 'AbortError') {
                    hideSuggestions();
                }
            });
    }

    function scheduleFetch() {
        window.clearTimeout(timeoutId);
        timeoutId = window.setTimeout(fetchSuggestions, 350);
    }

    fields.forEach(function (field) {
        field.addEventListener('input', scheduleFetch);
        field.addEventListener('blur', scheduleFetch);
    });
})();
