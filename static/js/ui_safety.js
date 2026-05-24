(function () {
    function digitsOnly(value) {
        let digits = String(value || '').replace(/\D/g, '');

        if (digits.length === 11 && digits.indexOf('51') === 0) {
            digits = digits.slice(2);
        }

        return digits.slice(0, 9);
    }

    function formatPhone(value) {
        const digits = digitsOnly(value);
        const parts = [];

        if (digits.slice(0, 3)) {
            parts.push(digits.slice(0, 3));
        }

        if (digits.slice(3, 6)) {
            parts.push(digits.slice(3, 6));
        }

        if (digits.slice(6, 9)) {
            parts.push(digits.slice(6, 9));
        }

        return parts.join(' ');
    }

    function titleCase(value) {
        return String(value || '')
            .toLowerCase()
            .replace(/\s+/g, ' ')
            .trim()
            .replace(/(^|\s)([a-záéíóúñü])/g, function (match) {
                return match.toUpperCase();
            });
    }

    document.querySelectorAll('input[name="telefono"]').forEach(function (input) {
        input.setAttribute('inputmode', 'numeric');
        input.setAttribute('autocomplete', 'tel');
        input.setAttribute('maxlength', '11');

        input.value = formatPhone(input.value);

        input.addEventListener('input', function () {
            input.value = formatPhone(input.value);
        });

        if (input.form) {
            input.form.addEventListener('submit', function () {
                input.value = digitsOnly(input.value);
            });
        }
    });

    document.querySelectorAll('input[name="nombre"]').forEach(function (input) {
        input.setAttribute('autocomplete', 'name');

        input.addEventListener('blur', function () {
            input.value = titleCase(input.value);
        });

        if (input.form) {
            input.form.addEventListener('submit', function () {
                input.value = titleCase(input.value);
            });
        }
    });

    document.querySelectorAll('[data-confirm]:not(form)').forEach(function (element) {
        element.addEventListener('click', function (event) {
            const message = element.getAttribute('data-confirm');

            if (message && !window.confirm(message)) {
                event.preventDefault();
                event.stopPropagation();
            }
        });
    });

    document.querySelectorAll('form[data-confirm]').forEach(function (form) {
        form.addEventListener('submit', function (event) {
            const message = form.getAttribute('data-confirm');

            if (message && !window.confirm(message)) {
                event.preventDefault();
            }
        });
    });

    document.querySelectorAll('input[type="file"][name="foto_referencia"]').forEach(function (input) {
        const root = input.closest('[data-photo-preview-root]') || input.parentElement;

        if (!root) {
            return;
        }

        let image = root.querySelector('[data-photo-preview-image]');
        let status = root.querySelector('[data-photo-preview-status]');

        if (!image) {
            image = document.createElement('img');
            image.alt = 'Vista previa de foto seleccionada';
            image.className = 'photo-preview-thumb';
            image.setAttribute('data-photo-preview-image', '');
            input.insertAdjacentElement('afterend', image);
        }

        if (!status) {
            status = document.createElement('div');
            status.className = 'photo-preview-status';
            status.setAttribute('data-photo-preview-status', '');
            image.insertAdjacentElement('afterend', status);
        }

        input.addEventListener('change', function () {
            const file = input.files && input.files[0];

            if (!file) {
                image.removeAttribute('src');
                image.classList.remove('is-visible');
                status.textContent = '';
                status.classList.remove('is-visible');
                return;
            }

            image.src = URL.createObjectURL(file);
            image.classList.add('is-visible');
            status.textContent = 'Foto seleccionada correctamente';
            status.classList.add('is-visible');
        });
    });
})();
