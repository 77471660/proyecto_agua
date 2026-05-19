(function () {
  console.log('pwa.js ejecutándose');

  if (!('serviceWorker' in navigator)) {
    console.log('Service worker no disponible');
    return;
  }

  function getCookie(name) {
    const value = `; ${document.cookie}`;
    const parts = value.split(`; ${name}=`);

    if (parts.length === 2) {
      return parts.pop().split(';').shift();
    }

    const csrfInput = document.querySelector('[name=csrfmiddlewaretoken]');

    if (csrfInput) {
      return csrfInput.value;
    }

    return '';
  }

  function urlBase64ToUint8Array(base64String) {
    const padding = '='.repeat((4 - base64String.length % 4) % 4);
    const base64 = (base64String + padding)
      .replace(/-/g, '+')
      .replace(/_/g, '/');
    const rawData = window.atob(base64);
    const outputArray = new Uint8Array(rawData.length);

    for (let i = 0; i < rawData.length; i += 1) {
      outputArray[i] = rawData.charCodeAt(i);
    }

    return outputArray;
  }

  function postJson(url, data) {
    const csrfToken = getCookie('csrftoken');
    console.log(`CSRF token disponible: ${Boolean(csrfToken)}`);

    return fetch(url, {
      method: 'POST',
      credentials: 'same-origin',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': csrfToken,
      },
      body: JSON.stringify(data),
    });
  }

  function updateButtons(buttons, text, disabled) {
    if (!buttons || !buttons.length) {
      return;
    }

    buttons.forEach(function (button) {
      button.textContent = text;
      button.disabled = Boolean(disabled);
    });
  }

  async function subscribeToPush(registration, buttons) {
    const button = buttons[0];

    if (!('PushManager' in window) || !('Notification' in window)) {
      console.log('Permiso notificaciones: no-disponible');
      updateButtons(buttons, 'Notificaciones no disponibles', true);
      return;
    }

    if (Notification.permission === 'denied') {
      console.log('Permiso notificaciones: denied');
      updateButtons(buttons, 'Notificaciones bloqueadas', true);
      return;
    }

    const permission = Notification.permission === 'granted'
      ? 'granted'
      : await Notification.requestPermission();
    console.log(`Permiso notificaciones: ${permission}`);

    if (permission !== 'granted') {
      updateButtons(buttons, 'Activar notificaciones', false);
      return;
    }

    updateButtons(buttons, 'Activando...', true);

    const publicKeyResponse = await fetch(button.dataset.publicKeyUrl, {
      credentials: 'same-origin',
    });
    console.log(`Respuesta public-key: ${publicKeyResponse.status}`);
    const publicKeyData = await publicKeyResponse.json();

    if (!publicKeyData.publicKey) {
      updateButtons(buttons, 'Push no configurado', true);
      return;
    }

    const existingSubscription = await registration.pushManager.getSubscription();
    const subscription = existingSubscription || await registration.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: urlBase64ToUint8Array(publicKeyData.publicKey),
    });

    const subscribeResponse = await postJson(
      button.dataset.subscribeUrl,
      subscription.toJSON()
    );

    if (!subscribeResponse.ok) {
      console.log(`Error enviando PushSubscription al backend: ${subscribeResponse.status}`);
      updateButtons(buttons, 'Activar notificaciones', false);
      return;
    }

    console.log('PushSubscription enviada al backend');
    updateButtons(buttons, 'Notificaciones activas', true);
  }

  window.addEventListener('load', function () {
    navigator.serviceWorker.register('/service-worker.js')
      .then(async function (registration) {
        console.log('Service worker activo');
        const buttons = Array.from(document.querySelectorAll('[data-webpush-toggle]'));

        if (!buttons.length) {
          console.log('Botón activar notificaciones no encontrado');
          return;
        }

        if (!('PushManager' in window) || !('Notification' in window)) {
          updateButtons(buttons, 'Notificaciones no disponibles', true);
          return;
        }

        const subscription = await registration.pushManager.getSubscription();

        if (subscription && Notification.permission === 'granted') {
          updateButtons(buttons, 'Notificaciones activas', true);
          return;
        }

        buttons.forEach(function (button) {
          button.addEventListener('click', function () {
            console.log('Botón activar notificaciones clickeado');
            subscribeToPush(registration, buttons).catch(function () {
              updateButtons(buttons, 'Activar notificaciones', false);
            });
          });
        });
      });
  });
})();
