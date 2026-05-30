(function () {
    const forms = document.querySelectorAll('[data-geolocation-form]');
    const MAX_ACCEPTED_ACCURACY = 100;
    const READ_ATTEMPTS = 3;

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

    function accuracyLabel(accuracy) {
        if (accuracy <= 30) {
            return 'Precision alta';
        }

        if (accuracy <= 80) {
            return 'Precision media';
        }

        return 'Precision baja';
    }

    function getPosition() {
        return new Promise((resolve, reject) => {
            navigator.geolocation.getCurrentPosition(
                resolve,
                reject,
                {
                    enableHighAccuracy: true,
                    timeout: 12000,
                    maximumAge: 0,
                }
            );
        });
    }

    async function getBestPosition(attempts) {
        const positions = [];
        let lastError = null;

        for (let index = 0; index < attempts; index += 1) {
            try {
                positions.push(await getPosition());
            } catch (error) {
                lastError = error;
            }
        }

        if (!positions.length) {
            throw lastError;
        }

        return positions.reduce((best, current) => {
            const bestAccuracy = best.coords.accuracy || Number.POSITIVE_INFINITY;
            const currentAccuracy = current.coords.accuracy || Number.POSITIVE_INFINITY;

            return currentAccuracy < bestAccuracy ? current : best;
        });
    }

    forms.forEach((form) => {
        const button = form.querySelector('[data-geolocation-button]');
        const latitudeInput = form.querySelector('[data-geolocation-latitude]');
        const longitudeInput = form.querySelector('[data-geolocation-longitude]');
        const accuracyInput = form.querySelector('[data-geolocation-accuracy]');
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

        button.addEventListener('click', async function () {
            if (button.disabled || button.getAttribute('aria-busy') === 'true') {
                return;
            }

            button.disabled = true;
            button.setAttribute('aria-busy', 'true');
            button.textContent = 'Obteniendo ubicacion...';
            setStatus(
                status,
                'Tomando varias lecturas GPS para elegir la mas precisa...',
                'text-muted'
            );

            try {
                const position = await getBestPosition(READ_ATTEMPTS);
                const accuracy = Number(position.coords.accuracy || 0);

                if (accuracy > MAX_ACCEPTED_ACCURACY) {
                    latitudeInput.value = '';
                    longitudeInput.value = '';
                    if (accuracyInput) {
                        accuracyInput.value = '';
                    }
                    setStatus(
                        status,
                        `Precision baja (${Math.round(accuracy)} m). Acercate a una zona abierta y reintenta antes de guardar GPS.`,
                        'text-danger'
                    );
                    restoreButton();
                    return;
                }

                latitudeInput.value = position.coords.latitude.toFixed(6);
                longitudeInput.value = position.coords.longitude.toFixed(6);
                if (accuracyInput) {
                    accuracyInput.value = accuracy.toFixed(1);
                }
                setStatus(
                    status,
                    `${accuracyLabel(accuracy)} (${Math.round(accuracy)} m). Ubicacion capturada con precision estimada.`,
                    accuracy <= 80 ? 'text-success' : 'text-danger'
                );
            } catch (error) {
                console.warn('Error de geolocalizacion', {
                    code: error ? error.code : null,
                    message: error ? error.message : '',
                });
                setStatus(status, geolocationMessage(error), 'text-danger');
            } finally {
                restoreButton();
            }
        });
    });
})();
