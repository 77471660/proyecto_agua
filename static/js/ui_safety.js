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
})();
