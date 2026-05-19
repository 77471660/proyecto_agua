(function () {
  console.log('pwa.js ejecutandose');

  let serviceWorkerRegistration = null;
  let subscriptionInProgress = false;

  function webpushButtons() {
    return Array.from(document.querySelectorAll('[data-webpush-toggle]'));
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

  function updateButtons(text, disabled) {
    webpushButtons().forEach(function (button) {
      button.textContent = text;
      button.disabled = Boolean(disabled);
    });
  }

  function showTemporaryAlert(message) {
    window.alert(message);
  }

  async function ensureServiceWorkerRegistration() {
    if (serviceWorkerRegistration) {
      return serviceWorkerRegistration;
    }

    if (!('serviceWorker' in navigator)) {
      console.log('Service worker no disponible');
      throw new Error('Service worker no disponible');
    }

    serviceWorkerRegistration = await navigator.serviceWorker.register('/service-worker.js');
    console.log('Service worker activo');
    return serviceWorkerRegistration;
  }

  async function subscribeToPush(button) {
    if (subscriptionInProgress) {
      return;
    }

    subscriptionInProgress = true;
    updateButtons('Activando...', true);

    try {
      if (!('PushManager' in window) || !('Notification' in window)) {
        console.log('Permiso notificaciones: no-disponible');
        updateButtons('Notificaciones no disponibles', true);
        showTemporaryAlert('Notificaciones no disponibles en este navegador.');
        return;
      }

      if (Notification.permission === 'denied') {
        console.log('Permiso notificaciones: denied');
        updateButtons('Notificaciones bloqueadas', true);
        showTemporaryAlert('Las notificaciones estan bloqueadas en este navegador.');
        return;
      }

      const permission = Notification.permission === 'granted'
        ? 'granted'
        : await Notification.requestPermission();
      console.log(`Permiso notificaciones: ${permission}`);

      if (permission !== 'granted') {
        updateButtons('Activar notificaciones', false);
        showTemporaryAlert('No se concedio permiso para notificaciones.');
        return;
      }

      const registration = await ensureServiceWorkerRegistration();
      const publicKeyResponse = await fetch(button.dataset.publicKeyUrl, {
        credentials: 'same-origin',
      });
      console.log(`Respuesta public-key: ${publicKeyResponse.status}`);
      const publicKeyData = await publicKeyResponse.json();

      if (!publicKeyResponse.ok || !publicKeyData.publicKey) {
        updateButtons('Push no configurado', true);
        showTemporaryAlert('No se pudo activar notificaciones: push no configurado.');
        return;
      }

      const existingSubscription = await registration.pushManager.getSubscription();
      const subscription = existingSubscription || await registration.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: urlBase64ToUint8Array(publicKeyData.publicKey),
      });

      console.log('Enviando PushSubscription al backend');
      const subscribeResponse = await postJson(
        button.dataset.subscribeUrl,
        subscription.toJSON()
      );
      console.log(`Respuesta subscribe: ${subscribeResponse.status}`);

      if (!subscribeResponse.ok) {
        updateButtons('Activar notificaciones', false);
        showTemporaryAlert('Error al activar notificaciones.');
        return;
      }

      console.log('PushSubscription enviada al backend');
      updateButtons('Notificaciones activas', true);
      showTemporaryAlert('Notificaciones activadas correctamente');
    } catch (error) {
      console.log('Error activando notificaciones', error);
      updateButtons('Activar notificaciones', false);
      showTemporaryAlert('Error al activar notificaciones.');
    } finally {
      subscriptionInProgress = false;
    }
  }

  document.addEventListener('click', function (event) {
    const button = event.target.closest('[data-webpush-toggle]');

    if (!button) {
      return;
    }

    event.preventDefault();
    event.stopPropagation();
    console.log('click activar notificaciones');
    console.log('Boton activar notificaciones clickeado');
    subscribeToPush(button);
  });

  window.addEventListener('load', function () {
    ensureServiceWorkerRegistration()
      .then(async function (registration) {
        const buttons = webpushButtons();

        if (!buttons.length) {
          console.log('Boton activar notificaciones no encontrado');
          return;
        }

        if (!('PushManager' in window) || !('Notification' in window)) {
          updateButtons('Notificaciones no disponibles', true);
          return;
        }

        const subscription = await registration.pushManager.getSubscription();

        if (subscription && Notification.permission === 'granted') {
          updateButtons('Notificaciones activas', true);
        }
      })
      .catch(function (error) {
        console.log('Error registrando service worker', error);
      });
  });
})();
