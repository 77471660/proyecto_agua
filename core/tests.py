from datetime import timedelta
from decimal import Decimal
from io import BytesIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from .models import Cliente, Pedido, PedidoHistorial
from .templatetags.phone_format import format_phone_display
from .views import repartidores_disponibles


class PermisosRolesTests(TestCase):
    def setUp(self):
        User = get_user_model()

        self.grupo_admin, _ = Group.objects.get_or_create(name='ADMIN')
        self.grupo_repartidor, _ = Group.objects.get_or_create(name='Repartidores')
        self.grupo_repartidor_legacy, _ = Group.objects.get_or_create(
            name='Repartidor'
        )
        self.grupo_jefe_repartidores, _ = Group.objects.get_or_create(
            name='JefeRepartidores'
        )

        self.secretaria = User.objects.create_user(
            username='ADMINISTRADOR',
            password='clave-secreta'
        )
        self.secretaria.groups.add(self.grupo_admin)

        self.repartidor = User.objects.create_user(
            username='ECOAGUA',
            password='clave-repartidor'
        )
        self.repartidor.groups.add(self.grupo_repartidor)

        self.otro_repartidor = User.objects.create_user(
            username='OTRO',
            password='clave-repartidor'
        )
        self.otro_repartidor.groups.add(self.grupo_repartidor)

        self.jefe_reparto = User.objects.create_user(
            username='HERAL',
            password='clave-jefe'
        )
        self.jefe_reparto.groups.add(self.grupo_jefe_repartidores)

        self.daniel = User.objects.create_superuser(
            username='DANIEL',
            password='clave-daniel',
            email='daniel@example.com'
        )

        self.cliente = Cliente.objects.create(
            nombre='Cliente Uno',
            telefono='999111222',
            direccion='Av. Agua 123'
        )

    def crear_pedido(self, repartidor, cliente=None, fecha_programada=None):
        return Pedido.objects.create(
            cliente=cliente or self.cliente,
            repartidor=repartidor,
            cantidad_bidones=2,
            precio_unitario=Decimal('7.00'),
            total=Decimal('14.00'),
            fecha_programada=fecha_programada or timezone.localdate()
        )

    def crear_foto_prueba(self, name='referencia.jpg', content_type='image/jpeg'):
        archivo = BytesIO()
        Image.new('RGB', (1200, 800), color=(31, 120, 180)).save(
            archivo,
            format='JPEG'
        )
        archivo.seek(0)
        return SimpleUploadedFile(
            name,
            archivo.read(),
            content_type=content_type
        )

    def test_cliente_maps_url_prioriza_coordenadas(self):
        self.cliente.latitud = Decimal('-12.046374')
        self.cliente.longitud = Decimal('-77.042793')

        self.assertTrue(self.cliente.tiene_coordenadas())
        self.assertEqual(
            self.cliente.obtener_maps_url(),
            (
                'https://www.google.com/maps/search/?api=1&query='
                '-12.046374,-77.042793'
            )
        )

    def test_format_phone_display_agrupa_numero_de_nueve_digitos(self):
        self.assertEqual(
            format_phone_display('999111222'),
            '999 111 222'
        )
        self.assertEqual(
            format_phone_display('+51 999-111-222'),
            '999 111 222'
        )

    def test_cliente_maps_url_usa_direccion_sin_coordenadas(self):
        self.assertFalse(self.cliente.tiene_coordenadas())
        self.assertEqual(
            self.cliente.obtener_maps_url(),
            'https://www.google.com/maps/search/?api=1&query=Av.+Agua+123'
        )

    def test_cliente_maps_url_normaliza_direccion(self):
        self.cliente.direccion = '  Av.   Agua  123 / Lima, Peru  '

        self.assertEqual(self.cliente.maps_label, 'Dirección textual')
        self.assertEqual(self.cliente.direccion_normalizada, 'Av. Agua 123 / Lima, Peru')
        self.assertEqual(
            self.cliente.obtener_maps_url(),
            'https://www.google.com/maps/search/?api=1&query=Av.+Agua+123+%2F+Lima,+Peru'
        )

    def test_cliente_maps_label_sin_ubicacion(self):
        self.cliente.direccion = '  '

        self.assertEqual(self.cliente.maps_label, 'Sin ubicación')
        self.assertEqual(self.cliente.obtener_maps_url(), '')

    def test_cliente_valida_rango_de_coordenadas(self):
        self.cliente.latitud = Decimal('91.000000')
        self.cliente.longitud = Decimal('-77.042793')

        with self.assertRaises(ValidationError):
            self.cliente.full_clean()

    def test_cliente_valida_coordenadas_incompletas(self):
        self.cliente.latitud = Decimal('-12.046374')
        self.cliente.longitud = None

        with self.assertRaises(ValidationError):
            self.cliente.full_clean()

    def test_registrar_cliente_sin_gps_ni_foto_ok(self):
        self.client.force_login(self.secretaria)

        response = self.client.post(
            reverse('registrar_cliente'),
            {
                'nombre': 'Cliente Simple',
                'telefono': '999888776',
                'direccion': 'Jr. Simple 456',
                'referencia': 'Sin referencia real',
            }
        )

        self.assertRedirects(
            response,
            reverse('lista_clientes'),
            fetch_redirect_response=False
        )
        cliente = Cliente.objects.get(telefono='999888776')
        self.assertIsNone(cliente.latitud)
        self.assertIsNone(cliente.longitud)
        self.assertFalse(cliente.foto_referencia_url)

    def test_registrar_cliente_bloquea_gps_sin_foto(self):
        self.client.force_login(self.secretaria)

        response = self.client.post(
            reverse('registrar_cliente'),
            {
                'nombre': 'Cliente GPS',
                'telefono': '999888777',
                'direccion': 'Jr. Rio 456',
                'referencia': 'Casa azul',
                'latitud': '-12.046374',
                'longitud': '-77.042793',
                'referencia_ubicacion': 'Frente al parque',
            }
        )

        self.assertRedirects(
            response,
            reverse('registrar_cliente'),
            fetch_redirect_response=False
        )
        self.assertFalse(
            Cliente.objects.filter(telefono='999888777').exists()
        )

    def test_registrar_cliente_bloquea_foto_sin_gps(self):
        self.client.force_login(self.secretaria)

        response = self.client.post(
            reverse('registrar_cliente'),
            {
                'nombre': 'Cliente Foto Sin GPS',
                'telefono': '999888778',
                'direccion': 'Jr. Rio 789',
                'referencia': 'Casa azul',
                'foto_referencia': self.crear_foto_prueba(),
            }
        )

        self.assertRedirects(
            response,
            reverse('registrar_cliente'),
            fetch_redirect_response=False
        )
        self.assertFalse(
            Cliente.objects.filter(telefono='999888778').exists()
        )

    def test_repartidor_puede_crear_cliente_sin_gps_ni_foto(self):
        self.client.force_login(self.repartidor)

        response = self.client.post(
            reverse('nuevo_cliente_repartidor'),
            {
                'nombre': 'Cliente Rapido',
                'telefono': '999666333',
                'direccion': 'Jr. Rapido 123',
                'referencia': 'Puerta lateral',
            }
        )

        self.assertRedirects(
            response,
            reverse('pedidos_repartidor'),
            fetch_redirect_response=False
        )
        cliente = Cliente.objects.get(telefono='999666333')
        self.assertIsNone(cliente.latitud)
        self.assertIsNone(cliente.longitud)
        self.assertFalse(cliente.foto_referencia_url)

    def test_lista_pedidos_no_muestra_botones_ubicacion_directos(self):
        self.crear_pedido(self.repartidor)
        self.client.force_login(self.secretaria)

        response = self.client.get(reverse('lista_pedidos'))

        self.assertNotContains(response, 'Abrir ubicaci&oacute;n')
        self.assertNotContains(response, 'Copiar ubicaci&oacute;n')
        self.assertNotContains(response, 'query=Av.+Agua+123')

    @override_settings(
        CLOUDINARY_CLOUD_NAME='demo',
        CLOUDINARY_API_KEY='key',
        CLOUDINARY_API_SECRET='secret'
    )
    @patch('core.cloudinary_images.cloudinary.uploader.upload')
    def test_registrar_cliente_sube_foto_referencia_cloudinary(self, upload_mock):
        upload_mock.return_value = {
            'secure_url': 'https://res.cloudinary.com/demo/clientes/foto.jpg',
            'public_id': 'aquasmart/clientes/foto',
        }
        self.client.force_login(self.secretaria)

        response = self.client.post(
            reverse('registrar_cliente'),
            {
                'nombre': 'Cliente Foto',
                'telefono': '999777555',
                'direccion': 'Av. Foto 123',
                'referencia': 'Fachada blanca',
                'latitud': '-12.046374',
                'longitud': '-77.042793',
                'referencia_ubicacion': 'Frente al parque',
                'foto_referencia': self.crear_foto_prueba(),
            }
        )

        self.assertRedirects(
            response,
            reverse('lista_clientes'),
            fetch_redirect_response=False
        )
        cliente = Cliente.objects.get(telefono='999777555')
        self.assertEqual(cliente.latitud, Decimal('-12.046374'))
        self.assertEqual(cliente.longitud, Decimal('-77.042793'))
        self.assertEqual(
            cliente.foto_referencia_url,
            'https://res.cloudinary.com/demo/clientes/foto.jpg'
        )
        self.assertEqual(
            cliente.foto_referencia_public_id,
            'aquasmart/clientes/foto'
        )
        upload_mock.assert_called_once()
        self.assertEqual(
            upload_mock.call_args.kwargs['folder'],
            'aquasmart/clientes'
        )

    def test_registrar_cliente_rechaza_foto_no_permitida(self):
        self.client.force_login(self.secretaria)

        response = self.client.post(
            reverse('registrar_cliente'),
            {
                'nombre': 'Cliente Archivo',
                'telefono': '999777444',
                'direccion': 'Av. Archivo 123',
                'referencia': '',
                'latitud': '-12.046374',
                'longitud': '-77.042793',
                'referencia_ubicacion': 'Frente al parque',
                'foto_referencia': SimpleUploadedFile(
                    'archivo.txt',
                    b'no es imagen',
                    content_type='text/plain'
                ),
            }
        )

        self.assertRedirects(
            response,
            reverse('registrar_cliente'),
            fetch_redirect_response=False
        )
        self.assertFalse(
            Cliente.objects.filter(telefono='999777444').exists()
        )

    def test_repartidor_no_puede_guardar_foto_sin_gps(self):
        self.client.force_login(self.repartidor)

        response = self.client.post(
            reverse('nuevo_cliente_repartidor'),
            {
                'nombre': 'Cliente Sin GPS',
                'telefono': '999777333',
                'direccion': 'Av. Sin GPS 123',
                'referencia': '',
                'foto_referencia': self.crear_foto_prueba(),
            }
        )

        self.assertRedirects(
            response,
            reverse('nuevo_cliente_repartidor'),
            fetch_redirect_response=False
        )
        self.assertFalse(
            Cliente.objects.filter(telefono='999777333').exists()
        )

    def test_actualizar_referencia_bloquea_gps_sin_foto(self):
        self.crear_pedido(self.repartidor)
        self.client.force_login(self.repartidor)

        response = self.client.post(
            reverse(
                'actualizar_referencia_cliente_repartidor',
                kwargs={'cliente_id': self.cliente.id}
            ),
            {
                'latitud': '-12.050000',
                'longitud': '-77.030000',
                'referencia_ubicacion': 'Casa con porton negro',
            }
        )

        self.assertRedirects(
            response,
            reverse(
                'actualizar_referencia_cliente_repartidor',
                kwargs={'cliente_id': self.cliente.id}
            ),
            fetch_redirect_response=False
        )
        self.cliente.refresh_from_db()
        self.assertIsNone(self.cliente.latitud)
        self.assertIsNone(self.cliente.longitud)

    @override_settings(
        CLOUDINARY_CLOUD_NAME='demo',
        CLOUDINARY_API_KEY='key',
        CLOUDINARY_API_SECRET='secret'
    )
    @patch('core.cloudinary_images.cloudinary.uploader.upload')
    def test_repartidor_guarda_foto_y_gps_desde_referencia(self, upload_mock):
        upload_mock.return_value = {
            'secure_url': 'https://res.cloudinary.com/demo/clientes/ref.jpg',
            'public_id': 'aquasmart/clientes/ref',
        }
        self.crear_pedido(self.repartidor)
        self.client.force_login(self.repartidor)

        response = self.client.post(
            reverse(
                'actualizar_referencia_cliente_repartidor',
                kwargs={'cliente_id': self.cliente.id}
            ),
            {
                'latitud': '-12.050000',
                'longitud': '-77.030000',
                'referencia_ubicacion': 'Casa con porton negro',
                'foto_referencia': self.crear_foto_prueba(),
            }
        )

        self.assertRedirects(
            response,
            reverse('pedidos_repartidor'),
            fetch_redirect_response=False
        )
        self.cliente.refresh_from_db()
        self.assertEqual(self.cliente.latitud, Decimal('-12.050000'))
        self.assertEqual(self.cliente.longitud, Decimal('-77.030000'))
        self.assertEqual(
            self.cliente.foto_referencia_public_id,
            'aquasmart/clientes/ref'
        )
        self.assertEqual(self.cliente.foto_referencia_actualizada_por, self.repartidor)
        self.assertIsNotNone(self.cliente.foto_referencia_actualizada_en)

    @override_settings(
        CLOUDINARY_CLOUD_NAME='demo',
        CLOUDINARY_API_KEY='key',
        CLOUDINARY_API_SECRET='secret'
    )
    @patch('core.cloudinary_images.cloudinary.uploader.destroy')
    @patch('core.cloudinary_images.cloudinary.uploader.upload')
    def test_repartidor_puede_cambiar_foto_antes_de_24h(
        self,
        upload_mock,
        destroy_mock
    ):
        self.cliente.foto_referencia_url = 'https://old.example/foto.jpg'
        self.cliente.foto_referencia_public_id = 'aquasmart/clientes/old'
        self.cliente.foto_referencia_actualizada_en = (
            timezone.now() - timedelta(hours=2)
        )
        self.cliente.foto_referencia_actualizada_por = self.repartidor
        self.cliente.save()
        upload_mock.return_value = {
            'secure_url': 'https://res.cloudinary.com/demo/clientes/new.jpg',
            'public_id': 'aquasmart/clientes/new',
        }
        self.crear_pedido(self.repartidor)
        self.client.force_login(self.repartidor)

        response = self.client.post(
            reverse(
                'actualizar_referencia_cliente_repartidor',
                kwargs={'cliente_id': self.cliente.id}
            ),
            {
                'latitud': '-12.050000',
                'longitud': '-77.030000',
                'referencia_ubicacion': 'Nueva referencia',
                'foto_referencia': self.crear_foto_prueba('nueva.jpg'),
            }
        )

        self.assertRedirects(
            response,
            reverse('pedidos_repartidor'),
            fetch_redirect_response=False
        )
        self.cliente.refresh_from_db()
        self.assertEqual(
            self.cliente.foto_referencia_public_id,
            'aquasmart/clientes/new'
        )
        destroy_mock.assert_called_once()

    @override_settings(
        CLOUDINARY_CLOUD_NAME='demo',
        CLOUDINARY_API_KEY='key',
        CLOUDINARY_API_SECRET='secret'
    )
    @patch('core.cloudinary_images.cloudinary.uploader.upload')
    def test_repartidor_no_puede_cambiar_foto_despues_de_24h(self, upload_mock):
        self.cliente.foto_referencia_url = 'https://old.example/foto.jpg'
        self.cliente.foto_referencia_public_id = 'aquasmart/clientes/old'
        self.cliente.foto_referencia_actualizada_en = (
            timezone.now() - timedelta(hours=25)
        )
        self.cliente.foto_referencia_actualizada_por = self.repartidor
        self.cliente.save()
        self.crear_pedido(self.repartidor)
        self.client.force_login(self.repartidor)

        response = self.client.post(
            reverse(
                'actualizar_referencia_cliente_repartidor',
                kwargs={'cliente_id': self.cliente.id}
            ),
            {
                'latitud': '-12.050000',
                'longitud': '-77.030000',
                'referencia_ubicacion': 'Nueva referencia',
                'foto_referencia': self.crear_foto_prueba('bloqueada.jpg'),
            }
        )

        self.assertRedirects(
            response,
            reverse(
                'actualizar_referencia_cliente_repartidor',
                kwargs={'cliente_id': self.cliente.id}
            ),
            fetch_redirect_response=False
        )
        self.cliente.refresh_from_db()
        self.assertEqual(
            self.cliente.foto_referencia_public_id,
            'aquasmart/clientes/old'
        )
        upload_mock.assert_not_called()

    @override_settings(
        CLOUDINARY_CLOUD_NAME='demo',
        CLOUDINARY_API_KEY='key',
        CLOUDINARY_API_SECRET='secret'
    )
    @patch('core.cloudinary_images.cloudinary.uploader.destroy')
    @patch('core.cloudinary_images.cloudinary.uploader.upload')
    def test_editar_cliente_reemplaza_foto_anterior(
        self,
        upload_mock,
        destroy_mock
    ):
        self.cliente.foto_referencia_url = 'https://old.example/foto.jpg'
        self.cliente.foto_referencia_public_id = 'aquasmart/clientes/old'
        self.cliente.foto_referencia_actualizada_en = (
            timezone.now() - timedelta(days=5)
        )
        self.cliente.foto_referencia_actualizada_por = self.repartidor
        self.cliente.save()
        upload_mock.return_value = {
            'secure_url': 'https://res.cloudinary.com/demo/clientes/new.jpg',
            'public_id': 'aquasmart/clientes/new',
        }
        self.client.force_login(self.secretaria)

        response = self.client.post(
            reverse('editar_cliente', kwargs={'cliente_id': self.cliente.id}),
            {
                'nombre': self.cliente.nombre,
                'telefono': self.cliente.telefono,
                'direccion': self.cliente.direccion,
                'referencia': self.cliente.referencia or '',
                'foto_referencia': self.crear_foto_prueba('nueva.jpg'),
            }
        )

        self.assertRedirects(
            response,
            reverse('detalle_cliente', kwargs={'cliente_id': self.cliente.id}),
            fetch_redirect_response=False
        )
        self.cliente.refresh_from_db()
        self.assertEqual(
            self.cliente.foto_referencia_public_id,
            'aquasmart/clientes/new'
        )
        destroy_mock.assert_called_once()
        self.assertEqual(
            destroy_mock.call_args.args[0],
            'aquasmart/clientes/old'
        )

    @override_settings(
        CLOUDINARY_CLOUD_NAME='demo',
        CLOUDINARY_API_KEY='key',
        CLOUDINARY_API_SECRET='secret'
    )
    @patch('core.cloudinary_images.cloudinary.uploader.destroy')
    def test_admin_elimina_foto_sin_limite_de_tiempo(self, destroy_mock):
        self.cliente.foto_referencia_url = 'https://old.example/foto.jpg'
        self.cliente.foto_referencia_public_id = 'aquasmart/clientes/old'
        self.cliente.foto_referencia_actualizada_en = (
            timezone.now() - timedelta(days=5)
        )
        self.cliente.foto_referencia_actualizada_por = self.repartidor
        self.cliente.save()
        self.client.force_login(self.secretaria)

        response = self.client.post(
            reverse('editar_cliente', kwargs={'cliente_id': self.cliente.id}),
            {
                'nombre': self.cliente.nombre,
                'telefono': self.cliente.telefono,
                'direccion': self.cliente.direccion,
                'referencia': self.cliente.referencia or '',
                'eliminar_foto_referencia': '1',
            }
        )

        self.assertRedirects(
            response,
            reverse('detalle_cliente', kwargs={'cliente_id': self.cliente.id}),
            fetch_redirect_response=False
        )
        self.cliente.refresh_from_db()
        self.assertFalse(self.cliente.foto_referencia_url)
        self.assertFalse(self.cliente.foto_referencia_public_id)
        self.assertIsNone(self.cliente.foto_referencia_actualizada_en)
        destroy_mock.assert_called_once()

    @override_settings(
        CLOUDINARY_CLOUD_NAME='demo',
        CLOUDINARY_API_KEY='key',
        CLOUDINARY_API_SECRET='secret'
    )
    @patch('core.cloudinary_images.cloudinary.uploader.destroy')
    def test_eliminar_cliente_elimina_foto_cloudinary(self, destroy_mock):
        self.cliente.foto_referencia_url = (
            'https://res.cloudinary.com/demo/image/upload/v123/'
            'aquasmart/clientes/foto.jpg'
        )
        self.cliente.foto_referencia_public_id = 'aquasmart/clientes/foto'
        self.cliente.save()
        cliente_id = self.cliente.id
        self.client.force_login(self.secretaria)

        response = self.client.post(
            reverse('eliminar_cliente', kwargs={'cliente_id': cliente_id})
        )

        self.assertRedirects(
            response,
            reverse('lista_clientes'),
            fetch_redirect_response=False
        )
        self.assertFalse(Cliente.objects.filter(id=cliente_id).exists())
        destroy_mock.assert_called_once()
        self.assertEqual(
            destroy_mock.call_args.args[0],
            'aquasmart/clientes/foto'
        )

    @override_settings(
        CLOUDINARY_CLOUD_NAME='demo',
        CLOUDINARY_API_KEY='key',
        CLOUDINARY_API_SECRET='secret'
    )
    @patch('core.cloudinary_images.cloudinary.uploader.destroy')
    def test_eliminar_cliente_extrae_public_id_desde_url(self, destroy_mock):
        self.cliente.foto_referencia_url = (
            'https://res.cloudinary.com/demo/image/upload/v123/'
            'aquasmart/clientes/foto-url.jpg'
        )
        self.cliente.foto_referencia_public_id = ''
        self.cliente.save()
        cliente_id = self.cliente.id
        self.client.force_login(self.secretaria)

        response = self.client.post(
            reverse('eliminar_cliente', kwargs={'cliente_id': cliente_id})
        )

        self.assertRedirects(
            response,
            reverse('lista_clientes'),
            fetch_redirect_response=False
        )
        self.assertFalse(Cliente.objects.filter(id=cliente_id).exists())
        self.assertEqual(
            destroy_mock.call_args.args[0],
            'aquasmart/clientes/foto-url'
        )

    @override_settings(
        CLOUDINARY_CLOUD_NAME='demo',
        CLOUDINARY_API_KEY='key',
        CLOUDINARY_API_SECRET='secret'
    )
    @patch('core.cloudinary_images.cloudinary.uploader.destroy')
    def test_eliminar_cliente_no_falla_si_destroy_falla(self, destroy_mock):
        self.cliente.foto_referencia_public_id = 'aquasmart/clientes/foto'
        self.cliente.save()
        destroy_mock.side_effect = Exception('cloudinary temporalmente caido')
        cliente_id = self.cliente.id
        self.client.force_login(self.secretaria)

        response = self.client.post(
            reverse('eliminar_cliente', kwargs={'cliente_id': cliente_id})
        )

        self.assertRedirects(
            response,
            reverse('lista_clientes'),
            fetch_redirect_response=False
        )
        self.assertFalse(Cliente.objects.filter(id=cliente_id).exists())
        destroy_mock.assert_called_once()

    @patch('core.cloudinary_images.cloudinary.uploader.destroy')
    def test_eliminar_cliente_sin_foto_no_llama_cloudinary(self, destroy_mock):
        cliente_id = self.cliente.id
        self.client.force_login(self.secretaria)

        response = self.client.post(
            reverse('eliminar_cliente', kwargs={'cliente_id': cliente_id})
        )

        self.assertRedirects(
            response,
            reverse('lista_clientes'),
            fetch_redirect_response=False
        )
        self.assertFalse(Cliente.objects.filter(id=cliente_id).exists())
        destroy_mock.assert_not_called()

    def test_repartidor_no_entra_al_crm_operativo(self):
        self.client.force_login(self.repartidor)

        response = self.client.get(reverse('lista_pedidos'))

        self.assertEqual(response.status_code, 302)
        self.assertIn('/login/', response['Location'])

    def test_secretaria_entra_al_crm_operativo(self):
        self.client.force_login(self.secretaria)

        response = self.client.get(reverse('lista_pedidos'))

        self.assertEqual(response.status_code, 200)

    def test_lista_pedidos_ordena_por_prioridad_operativa_en_una_tabla(self):
        hoy = timezone.localdate()

        pedido_cancelado = self.crear_pedido(
            self.repartidor,
            fecha_programada=hoy
        )
        Pedido.objects.filter(pk=pedido_cancelado.pk).update(
            estado=Pedido.CANCELADO
        )
        pedido_cancelado.refresh_from_db()

        pedido_futuro = self.crear_pedido(
            self.repartidor,
            fecha_programada=hoy + timedelta(days=4)
        )
        pedido_hoy = self.crear_pedido(
            self.repartidor,
            fecha_programada=hoy
        )
        pedido_entregado = self.crear_pedido(
            self.repartidor,
            fecha_programada=hoy
        )
        Pedido.objects.filter(pk=pedido_entregado.pk).update(
            estado=Pedido.ENTREGADO
        )
        pedido_entregado.refresh_from_db()

        pedido_manana = self.crear_pedido(
            self.repartidor,
            fecha_programada=hoy + timedelta(days=1)
        )
        pedido_atrasado = self.crear_pedido(
            self.repartidor,
            fecha_programada=hoy - timedelta(days=1)
        )

        self.client.force_login(self.secretaria)
        response = self.client.get(reverse('lista_pedidos'))

        pedidos = list(response.context['pedidos'])

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [pedido.id for pedido in pedidos[:6]],
            [
                pedido_atrasado.id,
                pedido_hoy.id,
                pedido_manana.id,
                pedido_futuro.id,
                pedido_entregado.id,
                pedido_cancelado.id,
            ]
        )
        self.assertContains(response, '<table', html=False)
        self.assertContains(response, 'pedido-row-atrasado')
        self.assertContains(response, 'pedido-row-manana')
        self.assertContains(response, 'badge-manana')

    def test_vistas_operativas_principales_renderizan(self):
        self.crear_pedido(self.repartidor)

        self.client.force_login(self.daniel)
        self.assertEqual(
            self.client.get(reverse('pedidos_repartidor')).status_code,
            200
        )

        self.client.force_login(self.secretaria)
        self.assertEqual(self.client.get(reverse('dashboard')).status_code, 200)
        self.assertEqual(self.client.get(reverse('lista_pedidos')).status_code, 200)

        self.client.force_login(self.repartidor)
        self.assertEqual(
            self.client.get(reverse('pedidos_repartidor')).status_code,
            200
        )

        self.client.force_login(self.jefe_reparto)
        self.assertEqual(
            self.client.get(reverse('panel_jefe_repartidores')).status_code,
            200
        )

    def test_repartidor_puede_ver_clientes_sin_acciones_administrativas(self):
        self.client.force_login(self.repartidor)

        response = self.client.get(reverse('lista_clientes'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.cliente.nombre)

        detalle = self.client.get(
            reverse(
                'detalle_cliente',
                kwargs={'cliente_id': self.cliente.id}
            )
        )

        self.assertEqual(detalle.status_code, 200)
        self.assertContains(detalle, 'Nuevo pedido para este cliente')
        self.assertNotContains(detalle, 'Editar cliente')
        self.assertNotContains(detalle, 'Eliminar cliente')
        self.assertNotContains(detalle, 'Revertir entrega')

    def test_jefe_repartidores_puede_ver_clientes(self):
        self.client.force_login(self.jefe_reparto)

        response = self.client.get(reverse('lista_clientes'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.cliente.nombre)

    def test_nuevo_pedido_repartidor_preselecciona_cliente(self):
        self.client.force_login(self.repartidor)

        response = self.client.get(
            reverse('nuevo_pedido_repartidor'),
            {'cliente': self.cliente.id}
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.cliente.nombre)
        self.assertContains(
            response,
            f'value="{self.cliente.id}"'
        )

    def test_registrar_pedido_sin_fecha_programada_usa_fecha_local_de_hoy(self):
        self.client.force_login(self.secretaria)

        response = self.client.post(
            reverse('registrar_pedido'),
            {
                'cliente': str(self.cliente.id),
                'repartidor': '',
                'cantidad_bidones': '2',
                'precio_unitario': '7.00',
                'estado': Pedido.PENDIENTE,
                'observacion': '',
                'fecha_programada': '',
            }
        )

        self.assertRedirects(
            response,
            reverse('lista_pedidos'),
            fetch_redirect_response=False
        )
        pedido = Pedido.objects.latest('id')
        self.assertEqual(pedido.fecha_programada, timezone.localdate())

    def test_registrar_pedido_respeta_fecha_programada_futura(self):
        self.client.force_login(self.secretaria)
        fecha_futura = timezone.localdate() + timedelta(days=2)

        response = self.client.post(
            reverse('registrar_pedido'),
            {
                'cliente': str(self.cliente.id),
                'repartidor': '',
                'cantidad_bidones': '3',
                'precio_unitario': '8.00',
                'estado': Pedido.PENDIENTE,
                'observacion': '',
                'fecha_programada': fecha_futura.strftime('%Y-%m-%d'),
            }
        )

        self.assertRedirects(
            response,
            reverse('lista_pedidos'),
            fetch_redirect_response=False
        )
        pedido = Pedido.objects.latest('id')
        self.assertEqual(pedido.fecha_programada, fecha_futura)

    def test_nuevo_pedido_repartidor_sin_fecha_programada_usa_fecha_local_de_hoy(self):
        self.client.force_login(self.repartidor)

        response = self.client.post(
            reverse('nuevo_pedido_repartidor'),
            {
                'cliente': str(self.cliente.id),
                'cantidad_bidones': '2',
                'precio_unitario': '7.00',
                'observacion': '',
                'fecha_programada': '',
            }
        )

        self.assertRedirects(
            response,
            reverse('pedidos_repartidor'),
            fetch_redirect_response=False
        )
        pedido = Pedido.objects.latest('id')
        self.assertEqual(pedido.fecha_programada, timezone.localdate())
        self.assertEqual(pedido.repartidor, self.repartidor)

    def test_nuevo_pedido_repartidor_respeta_fecha_programada_futura(self):
        self.client.force_login(self.repartidor)
        fecha_futura = timezone.localdate() + timedelta(days=2)

        response = self.client.post(
            reverse('nuevo_pedido_repartidor'),
            {
                'cliente': str(self.cliente.id),
                'cantidad_bidones': '4',
                'precio_unitario': '7.00',
                'observacion': '',
                'fecha_programada': fecha_futura.strftime('%Y-%m-%d'),
            }
        )

        self.assertRedirects(
            response,
            reverse('pedidos_repartidor'),
            fetch_redirect_response=False
        )
        pedido = Pedido.objects.latest('id')
        self.assertEqual(pedido.fecha_programada, fecha_futura)
        self.assertEqual(pedido.repartidor, self.repartidor)

    def test_repartidor_solo_ve_pedidos_asignados(self):
        pedido_asignado = self.crear_pedido(self.repartidor)
        cliente_otro = Cliente.objects.create(
            nombre='Cliente Dos',
            telefono='999333444',
            direccion='Av. Lejana 456'
        )
        self.crear_pedido(self.otro_repartidor, cliente=cliente_otro)

        self.client.force_login(self.repartidor)
        response = self.client.get(reverse('pedidos_repartidor'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, pedido_asignado.cliente.nombre)
        self.assertNotContains(response, cliente_otro.nombre)

    def test_panel_repartidor_separa_pedidos_hoy_y_programados(self):
        hoy = timezone.localdate()
        pedido_atrasado = self.crear_pedido(
            self.repartidor,
            fecha_programada=hoy - timedelta(days=1)
        )
        pedido_hoy = self.crear_pedido(
            self.repartidor,
            fecha_programada=hoy
        )
        pedido_manana = self.crear_pedido(
            self.repartidor,
            fecha_programada=hoy + timedelta(days=1)
        )

        self.client.force_login(self.repartidor)
        response = self.client.get(reverse('pedidos_repartidor'))

        self.assertEqual(response.status_code, 200)
        self.assertIn(pedido_atrasado, response.context['pedidos_hoy'])
        self.assertIn(pedido_hoy, response.context['pedidos_hoy'])
        self.assertIn(pedido_manana, response.context['pedidos_programados'])
        self.assertNotIn(pedido_manana, response.context['pedidos_hoy'])
        self.assertEqual(
            list(response.context['pedidos_hoy'])[:2],
            [pedido_atrasado, pedido_hoy]
        )
        self.assertContains(response, 'Pedidos pendientes')
        self.assertContains(response, 'Ver pedidos programados (1)')

    def test_panel_jefe_separa_pedidos_prioritarios_y_programados(self):
        hoy = timezone.localdate()
        pedido_sin_asignar_hoy = self.crear_pedido(
            repartidor=None,
            fecha_programada=hoy
        )
        pedido_asignado_atrasado = self.crear_pedido(
            self.repartidor,
            fecha_programada=hoy - timedelta(days=1)
        )
        pedido_programado = self.crear_pedido(
            self.repartidor,
            fecha_programada=hoy + timedelta(days=1)
        )

        self.client.force_login(self.jefe_reparto)
        response = self.client.get(reverse('panel_jefe_repartidores'))

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            pedido_sin_asignar_hoy,
            response.context['pedidos_sin_asignar']
        )
        self.assertIn(
            pedido_asignado_atrasado,
            response.context['pedidos_asignados']
        )
        self.assertIn(
            pedido_programado,
            response.context['pedidos_programados']
        )
        self.assertNotIn(
            pedido_programado,
            response.context['pedidos_asignados']
        )
        self.assertContains(response, 'Pedidos programados futuros')

    def test_asignar_pedido_cambia_estado_y_registra_timestamp(self):
        pedido = self.crear_pedido(None)
        self.client.force_login(self.jefe_reparto)

        response = self.client.post(
            reverse(
                'asignar_pedido_repartidor',
                kwargs={'pedido_id': pedido.id}
            ),
            {'repartidor': str(self.repartidor.id)}
        )

        self.assertRedirects(
            response,
            reverse('panel_jefe_repartidores'),
            fetch_redirect_response=False
        )
        pedido.refresh_from_db()
        self.assertEqual(pedido.estado, Pedido.ASIGNADO)
        self.assertEqual(pedido.repartidor, self.repartidor)
        self.assertIsNotNone(pedido.fecha_asignado)
        self.assertEqual(pedido.usuario_asignado, self.jefe_reparto)
        self.assertEqual(pedido.usuario_estado_actualizado, self.jefe_reparto)

    def test_repartidor_marca_pedido_en_ruta(self):
        pedido = self.crear_pedido(self.repartidor)
        Pedido.objects.filter(pk=pedido.pk).update(estado=Pedido.ASIGNADO)
        self.client.force_login(self.repartidor)

        response = self.client.post(
            reverse(
                'marcar_pedido_en_ruta_repartidor',
                kwargs={'pedido_id': pedido.id}
            )
        )

        self.assertRedirects(
            response,
            reverse('pedidos_repartidor'),
            fetch_redirect_response=False
        )
        pedido.refresh_from_db()
        self.assertEqual(pedido.estado, Pedido.EN_RUTA)
        self.assertIsNotNone(pedido.fecha_en_ruta)
        self.assertEqual(pedido.usuario_en_ruta, self.repartidor)
        self.assertTrue(
            PedidoHistorial.objects.filter(
                pedido=pedido,
                tipo_accion=PedidoHistorial.EN_RUTA,
                valor_nuevo=Pedido.EN_RUTA
            ).exists()
        )

    def test_repartidor_ve_boton_foto_referencia_cliente(self):
        self.cliente.foto_referencia_url = 'https://res.cloudinary.com/demo/foto.jpg'
        self.cliente.foto_referencia_public_id = 'aquasmart/clientes/foto'
        self.cliente.save()
        pedido = self.crear_pedido(self.repartidor)
        Pedido.objects.filter(pk=pedido.pk).update(estado=Pedido.ASIGNADO)
        self.client.force_login(self.repartidor)

        response = self.client.get(reverse('pedidos_repartidor'))

        self.assertContains(response, 'Ver foto')
        self.assertContains(
            response,
            'https://res.cloudinary.com/demo/foto.jpg'
        )

    def test_jefe_repartidores_accede_a_mi_reparto_y_filtra_sus_pedidos(self):
        pedido_asignado = self.crear_pedido(self.jefe_reparto)
        cliente_otro = Cliente.objects.create(
            nombre='Cliente Tres',
            telefono='999555666',
            direccion='Av. Otra 789'
        )
        self.crear_pedido(self.repartidor, cliente=cliente_otro)

        self.client.force_login(self.jefe_reparto)
        response = self.client.get(reverse('pedidos_repartidor'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, pedido_asignado.cliente.nombre)
        self.assertNotContains(response, cliente_otro.nombre)

    def test_jefe_repartidores_accede_al_panel_jefe(self):
        self.client.force_login(self.jefe_reparto)

        response = self.client.get(reverse('panel_jefe_repartidores'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Mi reparto')
        self.assertContains(response, 'Resumen de repartidores hoy')

    def test_login_redirige_cada_rol_a_su_panel(self):
        casos = [
            ('DANIEL', 'clave-daniel', reverse('dashboard')),
            ('ADMINISTRADOR', 'clave-secreta', reverse('dashboard')),
            ('HERAL', 'clave-jefe', reverse('panel_jefe_repartidores')),
            ('ECOAGUA', 'clave-repartidor', reverse('pedidos_repartidor')),
        ]

        for username, password, destino in casos:
            self.client.logout()

            response = self.client.post(
                reverse('login'),
                {
                    'username': username,
                    'password': password,
                }
            )

            self.assertRedirects(
                response,
                destino,
                fetch_redirect_response=False
            )

    def test_login_de_jefe_repartidores_redirige_a_panel_jefe(self):
        response = self.client.post(
            reverse('login'),
            {
                'username': 'HERAL',
                'password': 'clave-jefe',
            }
        )

        self.assertRedirects(
            response,
            reverse('panel_jefe_repartidores'),
            fetch_redirect_response=False
        )

    def test_login_de_repartidor_normal_redirige_a_mi_reparto(self):
        response = self.client.post(
            reverse('login'),
            {
                'username': 'ECOAGUA',
                'password': 'clave-repartidor',
            }
        )

        self.assertRedirects(
            response,
            reverse('pedidos_repartidor'),
            fetch_redirect_response=False
        )

    def test_grupo_legacy_repartidor_sigue_entrando_a_mi_reparto(self):
        User = get_user_model()
        repartidor_legacy = User.objects.create_user(
            username='LEGACY',
            password='clave-legacy'
        )
        repartidor_legacy.groups.add(self.grupo_repartidor_legacy)

        response = self.client.post(
            reverse('login'),
            {
                'username': 'LEGACY',
                'password': 'clave-legacy',
            }
        )

        self.assertRedirects(
            response,
            reverse('pedidos_repartidor'),
            fetch_redirect_response=False
        )

    def test_repartidores_disponibles_incluye_repartidores_y_jefes_no_superuser_suelto(self):
        usuarios = list(
            repartidores_disponibles().values_list('username', flat=True)
        )

        self.assertIn('ECOAGUA', usuarios)
        self.assertIn('HERAL', usuarios)
        self.assertNotIn('ADMINISTRADOR', usuarios)
        self.assertNotIn('DANIEL', usuarios)

    def test_panel_jefe_permita_asignar_a_jefe_repartidor(self):
        pedido = self.crear_pedido(repartidor=None)
        self.client.force_login(self.jefe_reparto)

        response = self.client.post(
            reverse(
                'asignar_pedido_repartidor',
                kwargs={'pedido_id': pedido.id}
            ),
            {'repartidor': str(self.jefe_reparto.id)}
        )

        pedido.refresh_from_db()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(pedido.repartidor, self.jefe_reparto)

    def test_secretaria_puede_editar_pedido_pendiente(self):
        pedido = self.crear_pedido(self.repartidor)
        self.client.force_login(self.secretaria)

        response = self.client.post(
            reverse(
                'editar_pedido',
                kwargs={'pedido_id': pedido.id}
            ),
            {
                'origen': 'cliente',
                'cantidad_bidones': '4',
                'precio_unitario': '7.00',
                'fecha_programada': timezone.localdate().strftime('%Y-%m-%d'),
                'observacion': 'Cliente corrigió cantidad al recibir.',
                'repartidor': str(self.repartidor.id),
            }
        )

        pedido.refresh_from_db()
        self.assertRedirects(
            response,
            reverse(
                'detalle_cliente',
                kwargs={'cliente_id': self.cliente.id}
            ),
            fetch_redirect_response=False
        )
        self.assertEqual(pedido.cantidad_bidones, 4)
        self.assertEqual(pedido.total, Decimal('28.00'))
        self.assertEqual(
            pedido.observacion,
            'Cliente corrigió cantidad al recibir.'
        )

    def test_editar_pedido_reprograma_si_cambia_fecha(self):
        pedido = self.crear_pedido(self.repartidor)
        nueva_fecha = timezone.localdate() + timedelta(days=1)
        self.client.force_login(self.secretaria)

        response = self.client.post(
            reverse(
                'editar_pedido',
                kwargs={'pedido_id': pedido.id}
            ),
            {
                'origen': 'cliente',
                'cantidad_bidones': '2',
                'precio_unitario': '7.00',
                'fecha_programada': nueva_fecha.strftime('%Y-%m-%d'),
                'observacion': 'Cliente pidio entrega manana.',
                'repartidor': str(self.repartidor.id),
            }
        )

        pedido.refresh_from_db()
        self.assertRedirects(
            response,
            reverse(
                'detalle_cliente',
                kwargs={'cliente_id': self.cliente.id}
            ),
            fetch_redirect_response=False
        )
        self.assertEqual(pedido.fecha_programada, nueva_fecha)
        self.assertEqual(pedido.estado, Pedido.REPROGRAMADO)
        self.assertIsNotNone(pedido.fecha_reprogramado)
        self.assertEqual(pedido.usuario_reprogramado, self.secretaria)
        self.assertTrue(
            PedidoHistorial.objects.filter(
                pedido=pedido,
                tipo_accion=PedidoHistorial.REPROGRAMADO,
                valor_nuevo=Pedido.REPROGRAMADO
            ).exists()
        )

    def test_cambiar_estado_no_permita_asignado_sin_repartidor(self):
        pedido = self.crear_pedido(None)
        self.client.force_login(self.secretaria)

        response = self.client.post(
            reverse(
                'cambiar_estado_pedido',
                kwargs={
                    'pedido_id': pedido.id,
                    'nuevo_estado': Pedido.ASIGNADO,
                }
            )
        )

        pedido.refresh_from_db()
        self.assertRedirects(
            response,
            reverse('lista_pedidos'),
            fetch_redirect_response=False
        )
        self.assertEqual(pedido.estado, Pedido.PENDIENTE)
        self.assertIsNone(pedido.fecha_asignado)
        self.assertFalse(
            PedidoHistorial.objects.filter(
                pedido=pedido,
                tipo_accion=PedidoHistorial.ASIGNADO
            ).exists()
        )

    def test_cambiar_estado_no_permita_en_ruta_sin_repartidor(self):
        pedido = self.crear_pedido(None)
        self.client.force_login(self.secretaria)

        response = self.client.post(
            reverse(
                'cambiar_estado_pedido',
                kwargs={
                    'pedido_id': pedido.id,
                    'nuevo_estado': Pedido.EN_RUTA,
                }
            )
        )

        pedido.refresh_from_db()
        self.assertRedirects(
            response,
            reverse('lista_pedidos'),
            fetch_redirect_response=False
        )
        self.assertEqual(pedido.estado, Pedido.PENDIENTE)
        self.assertIsNone(pedido.fecha_en_ruta)
        self.assertFalse(
            PedidoHistorial.objects.filter(
                pedido=pedido,
                tipo_accion=PedidoHistorial.EN_RUTA
            ).exists()
        )

    def test_historial_registra_creacion_y_entrega_de_pedido(self):
        self.client.force_login(self.secretaria)

        response = self.client.post(
            reverse('registrar_pedido'),
            {
                'cliente': str(self.cliente.id),
                'repartidor': str(self.repartidor.id),
                'cantidad_bidones': '2',
                'precio_unitario': '7.00',
                'estado': Pedido.PENDIENTE,
                'observacion': '',
                'fecha_programada': '',
            }
        )

        self.assertRedirects(
            response,
            reverse('lista_pedidos'),
            fetch_redirect_response=False
        )
        pedido = Pedido.objects.latest('id')
        self.assertTrue(
            PedidoHistorial.objects.filter(
                pedido=pedido,
                usuario=self.secretaria,
                tipo_accion=PedidoHistorial.CREADO
            ).exists()
        )

        self.client.post(
            reverse(
                'cambiar_estado_pedido',
                kwargs={
                    'pedido_id': pedido.id,
                    'nuevo_estado': Pedido.ENTREGADO,
                }
            )
        )

        self.assertTrue(
            PedidoHistorial.objects.filter(
                pedido=pedido,
                usuario=self.secretaria,
                tipo_accion=PedidoHistorial.ENTREGADO,
                valor_anterior=Pedido.ASIGNADO,
                valor_nuevo=Pedido.ENTREGADO
            ).exists()
        )

    def test_historial_de_pedidos_solo_visible_para_roles_autorizados(self):
        pedido = self.crear_pedido(self.repartidor)
        PedidoHistorial.objects.create(
            pedido=pedido,
            usuario=self.secretaria,
            tipo_accion=PedidoHistorial.EDITADO,
            descripcion='Cambio administrativo sensible.',
            valor_anterior='Antes',
            valor_nuevo='Ahora'
        )

        self.client.force_login(self.secretaria)
        response = self.client.get(
            reverse(
                'detalle_cliente',
                kwargs={'cliente_id': self.cliente.id}
            )
        )

        self.assertContains(response, 'Historial de cambios de pedidos')
        self.assertContains(response, 'Cambio administrativo sensible.')

        self.client.force_login(self.repartidor)
        response = self.client.get(
            reverse(
                'detalle_cliente',
                kwargs={'cliente_id': self.cliente.id}
            )
        )

        self.assertNotContains(response, 'Historial de cambios de pedidos')
        self.assertNotContains(response, 'Cambio administrativo sensible.')

    def test_jefe_repartidores_puede_editar_y_asignar_pedido(self):
        pedido = self.crear_pedido(repartidor=None)
        self.client.force_login(self.jefe_reparto)

        response = self.client.post(
            reverse(
                'editar_pedido',
                kwargs={'pedido_id': pedido.id}
            ),
            {
                'origen': 'panel_jefe',
                'cantidad_bidones': '5',
                'precio_unitario': '8.00',
                'fecha_programada': timezone.localdate().strftime('%Y-%m-%d'),
                'observacion': 'Asignado desde edición.',
                'repartidor': str(self.otro_repartidor.id),
            }
        )

        pedido.refresh_from_db()
        self.assertRedirects(
            response,
            reverse('panel_jefe_repartidores'),
            fetch_redirect_response=False
        )
        self.assertEqual(pedido.cantidad_bidones, 5)
        self.assertEqual(pedido.total, Decimal('40.00'))
        self.assertEqual(pedido.repartidor, self.otro_repartidor)

    def test_repartidor_no_puede_editar_pedido_directamente(self):
        pedido = self.crear_pedido(self.repartidor)
        self.client.force_login(self.repartidor)

        response = self.client.post(
            reverse(
                'editar_pedido',
                kwargs={'pedido_id': pedido.id}
            ),
            {
                'cantidad_bidones': '4',
                'precio_unitario': '7.00',
                'fecha_programada': timezone.localdate().strftime('%Y-%m-%d'),
                'observacion': 'Intento no permitido.',
                'repartidor': str(self.repartidor.id),
            }
        )

        pedido.refresh_from_db()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(pedido.cantidad_bidones, 2)
        self.assertEqual(pedido.total, Decimal('14.00'))


class ReporteMensualTests(TestCase):
    def setUp(self):
        User = get_user_model()
        grupo_secretaria, _ = Group.objects.get_or_create(name='Secretaria')
        self.secretaria = User.objects.create_user(
            username='secretaria',
            password='clave-secreta'
        )
        self.secretaria.groups.add(grupo_secretaria)

    def test_reporte_mensual_tolera_filtros_invalidos(self):
        self.client.force_login(self.secretaria)

        response = self.client.get(
            reverse('reporte_mensual'),
            {'mes': '13', 'anio': '1800'}
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Gráfico diario de ventas')
