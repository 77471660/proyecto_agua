(function () {
  const capacitor = window.Capacitor;

  function log(message, extra) {
    if (extra !== undefined) {
      console.log(`[FCM APK] ${message}`, extra);
    } else {
      console.log(`[FCM APK] ${message}`);
    }
  }

  function tokenForLog(token) {
    if (!token) {
      return 'sin-token';
    }

    if (token.length <= 24) {
      return token;
    }

    return `${token.slice(0, 12)}...${token.slice(-8)}`;
  }

  function isNativeCapacitor() {
    return Boolean(
      capacitor
      && (
        (typeof capacitor.isNativePlatform === 'function'
          && capacitor.isNativePlatform())
        || capacitor.getPlatform?.() === 'android'
      )
    );
  }

  function markNativeShell() {
    if (!isNativeCapacitor()) {
      return false;
    }

    document.documentElement.classList.add('capacitor-native');

    if (capacitor.getPlatform?.() === 'android') {
      document.documentElement.classList.add('capacitor-android');
    }

    return true;
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

  function getDeviceId() {
    const storageKey = 'aquasmart_capacitor_device_id';
    let deviceId = window.localStorage.getItem(storageKey);

    if (!deviceId) {
      deviceId = (
        window.crypto && window.crypto.randomUUID
          ? window.crypto.randomUUID()
          : `apk-${Date.now()}-${Math.random().toString(16).slice(2)}`
      );
      window.localStorage.setItem(storageKey, deviceId);
    }

    return deviceId;
  }

  async function postToken(token) {
    const deviceId = getDeviceId();
    log('Enviando token FCM al backend', {
      token: tokenForLog(token),
      deviceId,
      platform: 'android',
    });
    const response = await fetch('/fcm/register-token/', {
      method: 'POST',
      credentials: 'same-origin',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': getCookie('csrftoken'),
      },
      body: JSON.stringify({
        token,
        platform: 'android',
        deviceId,
      }),
    });

    let responseBody = {};
    try {
      responseBody = await response.json();
    } catch (error) {
      responseBody = { parseError: String(error) };
    }

    log(`Respuesta registro token FCM: ${response.status}`, responseBody);

    if (!response.ok) {
      throw new Error(`Registro FCM fallo con status ${response.status}`);
    }

    log('Token FCM registrado en backend', responseBody);
  }

  function openNotificationTarget(notification) {
    const data = notification && notification.notification
      ? notification.notification.data
      : notification && notification.data;
    const targetUrl = data && data.url ? data.url : '/pedidos/repartidor/';
    const url = new URL(targetUrl, window.location.origin);

    log(`Abriendo notificacion FCM: ${url.href}`);
    window.location.href = url.href;
  }

  async function registerPushNotifications() {
    if (!isNativeCapacitor()) {
      return;
    }

    const pushNotifications = capacitor.Plugins?.PushNotifications;

    if (!pushNotifications) {
      log('Plugin PushNotifications no disponible');
      return;
    }

    log('Solicitando permiso Android para notificaciones');
    const permissionStatus = await pushNotifications.requestPermissions();
    log('Permiso notificaciones nativas', permissionStatus);

    if (permissionStatus.receive !== 'granted') {
      log('Permiso FCM no concedido');
      return;
    }

    await pushNotifications.addListener('registration', async function (token) {
      log('registration token FCM recibido', {
        token: tokenForLog(token.value),
      });
      try {
        await postToken(token.value);
      } catch (error) {
        log('Error enviando token FCM al backend', error);
      }
    });

    await pushNotifications.addListener('registrationError', function (error) {
      log('registrationError FCM', error);
    });

    await pushNotifications.addListener('pushNotificationReceived', function (notification) {
      log('pushNotificationReceived', notification);
    });

    await pushNotifications.addListener('pushNotificationActionPerformed', function (notification) {
      log('pushNotificationActionPerformed', notification);
      openNotificationTarget(notification);
    });

    log('Registrando PushNotifications nativo');
    await pushNotifications.register();
  }

  window.addEventListener('load', function () {
    markNativeShell();
    registerPushNotifications().catch(function (error) {
      log('Error inicializando FCM APK', error);
    });
  });

  markNativeShell();
})();
