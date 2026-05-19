from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import Cliente, Pedido, PushSubscription


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

    def test_repartidor_registra_y_desactiva_suscripcion(self):
        self.client.force_login(self.repartidor)
        payload = {
            'endpoint': 'https://push.example.com/subscription/1',
            'keys': {
                'p256dh': 'p256dh-value',
                'auth': 'auth-value',
            },
        }

        response = self.client.post(
            reverse('webpush_subscribe'),
            payload,
            content_type='application/json'
        )

        self.assertEqual(response.status_code, 200)
        subscription = PushSubscription.objects.get(
            endpoint=payload['endpoint']
        )
        self.assertEqual(subscription.user, self.repartidor)
        self.assertTrue(subscription.is_active)

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
