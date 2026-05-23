(function () {
    const forms = document.querySelectorAll('[data-reference-house-form]');
    const message = 'Para guardar referencia de casa debes capturar ubicación GPS y tomar foto.';

    forms.forEach((form) => {
        const latitudeInput = form.querySelector('[name="latitud"]');
        const longitudeInput = form.querySelector('[name="longitud"]');
        const photoInput = form.querySelector('[name="foto_referencia"]');
        const status = form.querySelector('[data-reference-house-status]');

        if (!latitudeInput || !longitudeInput || !photoInput) {
            return;
        }

        function setStatus(text) {
            if (!status) {
                return;
            }

            status.textContent = text;
            status.classList.remove('d-none');
        }

        form.addEventListener('submit', function (event) {
            const hasGps = Boolean(
                latitudeInput.value.trim() && longitudeInput.value.trim()
            );
            const file = photoInput.files && photoInput.files[0];
            const hasPhoto = Boolean(file && file.size > 0);

            if (hasGps !== hasPhoto) {
                event.preventDefault();
                setStatus(message);
            }
        });
    });
})();
