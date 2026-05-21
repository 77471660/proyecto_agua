from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from pywebpush import WebPushException

from .models import Cliente, FCMToken, Pedido, PushSubscription
from .push_notifications import send_order_assignment_push, send_push_to_user


class WebPushTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.grupo_repartidor, _ = Group.objects.get_or_create(name='Repartidores')
        self.grupo_jefe_repartidores, _ = Group.objects.get_or_create(
            name='JefeRepartidores'
        )

        self.repartidor = User.objects.create_user(
            username='push-repartidor',
            password='clave-repartidor'
        )
        self.repartidor.groups.add(self.grupo_repartidor)

        self.jefe = User.objects.create_user(
            username='push-jefe',
            password='clave-jefe'
        )
        self.jefe.groups.add(self.grupo_jefe_repartidores)

        self.cliente = Cliente.objects.create(
            nombre='Cliente Push',
            telefono='999222333',
            direccion='Av. Push 123'
        )

    @override_settings(WEBPUSH_VAPID_PUBLIC_KEY='clave-publica')
    def test_repartidor_obtiene_clave_publica(self):
        self.client.force_login(self.repartidor)

        response = self.client.get(reverse('webpush_public_key'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['publicKey'], 'clave-publica')

    def test_pantalla_repartidor_muestra_boton_y_carga_pwa_js(self):
        self.client.force_login(self.repartidor)

        response = self.client.get(reverse('pedidos_repartidor'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-webpush-toggle')
        self.assertContains(response, 'Activar notificaciones')
        self.assertContains(response, 'js/pwa.js')

    def test_panel_jefe_muestra_activacion_en_acciones(self):
        self.client.force_login(self.jefe)

        response = self.client.get(reverse('panel_jefe_repartidores'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'navbar-actions-mobile')
        self.assertContains(response, 'data-webpush-toggle')
        self.assertContains(response, 'Activar notificaciones')

    def test_base_carga_js_fcm_capacitor_sin_reemplazar_webpush(self):
        self.client.force_login(self.jefe)

        response = self.client.get(reverse('panel_jefe_repartidores'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'js/pwa.js')
        self.assertContains(response, 'js/capacitor_push.js')

    def test_pwa_js_sincroniza_suscripcion_real_con_backend(self):
        pwa_js = (
            Path(settings.BASE_DIR)
            / 'static'
            / 'js'
            / 'pwa.js'
        ).read_text(encoding='utf-8')

        self.assertIn('navigator.serviceWorker.ready', pwa_js)
        self.assertIn('registration.pushManager.subscribe', pwa_js)
        self.assertIn('sendSubscriptionToBackend', pwa_js)
        self.assertIn('Push subscription existente detectada; sincronizando backend', pwa_js)
        self.assertIn('Permiso granted sin push subscription; recreando suscripcion', pwa_js)
        self.assertIn("button.dataset.webpushState === 'renew'", pwa_js)
        self.assertIn("const ACTIVE_PUSH_TEXT = '\\u{1F514} Notificaciones activas';", pwa_js)
        self.assertIn("const INACTIVE_PUSH_TEXT = '\\u26A0\\uFE0F Activar notificaciones';", pwa_js)
        self.assertIn("const RENEW_PUSH_TEXT = '\\u{1F504} Renovar notificaciones';", pwa_js)
        self.assertIn('setActivePushState();', pwa_js)

    def test_titulo_pedidos_hoy_es_subtitulo_discreto(self):
        self.client.force_login(self.repartidor)

        response = self.client.get(reverse('pedidos_repartidor'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'orders-section-title')
        self.assertContains(response, 'font-size: 16px;')
        self.assertContains(response, 'font-weight: 600;')
        self.assertContains(response, 'color: #64748b;')
        self.assertContains(response, 'margin: 18px 0 10px;')

    def test_repartidor_registra_y_desactiva_suscripcion(self):
        self.client.force_login(self.repartidor)
        payload = {
            'endpoint': 'https://push.example.com/subscription/1',
            'keys': {
                'p256dh': 'p256dh-value',
                'auth': 'auth-value',
            },
        }

        with self.assertLogs('core.views', level='INFO') as logs:
            response = self.client.post(
                reverse('webpush_subscribe'),
                payload,
                content_type='application/json'
            )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            any(
                f'PushSubscription guardada usuario={self.repartidor.id}' in line
                for line in logs.output
            )
        )
        subscription = PushSubscription.objects.get(
            endpoint=payload['endpoint']
        )
        self.assertEqual(subscription.user, self.repartidor)
        self.assertTrue(subscription.is_active)
        self.assertEqual(subscription.last_error, '')

        response = self.client.post(
            reverse('webpush_unsubscribe'),
            {'endpoint': payload['endpoint']},
            content_type='application/json'
        )

        self.assertEqual(response.status_code, 200)
        subscription.refresh_from_db()
        self.assertFalse(subscription.is_active)

    def test_usuario_autenticado_registra_token_fcm(self):
        self.client.force_login(self.repartidor)
        payload = {
            'token': 'fcm-token-abc-123',
            'platform': 'android',
            'deviceId': 'device-1',
        }

        with self.assertLogs('core.views', level='INFO') as logs:
            response = self.client.post(
                reverse('fcm_register_token'),
                payload,
                content_type='application/json'
            )

        self.assertEqual(response.status_code, 200)
        token = FCMToken.objects.get(token=payload['token'])
        self.assertEqual(token.user, self.repartidor)
        self.assertEqual(token.platform, 'android')
        self.assertEqual(token.device_id, 'device-1')
        self.assertTrue(token.is_active)
        self.assertEqual(token.last_error, '')
        self.assertTrue(
            any('FCM token guardado' in line for line in logs.output)
        )

    def test_token_fcm_duplicado_se_actualiza_sin_crear_otro(self):
        FCMToken.objects.create(
            user=self.jefe,
            token='fcm-token-duplicado',
            platform='android',
            device_id='old-device',
            is_active=False,
            last_error='token viejo'
        )
        self.client.force_login(self.repartidor)

        response = self.client.post(
            reverse('fcm_register_token'),
            {
                'token': 'fcm-token-duplicado',
                'platform': 'android',
                'deviceId': 'new-device',
            },
            content_type='application/json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(FCMToken.objects.count(), 1)
        token = FCMToken.objects.get(token='fcm-token-duplicado')
        self.assertEqual(token.user, self.repartidor)
        self.assertEqual(token.device_id, 'new-device')
        self.assertTrue(token.is_active)
        self.assertEqual(token.last_error, '')

    def test_registro_fcm_requiere_login(self):
        response = self.client.post(
            reverse('fcm_register_token'),
            {'token': 'fcm-token-anonimo'},
            content_type='application/json'
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(FCMToken.objects.exists())

    def test_secretaria_no_puede_registrar_suscripcion(self):
        User = get_user_model()
        secretaria = User.objects.create_user(
            username='push-secretaria',
            password='clave-secretaria'
        )
        secretaria.groups.add(Group.objects.create(name='Secretaria'))
        self.client.force_login(secretaria)

        response = self.client.post(
            reverse('webpush_subscribe'),
            {
                'endpoint': 'https://push.example.com/subscription/2',
                'keys': {'p256dh': 'p256dh-value', 'auth': 'auth-value'},
            },
            content_type='application/json'
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(PushSubscription.objects.exists())

    def test_asignar_pedido_dispara_push_event_driven(self):
        pedido = Pedido.objects.create(
            cliente=self.cliente,
            cantidad_bidones=2,
            precio_unitario=Decimal('7.00'),
            total=Decimal('14.00'),
            fecha_programada=timezone.localdate()
        )
        self.client.force_login(self.jefe)

        with patch('core.views.send_order_assignment_push') as send_push:
            response = self.client.post(
                reverse(
                    'asignar_pedido_repartidor',
                    kwargs={'pedido_id': pedido.id}
                ),
                {'repartidor': str(self.repartidor.id)}
            )

        pedido.refresh_from_db()
        self.assertRedirects(
            response,
            reverse('panel_jefe_repartidores'),
            fetch_redirect_response=False
        )
        self.assertEqual(pedido.repartidor, self.repartidor)
        send_push.assert_called_once()

    def test_payload_de_pedido_asignado_abre_ancla_del_pedido(self):
        pedido = Pedido.objects.create(
            cliente=self.cliente,
            repartidor=self.repartidor,
            cantidad_bidones=2,
            precio_unitario=Decimal('7.00'),
            total=Decimal('14.00'),
            fecha_programada=timezone.localdate()
        )

        with patch('core.push_notifications.send_push_to_user') as send_push:
            send_order_assignment_push(pedido)

        send_push.assert_called_once()
        user, payload = send_push.call_args.args
        self.assertEqual(user, self.repartidor)
        self.assertEqual(payload['title'], 'Pedido asignado')
        self.assertEqual(payload['tag'], f'pedido-{pedido.id}-asignado')
        self.assertEqual(payload['url'], f'/pedidos/repartidor/#pedido-{pedido.id}')
        self.assertFalse(payload['renotify'])
        self.assertFalse(payload['requireInteraction'])
        self.assertIn(f'Cliente: {self.cliente.nombre}', payload['body'])
        self.assertIn('2 bidones', payload['body'])
        self.assertIn(f'Entrega en {self.cliente.direccion}', payload['body'])
        self.assertNotIn(f'Pedido #{pedido.id}', payload['body'])
        self.assertIn('android-chrome-192x192.png', payload['icon'])
        self.assertIn('notification-bidon.png', payload['badge'])
        self.assertIn('timestamp', payload)
        self.assertIn('vibrate', payload)

    def test_iconos_push_android_existen_y_no_reusan_favicon_como_badge(self):
        icons_dir = Path(settings.BASE_DIR) / 'static' / 'img' / 'icons'
        service_worker = (
            Path(settings.BASE_DIR)
            / 'static'
            / 'service-worker.js'
        ).read_text(encoding='utf-8')

        self.assertTrue((icons_dir / 'android-chrome-192x192.png').exists())
        self.assertTrue((icons_dir / 'notification-bidon.png').exists())
        self.assertIn('/static/img/icons/android-chrome-192x192.png', service_worker)
        self.assertIn('/static/img/icons/notification-bidon.png', service_worker)
        self.assertNotIn("badge: data.badge || '/static/img/icons/favicon-96x96.png'", service_worker)

    def test_payload_de_pedido_reasignado_renotifica(self):
        User = get_user_model()
        repartidor_anterior = User.objects.create_user(
            username='push-anterior',
            password='clave-repartidor'
        )
        repartidor_anterior.groups.add(self.grupo_repartidor)
        pedido = Pedido.objects.create(
            cliente=self.cliente,
            repartidor=self.repartidor,
            cantidad_bidones=2,
            precio_unitario=Decimal('7.00'),
            total=Decimal('14.00'),
            fecha_programada=timezone.localdate()
        )

        with patch('core.push_notifications.send_push_to_user') as send_push:
            send_order_assignment_push(
                pedido,
                previous_repartidor=repartidor_anterior
            )

        payload = send_push.call_args.args[1]
        self.assertEqual(payload['title'], 'Pedido reasignado')
        self.assertEqual(payload['tag'], f'pedido-{pedido.id}-reasignado')
        self.assertIn(f'Cliente: {self.cliente.nombre}', payload['body'])
        self.assertIn(f'Entrega en {self.cliente.direccion}', payload['body'])
        self.assertNotIn(f'Pedido #{pedido.id}', payload['body'])
        self.assertTrue(payload['renotify'])
        self.assertTrue(payload['requireInteraction'])

    def test_sesion_y_safe_area_estan_configurados_para_apk(self):
        base_template = (
            Path(settings.BASE_DIR)
            / 'templates'
            / 'base.html'
        ).read_text(encoding='utf-8')
        repartidor_template = (
            Path(settings.BASE_DIR)
            / 'templates'
            / 'core'
            / 'pedidos_repartidor.html'
        ).read_text(encoding='utf-8')
        capacitor_js = (
            Path(settings.BASE_DIR)
            / 'static'
            / 'js'
            / 'capacitor_push.js'
        ).read_text(encoding='utf-8')

        self.assertGreaterEqual(settings.SESSION_COOKIE_AGE, 60 * 60 * 24 * 30)
        self.assertFalse(settings.SESSION_EXPIRE_AT_BROWSER_CLOSE)
        self.assertTrue(settings.SESSION_SAVE_EVERY_REQUEST)
        self.assertIn('safe-area-inset-top', base_template)
        self.assertIn('safe-area-inset-bottom', base_template)
        self.assertIn('safe-area-inset-top', repartidor_template)
        self.assertIn('safe-area-inset-bottom', repartidor_template)
        self.assertIn("classList.add('capacitor-android')", capacitor_js)

    @override_settings(
        WEBPUSH_VAPID_PUBLIC_KEY='clave-publica',
        WEBPUSH_VAPID_PRIVATE_KEY='clave-privada',
        WEBPUSH_VAPID_SUBJECT='mailto:villarcalderondaniel@gmail.com'
    )
    def test_error_de_pywebpush_queda_registrado_en_suscripcion(self):
        subscription = PushSubscription.objects.create(
            user=self.repartidor,
            endpoint='https://push.example.com/subscription/error',
            p256dh='p256dh-value',
            auth='auth-value'
        )

        with self.assertLogs('core.push_notifications', level='ERROR'):
            with patch('pywebpush.webpush', side_effect=RuntimeError('boom diag')):
                send_push_to_user(
                    self.repartidor,
                    {
                        'title': 'Pedido asignado',
                        'body': 'Pedido #1 asignado a tu reparto.',
                        'url': '/pedidos/repartidor/',
                        'tag': 'pedido-1-asignacion',
                    }
                )

        subscription.refresh_from_db()
        self.assertIn('boom diag', subscription.last_error)

    @override_settings(
        WEBPUSH_VAPID_PUBLIC_KEY='clave-publica',
        WEBPUSH_VAPID_PRIVATE_KEY='clave-privada',
        WEBPUSH_VAPID_SUBJECT='mailto:villarcalderondaniel@gmail.com'
    )
    def test_error_410_desactiva_suscripcion_expirada(self):
        class FakeResponse:
            status_code = 410

        subscription = PushSubscription.objects.create(
            user=self.repartidor,
            endpoint='https://push.example.com/subscription/gone',
            p256dh='p256dh-value',
            auth='auth-value'
        )

        with self.assertLogs('core.push_notifications', level='WARNING') as logs:
            with patch(
                'pywebpush.webpush',
                side_effect=WebPushException('gone', response=FakeResponse())
            ):
                send_push_to_user(
                    self.repartidor,
                    {
                        'title': 'Pedido reasignado',
                        'body': 'Pedido #1 reasignado a tu reparto.',
                        'url': '/pedidos/repartidor/',
                        'tag': 'pedido-1-reasignado',
                    }
                )

        subscription.refresh_from_db()
        self.assertFalse(subscription.is_active)
        self.assertIn('status=410', subscription.last_error)
        self.assertTrue(
            any(
                'Web Push suscripcion invalida desactivada' in line
                for line in logs.output
            )
        )
