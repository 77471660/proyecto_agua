(function () {
    const buttons = document.querySelectorAll('[data-copy-location]');

    if (!buttons.length) {
        return;
    }

    function setStatus(button, message, tone) {
        const container = button.parentElement || document;
        const status = container.querySelector('[data-copy-location-status]');

        if (!status) {
            return;
        }

        status.textContent = message;
        status.classList.remove('text-danger', 'text-success', 'text-muted');
        status.classList.add(tone || 'text-muted');
    }

    function fallbackCopy(text) {
        const textarea = document.createElement('textarea');
        textarea.value = text;
        textarea.setAttribute('readonly', '');
        textarea.style.left = '-9999px';
        textarea.style.position = 'fixed';
        document.body.appendChild(textarea);
        textarea.select();

        try {
            return document.execCommand('copy');
        } finally {
            document.body.removeChild(textarea);
        }
    }

    function copyText(text) {
        if (navigator.clipboard && window.isSecureContext) {
            return navigator.clipboard.writeText(text);
        }

        return new Promise((resolve, reject) => {
            if (fallbackCopy(text)) {
                resolve();
            } else {
                reject(new Error('Fallback copy failed'));
            }
        });
    }

    buttons.forEach((button) => {
        button.addEventListener('click', function () {
            const value = button.dataset.copyLocation || '';

            if (!value) {
                setStatus(button, 'No hay ubicación para copiar.', 'text-danger');
                return;
            }

            button.disabled = true;

            copyText(value)
                .then(() => {
                    setStatus(button, 'Ubicación copiada.', 'text-success');
                })
                .catch((error) => {
                    console.warn('Error copiando ubicación', error);
                    setStatus(
                        button,
                        'No se pudo copiar automáticamente. Mantén presionado el texto para copiarlo.',
                        'text-danger'
                    );
                })
                .finally(() => {
                    button.disabled = false;
                });
        });
    });
})();
