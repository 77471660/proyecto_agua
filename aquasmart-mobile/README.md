# AquaSmart Mobile

Contenedor Android Capacitor para cargar la PWA actual de AquaSmart CRM:

https://proyecto-agua-bkco.onrender.com/

## Requisitos

- Node.js con npm
- Android Studio
- Android SDK
- JDK compatible con Android Gradle Plugin

## Instalacion

```powershell
cd aquasmart-mobile
npm install
npx cap sync android
npx cap open android
```

La carpeta `android/` ya existe en este workspace. Si se trabaja desde un clon limpio
donde no exista, generarla una sola vez con:

```powershell
cd aquasmart-mobile
npm install
npx cap add android
```

Despues de cambios web, ejecutar siempre:

```powershell
cd aquasmart-mobile
npx cap sync android
```

## Compilar APK

En Android Studio:

1. Esperar a que Gradle sincronice.
2. Ir a `Build > Build Bundle(s) / APK(s) > Build APK(s)`.
3. El APK se generara dentro de `android/app/build/outputs/apk/`.

## Configuracion actual

- App name: `AquaSmart`
- Package id: `com.aquasmart.crm`
- URL remota: `https://proyecto-agua-bkco.onrender.com/`
- HTTPS habilitado: `server.cleartext = false`
- WebView carga el sitio remoto desde `server.url`
- `hostname` no esta configurado porque se usa servidor remoto HTTPS.
- Proyecto Django: no se modifica.

## Versionado Android

La carpeta `android/` se versiona porque contiene configuracion nativa necesaria para
el APK, incluyendo permisos de ubicacion en `AndroidManifest.xml`.

No versionar archivos generados o locales:

- `android/.gradle/`
- `android/build/`
- `android/app/build/`
- `android/local.properties`
- `android/app/google-services.json`
- `android/capacitor-cordova-android-plugins/`
- assets web copiados por `npx cap sync android`

Si se regenera `android/`, volver a comprobar el manifest y ejecutar:

```powershell
git status --short --ignored aquasmart-mobile
```

## Ubicacion GPS en Android

La captura GPS se hace con `navigator.geolocation` dentro del WebView, sin plugins
adicionales. No agregar plugin nativo mientras esta ruta funcione.

Capacitor Android 7 habilita geolocalizacion en el WebView y maneja el prompt nativo
desde su `BridgeWebChromeClient`. La app solo necesita manifest correcto, HTTPS y que
el usuario conceda permisos en Android.

`android/app/src/main/AndroidManifest.xml` debe incluir:

```xml
<uses-permission android:name="android.permission.ACCESS_FINE_LOCATION" />
<uses-permission android:name="android.permission.ACCESS_COARSE_LOCATION" />
```

Tambien debe mantenerse:

```xml
<uses-permission android:name="android.permission.INTERNET" />
```

`navigator.geolocation` requiere contexto seguro. La app usa `https://proyecto-agua-bkco.onrender.com/`,
por lo que cumple este requisito dentro del WebView. En Android 12+ el usuario puede
conceder ubicacion aproximada; por eso la app permite guardar direccion y referencia
manual aunque el GPS falle.

## Checklist de prueba GPS en APK real

1. Ejecutar `npx cap sync android`.
2. Abrir Android Studio con `npx cap open android`.
3. Compilar e instalar la APK en un telefono fisico.
4. Abrir la app e iniciar sesion.
5. Ir a formulario de cliente.
6. Tocar `Usar ubicacion actual`.
7. Aceptar el permiso de ubicacion de Android.
8. Verificar que se llenen latitud y longitud.
9. Guardar el cliente.
10. Crear o abrir un pedido de ese cliente.
11. Tocar `Abrir ubicacion`.
12. Confirmar que Android abre Google Maps o el selector de apps compatible.
13. Volver al formulario y probar permiso denegado.
14. Probar con GPS/ubicacion del sistema apagada.
15. Verificar que el formulario permita guardar direccion manual aunque falle GPS.

## Problemas comunes GPS

- No aparece el prompt de permiso: revisar que el Manifest tenga `ACCESS_FINE_LOCATION`
  y `ACCESS_COARSE_LOCATION`, luego ejecutar `npx cap sync android` y reinstalar APK.
- El navegador/WebView dice que ubicacion no esta disponible: confirmar que `server.url`
  use HTTPS y que el GPS del telefono este activado.
- Android devuelve ubicacion aproximada: es comportamiento normal en Android 12+ si el
  usuario no concede ubicacion precisa.
- El boton no llena coordenadas en emulador: configurar una ubicacion simulada en Android
  Studio o probar en telefono fisico.
- Google Maps no abre: instalar/activar Google Maps o probar con el selector de apps;
  el enlace es externo y no usa Google Maps SDK ni APIs pagas.

## Notificaciones en APK

La PWA en Chrome usa Web Push con service worker y `PushManager`.
Dentro de Android WebView/Capacitor, `PushManager` no esta disponible como en Chrome,
por eso la interfaz web puede mostrar `Notificaciones no disponibles`.

Para notificaciones reales en la APK se usa una capa nativa:

1. Crear proyecto Firebase para `com.aquasmart.crm`.
2. Descargar `google-services.json` y colocarlo en `android/app/google-services.json`.
3. Instalar `@capacitor/push-notifications`.
4. Registrar el token FCM desde la APK.
5. Guardar el token en Django via `/fcm/register-token/`.
6. Enviar notificaciones nativas via Firebase Cloud Messaging cuando se asigne o reasigne un pedido.

Este flujo esta separado de Web Push para no romper Chrome/PWA.

En Render/Django se debe configurar una de estas variables:

- `FIREBASE_CREDENTIALS_JSON`: JSON completo de service account en una sola variable.
- `FIREBASE_CREDENTIALS_PATH`: ruta al archivo JSON de service account.

No hardcodear credenciales Firebase dentro del repositorio.

## Iconos Android

Los recursos de launcher y splash se generan desde:

`../static/img/icons/android-chrome-512x512.png`
