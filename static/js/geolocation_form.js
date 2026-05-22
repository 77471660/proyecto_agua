(function () {
    const forms = document.querySelectorAll('[data-geolocation-form]');

    if (!forms.length) {
        return;
    }

    function setStatus(status, message, tone) {
        status.textContent = message;
        status.classList.remove('text-danger', 'text-success', 'text-muted');
        status.classList.add(tone || 'text-muted');
    }

    function geolocationMessage(error) {
        if (!error) {
            return 'No se pudo obtener la ubicacion actual. Puedes guardar el cliente manualmente.';
        }

        if (error.code === error.PERMISSION_DENIED) {
            return 'Permiso de ubicacion denegado. Puedes escribir la direccion o las coordenadas manualmente.';
        }

        if (error.code === error.POSITION_UNAVAILABLE) {
            return 'GPS desactivado o ubicacion no disponible. Puedes continuar con direccion manual.';
        }

        if (error.code === error.TIMEOUT) {
            return 'La ubicacion tardo demasiado. Revisa el GPS o intenta nuevamente.';
        }

        return 'No se pudo obtener la ubicacion actual. Puedes guardar el cliente manualmente.';
    }

    forms.forEach((form) => {
        const button = form.querySelector('[data-geolocation-button]');
        const latitudeInput = form.querySelector('[data-geolocation-latitude]');
        const longitudeInput = form.querySelector('[data-geolocation-longitude]');
        const status = form.querySelector('[data-geolocation-status]');

        if (!button || !latitudeInput || !longitudeInput || !status) {
            return;
        }

        const defaultButtonText = button.textContent.trim();

        function restoreButton() {
            button.disabled = false;
            button.textContent = defaultButtonText;
            button.removeAttribute('aria-busy');
        }

        if (!('geolocation' in navigator)) {
            button.disabled = true;
            setStatus(
                status,
                'Este dispositivo no permite capturar ubicacion desde el navegador. Puedes guardar la direccion manualmente.',
                'text-danger'
            );
            return;
        }

        button.addEventListener('click', function () {
            if (button.disabled || button.getAttribute('aria-busy') === 'true') {
                return;
            }

            button.disabled = true;
            button.setAttribute('aria-busy', 'true');
            button.textContent = 'Obteniendo ubicacion...';
            setStatus(status, 'Solicitando permiso de ubicacion...', 'text-muted');

            navigator.geolocation.getCurrentPosition(
                function (position) {
                    latitudeInput.value = position.coords.latitude.toFixed(6);
                    longitudeInput.value = position.coords.longitude.toFixed(6);
                    setStatus(status, 'Ubicacion actual capturada correctamente.', 'text-success');
                    restoreButton();
                },
                function (error) {
                    console.warn('Error de geolocalizacion', {
                        code: error ? error.code : null,
                        message: error ? error.message : '',
                    });
                    setStatus(status, geolocationMessage(error), 'text-danger');
                    restoreButton();
                },
                {
                    enableHighAccuracy: true,
                    timeout: 12000,
                    maximumAge: 60000,
                }
            );
        });
    });
})();
