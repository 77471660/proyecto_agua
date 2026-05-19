from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import Cliente, Pedido, PushSubscription
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
        self.assertIn('android-chrome-192x192.png', payload['icon'])
        self.assertIn('favicon-96x96.png', payload['badge'])
        self.assertIn('timestamp', payload)
        self.assertIn('vibrate', payload)

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
        self.assertTrue(payload['renotify'])
        self.assertTrue(payload['requireInteraction'])

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
