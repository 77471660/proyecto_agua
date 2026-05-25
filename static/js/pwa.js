(function () {
  console.log('pwa.js ejecutandose');

  let serviceWorkerRegistration = null;
  let subscriptionInProgress = false;
  const ACTIVE_PUSH_TEXT = '\u{1F514} Notificaciones activas';
  const ANDROID_PUSH_TEXT = '\u{1F514} Notificaciones Android activas';
  const INACTIVE_PUSH_TEXT = '\u26A0\uFE0F Activar notificaciones';
  const RENEW_PUSH_TEXT = '\u{1F504} Renovar notificaciones';

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

  function updateButtons(text, disabled, state) {
    webpushButtons().forEach(function (button) {
      button.textContent = text;
      button.disabled = Boolean(disabled);
      button.dataset.webpushState = state || 'inactive';
      button.dataset.webpushActive = state === 'active' ? 'true' : 'false';
    });
  }

  function setInactivePushState() {
    updateButtons(INACTIVE_PUSH_TEXT, false, 'inactive');
  }

  function setActivePushState() {
    updateButtons(ACTIVE_PUSH_TEXT, true, 'active');
  }

  function setRenewPushState() {
    updateButtons(RENEW_PUSH_TEXT, false, 'renew');
  }

  function setUnsupportedPushState() {
    if (document.documentElement.classList.contains('capacitor-android')) {
      updateButtons(ANDROID_PUSH_TEXT, true, 'active');
      return true;
    }

    updateButtons('Notificaciones no disponibles', true, 'unsupported');
    return false;
  }

  function showTemporaryAlert(message, silent) {
    if (!silent) {
      window.alert(message);
    }
  }

  async function ensureServiceWorkerRegistration() {
    if (serviceWorkerRegistration) {
      return serviceWorkerRegistration;
    }

    if (!('serviceWorker' in navigator)) {
      console.log('Service worker no disponible');
      throw new Error('Service worker no disponible');
    }

    await navigator.serviceWorker.register('/service-worker.js');
    serviceWorkerRegistration = await navigator.serviceWorker.ready;
    console.log('Service worker activo');
    return serviceWorkerRegistration;
  }

  async function sendSubscriptionToBackend(button, subscription) {
    console.log('Enviando PushSubscription al backend');
    const subscribeResponse = await postJson(
      button.dataset.subscribeUrl,
      subscription.toJSON()
    );
    console.log(`Respuesta subscribe: ${subscribeResponse.status}`);

    if (!subscribeResponse.ok) {
      throw new Error(`Subscribe backend fallo con status ${subscribeResponse.status}`);
    }

    console.log('PushSubscription enviada al backend');
    return subscribeResponse;
  }

  async function createPushSubscription(registration, publicKeyData, forceRenew) {
    const existingSubscription = await registration.pushManager.getSubscription();
    console.log(`Existe push subscription: ${Boolean(existingSubscription)}`);

    if (existingSubscription && forceRenew) {
      console.log('Renovando PushSubscription existente');
      await existingSubscription.unsubscribe();
    } else if (existingSubscription) {
      return existingSubscription;
    }

    console.log('Ejecutando pushManager.subscribe');
    return registration.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: urlBase64ToUint8Array(publicKeyData.publicKey),
    });
  }

  async function subscribeToPush(button, options) {
    if (subscriptionInProgress) {
      return;
    }

    const forceRenew = Boolean(options && options.forceRenew);
    const silent = Boolean(options && options.silent);
    subscriptionInProgress = true;
    updateButtons(forceRenew ? 'Renovando...' : 'Activando...', true, 'loading');

    try {
      if (!('PushManager' in window) || !('Notification' in window)) {
        console.log('Permiso notificaciones: no-disponible');
        if (!setUnsupportedPushState()) {
          showTemporaryAlert('Notificaciones no disponibles en este navegador.', silent);
        }
        return;
      }

      if (Notification.permission === 'denied') {
        console.log('Permiso notificaciones: denied');
        updateButtons('Notificaciones bloqueadas', true, 'blocked');
        showTemporaryAlert('Las notificaciones estan bloqueadas en este navegador.', silent);
        return;
      }

      const permission = Notification.permission === 'granted'
        ? 'granted'
        : await Notification.requestPermission();
      console.log(`Permiso notificaciones: ${permission}`);

      if (permission !== 'granted') {
        setInactivePushState();
        showTemporaryAlert('No se concedio permiso para notificaciones.', silent);
        return;
      }

      const registration = await ensureServiceWorkerRegistration();
      const publicKeyResponse = await fetch(button.dataset.publicKeyUrl, {
        credentials: 'same-origin',
      });
      console.log(`Respuesta public-key: ${publicKeyResponse.status}`);
      const publicKeyData = await publicKeyResponse.json();

      if (!publicKeyResponse.ok || !publicKeyData.publicKey) {
        updateButtons('Push no configurado', true, 'unconfigured');
        showTemporaryAlert('No se pudo activar notificaciones: push no configurado.', silent);
        return;
      }

      const subscription = await createPushSubscription(
        registration,
        publicKeyData,
        forceRenew
      );
      await sendSubscriptionToBackend(button, subscription);

      if (forceRenew) {
        console.log('Suscripcion renovada');
      }

      setActivePushState();
      showTemporaryAlert(
        forceRenew
          ? 'Notificaciones renovadas correctamente'
          : 'Notificaciones activadas correctamente',
        silent
      );
    } catch (error) {
      console.log('Error activando notificaciones', error);

      if (forceRenew) {
        setRenewPushState();
      } else {
        setInactivePushState();
      }

      showTemporaryAlert('Error al activar notificaciones.', silent);
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

    if (button.disabled) {
      return;
    }

    console.log('click activar notificaciones');
    console.log('Boton activar notificaciones clickeado');
    subscribeToPush(button, {
      forceRenew: button.dataset.webpushState === 'renew',
    });
  });

  window.addEventListener('load', function () {
    if (document.documentElement.classList.contains('capacitor-android')) {
      setUnsupportedPushState();
      return;
    }

    ensureServiceWorkerRegistration()
      .then(async function (registration) {
        const buttons = webpushButtons();

        if (!buttons.length) {
          console.log('Boton activar notificaciones no encontrado');
          return;
        }

        if (!('PushManager' in window) || !('Notification' in window)) {
          setUnsupportedPushState();
          return;
        }

        const subscription = await registration.pushManager.getSubscription();
        console.log(`Existe push subscription al cargar: ${Boolean(subscription)}`);
        console.log(`Permiso notificaciones al cargar: ${Notification.permission}`);

        if (subscription && Notification.permission === 'granted') {
          console.log('Push subscription existente detectada; sincronizando backend');

          try {
            await sendSubscriptionToBackend(buttons[0], subscription);
            setActivePushState();
          } catch (error) {
            console.log('No se pudo sincronizar PushSubscription existente', error);
            setRenewPushState();
          }
        } else if (!subscription && Notification.permission === 'granted') {
          console.log('Permiso granted sin push subscription; recreando suscripcion');
          await subscribeToPush(buttons[0], { silent: true });
        } else {
          setInactivePushState();
        }
      })
      .catch(function (error) {
        console.log('Error registrando service worker', error);
      });
  });
})();
