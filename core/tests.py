import os
import subprocess
import sys
from datetime import datetime, time, timedelta
from decimal import Decimal
from io import BytesIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from .models import CierreCajaDiario, Cliente, Egreso, Lugar, Pedido, PedidoHistorial
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
        self.lugar_operativo = Lugar.objects.create(
            nombre='La Merced',
            orden=1
        )

    def crear_pedido(
        self,
        repartidor,
        cliente=None,
        fecha_programada=None,
        con_lugar=True
    ):
        return Pedido.objects.create(
            cliente=cliente or self.cliente,
            repartidor=repartidor,
            lugar=self.lugar_operativo if con_lugar else None,
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

        self.assertEqual(self.cliente.maps_label, 'Direcci\u00f3n textual')
        self.assertEqual(self.cliente.direccion_normalizada, 'Av. Agua 123 / Lima, Peru')
        self.assertEqual(
            self.cliente.obtener_maps_url(),
            'https://www.google.com/maps/search/?api=1&query=Av.+Agua+123+%2F+Lima,+Peru'
        )

    def test_cliente_maps_label_sin_ubicacion(self):
        self.cliente.direccion = '  '

        self.assertEqual(self.cliente.maps_label, 'Sin ubicaci\u00f3n')
        self.assertEqual(self.cliente.obtener_maps_url(), '')

    def test_detalle_cliente_muestra_solo_referencia_para_llegar(self):
        self.cliente.referencia = 'Referencia antigua'
        self.cliente.referencia_ubicacion = 'Porton negro'
        self.cliente.save(update_fields=['referencia', 'referencia_ubicacion'])
        self.client.force_login(self.secretaria)

        response = self.client.get(
            reverse('detalle_cliente', kwargs={'cliente_id': self.cliente.id})
        )

        self.assertContains(response, 'Referencia para llegar')
        self.assertContains(response, 'Porton negro')
        self.assertNotContains(response, '<strong>Referencia:</strong>', html=False)
        self.assertNotContains(response, 'Referencia antigua')

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
                'lugar': str(self.lugar_operativo.id),
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
        self.assertEqual(cliente.lugar, self.lugar_operativo)
        self.assertEqual(cliente.referencia, '')

    def test_registrar_cliente_exige_lugar_valido(self):
        self.client.force_login(self.secretaria)

        response = self.client.post(
            reverse('registrar_cliente'),
            {
                'nombre': 'Cliente Sin Zona',
                'telefono': '999888775',
                'direccion': 'Jr. Sin Zona 456',
            },
            follow=True
        )

        self.assertFalse(
            Cliente.objects.filter(telefono='999888775').exists()
        )
        self.assertContains(
            response,
            'Selecciona una zona v\u00e1lida para el cliente.'
        )

    def test_registrar_cliente_duplica_solo_telefono_exacto_normalizado(self):
        Cliente.objects.create(
            nombre='Cliente Existente',
            telefono='999 111-333',
            direccion='Av. Misma 1',
            lugar=self.lugar_operativo
        )
        self.client.force_login(self.secretaria)

        duplicado = self.client.post(
            reverse('registrar_cliente'),
            {
                'nombre': 'Cliente Nuevo',
                'telefono': '999111333',
                'direccion': 'Av. Nueva 2',
                'lugar': str(self.lugar_operativo.id),
            },
            follow=True
        )

        self.assertContains(duplicado, 'Ya existe un cliente con ese tel\u00e9fono.')
        self.assertFalse(
            Cliente.objects.filter(nombre='Cliente Nuevo').exists()
        )

        permitido = self.client.post(
            reverse('registrar_cliente'),
            {
                'nombre': 'Cliente Existente',
                'telefono': '9991113339',
                'direccion': 'Av. Misma 1',
                'lugar': str(self.lugar_operativo.id),
            }
        )

        self.assertRedirects(
            permitido,
            reverse('lista_clientes'),
            fetch_redirect_response=False
        )
        cliente = Cliente.objects.get(telefono='9991113339')
        self.assertEqual(cliente.nombre, 'Cliente Existente')

    def test_registrar_cliente_muestra_una_sola_referencia_visible(self):
        self.client.force_login(self.secretaria)

        response = self.client.get(reverse('registrar_cliente'))

        self.assertContains(response, 'Referencia para llegar')
        self.assertContains(response, 'name="referencia_ubicacion"')
        self.assertNotContains(response, 'Referencia de entrega')
        self.assertNotContains(response, 'name="referencia"')

    def test_registrar_cliente_bloquea_gps_sin_foto(self):
        self.client.force_login(self.secretaria)

        response = self.client.post(
            reverse('registrar_cliente'),
            {
                'nombre': 'Cliente GPS',
                'telefono': '999888777',
                'direccion': 'Jr. Rio 456',
                'referencia': 'Casa azul',
                'lugar': str(self.lugar_operativo.id),
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
                'lugar': str(self.lugar_operativo.id),
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

    def test_registrar_cliente_bloquea_gps_con_precision_baja(self):
        self.client.force_login(self.secretaria)

        response = self.client.post(
            reverse('registrar_cliente'),
            {
                'nombre': 'Cliente GPS Impreciso',
                'telefono': '999888779',
                'direccion': 'Jr. Rio 790',
                'lugar': str(self.lugar_operativo.id),
                'latitud': '-12.046374',
                'longitud': '-77.042793',
                'gps_accuracy': '145.5',
                'referencia_ubicacion': 'Frente al parque',
                'foto_referencia': self.crear_foto_prueba(),
            }
        )

        self.assertRedirects(
            response,
            reverse('registrar_cliente'),
            fetch_redirect_response=False
        )
        self.assertFalse(
            Cliente.objects.filter(telefono='999888779').exists()
        )

    def test_repartidor_puede_crear_cliente_sin_gps_ni_foto(self):
        self.client.force_login(self.repartidor)

        response = self.client.post(
            reverse('nuevo_cliente_repartidor'),
            {
                'nombre': 'Cliente Rapido',
                'telefono': '999666333',
                'direccion': 'Jr. Rapido 123',
                'lugar': str(self.lugar_operativo.id),
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
        self.assertEqual(cliente.lugar, self.lugar_operativo)
        self.assertEqual(cliente.referencia, '')

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
                'lugar': str(self.lugar_operativo.id),
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
        self.assertEqual(cliente.lugar, self.lugar_operativo)
        self.assertEqual(cliente.referencia, '')
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
                'lugar': str(self.lugar_operativo.id),
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

    def test_editar_cliente_conserva_referencia_historica_oculta(self):
        self.cliente.referencia = 'Casa verde al fondo'
        self.cliente.save(update_fields=['referencia'])
        self.client.force_login(self.secretaria)

        response = self.client.post(
            reverse('editar_cliente', kwargs={'cliente_id': self.cliente.id}),
            {
                'nombre': self.cliente.nombre,
                'telefono': self.cliente.telefono,
                'direccion': self.cliente.direccion,
            }
        )

        self.assertRedirects(
            response,
            reverse('detalle_cliente', kwargs={'cliente_id': self.cliente.id}),
            fetch_redirect_response=False
        )
        self.cliente.refresh_from_db()
        self.assertEqual(self.cliente.referencia, 'Casa verde al fondo')

    def test_editar_cliente_duplica_solo_telefono_exacto_normalizado(self):
        otro_cliente = Cliente.objects.create(
            nombre='Cliente Dos',
            telefono='999 222-333',
            direccion='Av. Dos 2',
            lugar=self.lugar_operativo
        )
        self.client.force_login(self.secretaria)

        response = self.client.post(
            reverse('editar_cliente', kwargs={'cliente_id': self.cliente.id}),
            {
                'nombre': self.cliente.nombre,
                'telefono': '999222333',
                'direccion': self.cliente.direccion,
            },
            follow=True
        )

        self.assertContains(response, 'Ya existe otro cliente con ese tel\u00e9fono.')
        self.cliente.refresh_from_db()
        self.assertNotEqual(self.cliente.telefono, '999222333')

        permitido = self.client.post(
            reverse('editar_cliente', kwargs={'cliente_id': self.cliente.id}),
            {
                'nombre': otro_cliente.nombre,
                'telefono': '9992223339',
                'direccion': otro_cliente.direccion,
            }
        )

        self.assertRedirects(
            permitido,
            reverse('detalle_cliente', kwargs={'cliente_id': self.cliente.id}),
            fetch_redirect_response=False
        )
        self.cliente.refresh_from_db()
        self.assertEqual(self.cliente.telefono, '9992223339')

    @override_settings(
        CLOUDINARY_CLOUD_NAME='demo',
        CLOUDINARY_API_KEY='key',
        CLOUDINARY_API_SECRET='secret'
    )
    @patch('core.cloudinary_images.cloudinary.uploader.destroy')
    @patch('core.cloudinary_images.cloudinary.uploader.upload')
    def test_editar_cliente_reemplaza_foto_legacy_usando_public_id_de_url(
        self,
        upload_mock,
        destroy_mock
    ):
        self.cliente.foto_referencia_url = (
            'https://res.cloudinary.com/demo/image/upload/v123/'
            'aquasmart/clientes/legacy.jpg'
        )
        self.cliente.foto_referencia_public_id = ''
        self.cliente.save()
        upload_mock.return_value = {
            'secure_url': 'https://res.cloudinary.com/demo/clientes/new.jpg',
            'public_id': 'aquasmart/clientes/new',
        }
        self.client.force_login(self.secretaria)

        self.client.post(
            reverse('editar_cliente', kwargs={'cliente_id': self.cliente.id}),
            {
                'nombre': self.cliente.nombre,
                'telefono': self.cliente.telefono,
                'direccion': self.cliente.direccion,
                'referencia': '',
                'foto_referencia': self.crear_foto_prueba('nueva.jpg'),
            }
        )

        self.assertEqual(
            destroy_mock.call_args.args[0],
            'aquasmart/clientes/legacy'
        )

    @override_settings(
        CLOUDINARY_CLOUD_NAME='demo',
        CLOUDINARY_API_KEY='key',
        CLOUDINARY_API_SECRET='secret'
    )
    @patch('core.cloudinary_images.cloudinary.uploader.destroy')
    @patch('core.cloudinary_images.cloudinary.uploader.upload')
    def test_registrar_cliente_limpia_foto_nueva_si_falla_guardado(
        self,
        upload_mock,
        destroy_mock
    ):
        upload_mock.return_value = {
            'secure_url': 'https://res.cloudinary.com/demo/clientes/new.jpg',
            'public_id': 'aquasmart/clientes/new',
        }
        self.client.force_login(self.secretaria)

        with patch('core.models.Cliente.save', side_effect=RuntimeError('db error')):
            with self.assertRaises(RuntimeError):
                self.client.post(
                    reverse('registrar_cliente'),
                    {
                        'nombre': 'Cliente Foto Fallida',
                        'telefono': '999444555',
                        'direccion': 'Av. Error 1',
                        'lugar': str(self.lugar_operativo.id),
                        'latitud': '-12.050000',
                        'longitud': '-77.030000',
                        'foto_referencia': self.crear_foto_prueba(),
                    }
                )

        destroy_mock.assert_called_once()
        self.assertEqual(destroy_mock.call_args.args[0], 'aquasmart/clientes/new')

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
        self.assertContains(response, 'href="/pedidos/?filtro=atrasados"')
        self.assertContains(response, 'href="/pedidos/?filtro=hoy"')
        self.assertNotContains(response, 'filtro=entregados')
        self.assertNotContains(response, 'filtro=cancelados')

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

    def test_fragmentos_administrativos_requieren_login(self):
        for fragment_name in (
            'dashboard_fragmento',
            'clientes_fragmento',
            'pedidos_fragmento',
            'reporte_diario_fragmento',
            'panel_jefe_repartidores_fragmento',
        ):
            response = self.client.get(reverse(fragment_name))

            self.assertEqual(response.status_code, 302)
            self.assertIn('/login/', response['Location'])

    def test_dashboard_refresca_solo_fragmento_visual_completo(self):
        self.crear_pedido(self.repartidor)
        self.client.force_login(self.secretaria)

        page = self.client.get(reverse('dashboard'))
        fragment = self.client.get(reverse('dashboard_fragmento'))

        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'id="dashboard-live-container"')
        self.assertContains(page, 'data-refresh-ms="10000"')
        self.assertContains(page, 'js/partial_refresh.js')
        self.assertEqual(fragment.status_code, 200)
        self.assertTemplateUsed(
            fragment,
            'core/includes/dashboard_admin_fragmento.html'
        )
        self.assertContains(fragment, 'Vendido hoy')
        self.assertContains(fragment, 'Pendientes por atender')
        self.assertContains(fragment, 'Bidones vendidos hoy')
        self.assertContains(fragment, 'Pedidos registrados hoy')
        self.assertContains(fragment, 'Cobrado hoy')
        self.assertContains(fragment, 'Por cobrar')
        self.assertContains(fragment, 'Alerta operativa')
        self.assertContains(fragment, 'Pedidos urgentes')
        self.assertNotContains(fragment, 'Resumen general')
        self.assertNotContains(fragment, 'Estado de pedidos')
        self.assertNotContains(fragment, 'Estado de clientes')
        self.assertNotContains(fragment, 'Ranking de clientes frecuentes')
        self.assertNotContains(fragment, 'Lectura inteligente simple')
        self.assertNotContains(fragment, 'Sin historial')
        self.assertNotContains(fragment, 'eco_agua_banner_dashboard')
        self.assertNotContains(fragment, 'Buscar cliente')

    def test_reporte_diario_refresca_fragmento_operativo(self):
        pedido = self.crear_pedido(self.repartidor)
        Pedido.objects.filter(pk=pedido.pk).update(
            estado=Pedido.ENTREGADO,
            fecha_entrega=timezone.now(),
            metodo_pago=Pedido.PAGO_EFECTIVO
        )
        self.client.force_login(self.secretaria)

        page = self.client.get(reverse('reporte_diario'))
        fragment = self.client.get(reverse('reporte_diario_fragmento'))

        self.assertContains(page, 'id="reporte-diario-live"')
        self.assertContains(page, 'data-refresh-ms="10000"')
        self.assertEqual(fragment.status_code, 200)
        self.assertTemplateUsed(fragment, 'core/reporte_diario.html')
        self.assertContains(fragment, 'id="reporte-diario-live"')
        self.assertContains(fragment, 'Cobrado hoy')
        self.assertContains(fragment, 'Cr&eacute;dito otorgado hoy')
        self.assertContains(fragment, 'Cierre de caja del dia')
        self.assertContains(fragment, 'Cierre operativo del dia')
        self.assertContains(fragment, pedido.cliente.nombre)
        self.assertNotContains(fragment, '<html')

    def test_reporte_diario_tiene_impresion_limpia(self):
        self.client.force_login(self.secretaria)

        response = self.client.get(reverse('reporte_diario'))

        self.assertContains(response, 'onclick="window.print()"')
        self.assertContains(response, 'Imprimir')
        self.assertContains(response, 'Registrar egreso')
        self.assertContains(response, 'Guardar cierre')
        self.assertContains(response, 'class="btn btn-outline-primary btn-sm no-print"')
        self.assertContains(response, 'row g-2 align-items-end mb-3 no-print')
        self.assertContains(response, 'Cierre de caja del dia')
        self.assertContains(response, 'Cierre operativo del dia')

    def test_clientes_refresca_fragmento_con_clientes_actuales(self):
        self.client.force_login(self.secretaria)

        page = self.client.get(reverse('lista_clientes'))
        fragment = self.client.get(reverse('clientes_fragmento'))

        self.assertContains(page, 'id="clientes-live-container"')
        self.assertContains(page, 'data-refresh-ms="10000"')
        self.assertEqual(fragment.status_code, 200)
        self.assertTemplateUsed(
            fragment,
            'core/includes/clientes_lista_fragmento.html'
        )
        self.assertContains(fragment, self.cliente.nombre)
        self.assertNotContains(fragment, '<html')

    def test_pedidos_refresca_fragmento_y_mantiene_permiso_secretaria(self):
        pedido = self.crear_pedido(self.repartidor)
        pedido_credito = self.crear_pedido(self.repartidor)
        Pedido.objects.filter(pk=pedido_credito.pk).update(
            metodo_pago=Pedido.PAGO_FIADO
        )
        self.client.force_login(self.secretaria)

        page = self.client.get(reverse('lista_pedidos'))
        fragment = self.client.get(reverse('pedidos_fragmento'))

        self.assertContains(page, 'id="pedidos-live-container"')
        self.assertContains(page, 'data-refresh-ms="5000"')
        self.assertEqual(fragment.status_code, 200)
        self.assertTemplateUsed(
            fragment,
            'core/includes/pedidos_lista_fragmento.html'
        )
        self.assertContains(fragment, pedido.cliente.nombre)
        self.assertContains(fragment, 'Gestionar')
        self.assertContains(fragment, 'gestion-pedido-')
        self.assertContains(fragment, 'Gestionar pedido')
        self.assertContains(fragment, 'Entregar')
        self.assertContains(fragment, 'Cancelar')
        self.assertContains(fragment, 'Cr&eacute;dito pendiente')
        self.assertContains(fragment, 'Cr&eacute;dito')
        self.assertNotContains(fragment, 'Fiado')
        self.assertNotContains(fragment, 'Acciones &#9662;')
        self.assertNotContains(fragment, '<html')

        self.client.force_login(self.repartidor)
        denied_fragment = self.client.get(reverse('pedidos_fragmento'))
        self.assertEqual(denied_fragment.status_code, 302)

    def test_panel_repartidor_refresca_fragmento_sin_exponer_otro_repartidor(self):
        pedido_asignado = self.crear_pedido(self.repartidor)
        pedido_credito = self.crear_pedido(self.repartidor)
        Pedido.objects.filter(pk=pedido_credito.pk).update(
            metodo_pago=Pedido.PAGO_FIADO
        )
        cliente_otro = Cliente.objects.create(
            nombre='Cliente Fragmento Ajeno',
            telefono='999555444',
            direccion='Av. No Mostrar 123'
        )
        self.crear_pedido(self.otro_repartidor, cliente=cliente_otro)
        self.client.force_login(self.repartidor)

        page = self.client.get(reverse('pedidos_repartidor'))
        fragment = self.client.get(reverse('pedidos_repartidor_fragmento'))

        self.assertContains(page, 'id="pedidos-container"')
        self.assertContains(page, 'data-refresh-ms="5000"')
        self.assertContains(page, 'js/partial_refresh.js')
        self.assertContains(page, 'data-refresh-key="acciones-')
        self.assertEqual(fragment.status_code, 200)
        self.assertTemplateUsed(
            fragment,
            'core/includes/pedidos_repartidor_fragmento.html'
        )
        self.assertContains(fragment, pedido_asignado.cliente.nombre)
        self.assertContains(fragment, 'Cr&eacute;dito')
        self.assertNotContains(fragment, 'Fiado')
        self.assertNotContains(fragment, cliente_otro.nombre)
        self.assertNotContains(fragment, '<html')

    def test_panel_jefe_refresca_fragmento_operativo(self):
        pedido = self.crear_pedido(None)
        self.client.force_login(self.jefe_reparto)

        page = self.client.get(reverse('panel_jefe_repartidores'))
        fragment = self.client.get(reverse('panel_jefe_repartidores_fragmento'))

        self.assertContains(page, 'id="panel-jefe-repartidores-live"')
        self.assertContains(page, 'data-refresh-ms="5000"')
        self.assertContains(page, 'js/partial_refresh.js')
        self.assertEqual(fragment.status_code, 200)
        self.assertTemplateUsed(fragment, 'core/panel_jefe_repartidores.html')
        self.assertContains(fragment, 'id="panel-jefe-repartidores-live"')
        self.assertContains(fragment, 'Resumen de repartidores hoy')
        self.assertContains(fragment, 'Pedidos sin asignar de hoy / atrasados')
        self.assertContains(fragment, pedido.cliente.nombre)
        self.assertContains(fragment, 'data-refresh-pause="true"')
        self.assertNotContains(fragment, '<html')

    def test_panel_repartidor_muestra_solo_creditos_pendientes_propios(self):
        cliente_propio = Cliente.objects.create(
            nombre='Cliente Credito Propio',
            telefono='999333111',
            direccion='Av. Cobro 123'
        )
        credito_propio = self.crear_pedido(
            self.repartidor,
            cliente=cliente_propio
        )
        Pedido.objects.filter(pk=credito_propio.pk).update(
            estado=Pedido.ENTREGADO,
            fecha_entrega=timezone.now(),
            metodo_pago=Pedido.PAGO_FIADO
        )
        cliente_ajeno = Cliente.objects.create(
            nombre='Cliente Credito Ajeno',
            telefono='999333222',
            direccion='Av. Ajena 456'
        )
        credito_ajeno = self.crear_pedido(
            self.otro_repartidor,
            cliente=cliente_ajeno
        )
        Pedido.objects.filter(pk=credito_ajeno.pk).update(
            estado=Pedido.ENTREGADO,
            fecha_entrega=timezone.now(),
            metodo_pago=Pedido.PAGO_FIADO
        )
        self.client.force_login(self.repartidor)

        response = self.client.get(reverse('pedidos_repartidor_fragmento'))

        self.assertEqual(response.context['total_creditos_pendientes'], 1)
        self.assertContains(response, 'Cr&eacute;ditos pendientes (1)')
        self.assertContains(response, cliente_propio.nombre)
        self.assertContains(response, 'Crédito pendiente')
        self.assertContains(response, 'Cobrar')
        self.assertContains(response, 'Abrir ubicaci&oacute;n')
        self.assertNotContains(response, cliente_ajeno.nombre)
        self.assertNotContains(response, 'Fiado')

    def test_panel_repartidor_muestra_ver_foto_en_credito_si_existe(self):
        cliente_con_foto = Cliente.objects.create(
            nombre='Cliente Credito Foto',
            telefono='999333113',
            direccion='Av. Foto Credito 123',
            foto_referencia_url='https://res.cloudinary.com/demo/fachada.jpg',
            foto_referencia_public_id='aquasmart/clientes/fachada'
        )
        credito = self.crear_pedido(
            self.repartidor,
            cliente=cliente_con_foto
        )
        Pedido.objects.filter(pk=credito.pk).update(
            estado=Pedido.ENTREGADO,
            fecha_entrega=timezone.now(),
            metodo_pago=Pedido.PAGO_FIADO
        )
        self.client.force_login(self.repartidor)

        response = self.client.get(reverse('pedidos_repartidor_fragmento'))

        self.assertContains(response, 'Gestionar cr&eacute;dito')
        self.assertContains(response, 'Ver foto')
        self.assertContains(
            response,
            'https://res.cloudinary.com/demo/fachada.jpg'
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

    def test_flujo_principal_oculta_programacion_visible(self):
        pedido = self.crear_pedido(self.repartidor)
        self.client.force_login(self.secretaria)

        registrar = self.client.get(reverse('registrar_pedido'))
        self.assertNotContains(registrar, 'Fecha programada')
        self.assertNotContains(registrar, 'Reprogramado')

        self.client.force_login(self.repartidor)
        repartidor = self.client.get(reverse('nuevo_pedido_repartidor'))
        self.assertNotContains(repartidor, 'Fecha programada')

        self.client.force_login(self.secretaria)
        editar = self.client.get(reverse('editar_pedido', kwargs={'pedido_id': pedido.id}))
        self.assertContains(editar, 'Ajuste avanzado')
        self.assertContains(editar, 'Cambiar fecha')

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
                'lugar': str(self.lugar_operativo.id),
            }
        )

        self.assertRedirects(
            response,
            reverse('lista_pedidos'),
            fetch_redirect_response=False
        )
        pedido = Pedido.objects.latest('id')
        self.assertEqual(pedido.fecha_programada, timezone.localdate())

    def test_registrar_pedido_ignora_fecha_programada_futura_en_alta(self):
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
        self.assertEqual(pedido.fecha_programada, timezone.localdate())

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

    def test_nuevo_pedido_repartidor_ignora_fecha_programada_futura_en_alta(self):
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
        self.assertEqual(pedido.fecha_programada, timezone.localdate())
        self.assertEqual(pedido.repartidor, self.repartidor)

    def test_nuevo_pedido_repartidor_rechaza_cantidad_mayor_a_100(self):
        self.client.force_login(self.repartidor)

        self.client.post(
            reverse('nuevo_pedido_repartidor'),
            {
                'cliente': str(self.cliente.id),
                'cantidad_bidones': '101',
                'precio_unitario': '7.00',
                'observacion': '',
                'fecha_programada': '',
            }
        )

        self.assertFalse(Pedido.objects.exists())

    def test_nuevo_pedido_repartidor_rechaza_precio_mayor_a_50(self):
        self.client.force_login(self.repartidor)

        self.client.post(
            reverse('nuevo_pedido_repartidor'),
            {
                'cliente': str(self.cliente.id),
                'cantidad_bidones': '2',
                'precio_unitario': '50.01',
                'observacion': '',
                'fecha_programada': '',
            }
        )

        self.assertFalse(Pedido.objects.exists())

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

    def test_panel_repartidor_muestra_futuros_historicos_en_flujo_discreto(self):
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
        self.assertIn(pedido_manana, response.context['pedidos_hoy'])
        self.assertEqual(
            list(response.context['pedidos_hoy'])[:3],
            [pedido_atrasado, pedido_hoy, pedido_manana]
        )
        self.assertContains(response, 'Pedidos pendientes')
        self.assertContains(response, 'Entrega posterior')
        self.assertNotContains(response, 'Ver pedidos programados')

    def test_panel_jefe_muestra_futuros_historicos_en_flujo_discreto(self):
        hoy = timezone.localdate()
        pedido_sin_asignar_hoy = self.crear_pedido(
            repartidor=None,
            fecha_programada=hoy
        )
        pedido_sin_asignar_hoy.cliente.referencia = 'Referencia antigua'
        pedido_sin_asignar_hoy.cliente.referencia_ubicacion = 'Porton negro'
        pedido_sin_asignar_hoy.cliente.save(
            update_fields=['referencia', 'referencia_ubicacion']
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
            response.context['pedidos_asignados']
        )
        self.assertContains(response, 'Entrega posterior')
        self.assertContains(response, 'Referencia para llegar')
        self.assertContains(response, 'Porton negro')
        self.assertNotContains(response, 'Referencia antigua')
        self.assertNotContains(response, 'Pedidos programados futuros')

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

    def test_repartidor_no_actualiza_referencia_de_pedido_cerrado_antiguo(self):
        pedido = self.crear_pedido(self.repartidor)
        momento = timezone.now() - timedelta(days=2)
        pedido.registrar_estado(Pedido.ENTREGADO, self.repartidor, momento=momento)
        pedido.save()
        self.client.force_login(self.repartidor)

        response = self.client.get(
            reverse(
                'actualizar_referencia_cliente_repartidor',
                kwargs={'cliente_id': self.cliente.id}
            )
        )

        self.assertEqual(response.status_code, 404)

    def test_repartidor_actualiza_referencia_de_entrega_reciente(self):
        pedido = self.crear_pedido(self.repartidor)
        pedido.registrar_estado(Pedido.ENTREGADO, self.repartidor)
        pedido.save()
        self.client.force_login(self.repartidor)

        response = self.client.get(
            reverse(
                'actualizar_referencia_cliente_repartidor',
                kwargs={'cliente_id': self.cliente.id}
            )
        )

        self.assertEqual(response.status_code, 200)

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
                'observacion': 'Cliente corrigi\u00f3 cantidad al recibir.',
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
            'Cliente corrigi\u00f3 cantidad al recibir.'
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
                'lugar': str(self.lugar_operativo.id),
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
            ),
            {'metodo_pago': Pedido.PAGO_EFECTIVO}
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
                'observacion': 'Asignado desde edici\u00f3n.',
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

    def test_registrar_pedido_revierte_creacion_si_falla_historial(self):
        self.client.force_login(self.secretaria)

        with patch(
            'core.views.registrar_historial_pedido',
            side_effect=RuntimeError('historial no disponible')
        ):
            with self.assertRaises(RuntimeError):
                self.client.post(
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

        self.assertFalse(Pedido.objects.exists())

    def test_metodo_pago_por_defecto_es_pendiente(self):
        pedido = self.crear_pedido(self.repartidor)

        self.assertEqual(pedido.metodo_pago, Pedido.PAGO_PENDIENTE)

    def test_secretaria_crea_lugar_y_cliente_puede_tenerlo(self):
        self.client.force_login(self.secretaria)
        response = self.client.post(
            reverse('lugares'),
            {'nombre': 'Perene', 'orden': '2'}
        )

        self.assertRedirects(
            response,
            reverse('lugares'),
            fetch_redirect_response=False
        )
        lugar = Lugar.objects.get(nombre='Perene')
        self.client.post(
            reverse('registrar_cliente'),
            {
                'nombre': 'Cliente Zona',
                'telefono': '999444333',
                'direccion': 'Av. Zona 4',
                'referencia': '',
                'lugar': str(lugar.id),
            }
        )

        self.assertEqual(
            Cliente.objects.get(telefono='999444333').lugar,
            lugar
        )

    def test_pedido_hereda_lugar_del_cliente_y_permite_lugar_propio(self):
        lugar_cliente = self.lugar_operativo
        lugar_pedido = Lugar.objects.create(nombre='San Luis de Shuaro', orden=2)
        self.cliente.lugar = lugar_cliente
        self.cliente.save(update_fields=['lugar'])
        self.client.force_login(self.secretaria)

        for lugar_enviado in ('', str(lugar_pedido.id)):
            self.client.post(
                reverse('registrar_pedido'),
                {
                    'cliente': str(self.cliente.id),
                    'repartidor': '',
                    'cantidad_bidones': '2',
                    'precio_unitario': '7.00',
                    'metodo_pago': Pedido.PAGO_PENDIENTE,
                    'estado': Pedido.PENDIENTE,
                    'observacion': '',
                    'fecha_programada': '',
                    'lugar': lugar_enviado,
                }
            )

        pedidos = list(Pedido.objects.order_by('id'))
        self.assertEqual(pedidos[0].lugar, lugar_cliente)
        self.assertEqual(pedidos[1].lugar, lugar_pedido)

    def test_no_permite_entregar_sin_lugar(self):
        pedido = self.crear_pedido(self.repartidor, con_lugar=False)
        self.client.force_login(self.secretaria)

        response = self.client.post(
            reverse(
                'cambiar_estado_pedido',
                kwargs={
                    'pedido_id': pedido.id,
                    'nuevo_estado': Pedido.ENTREGADO,
                }
            ),
            {'metodo_pago': Pedido.PAGO_EFECTIVO},
            follow=True
        )

        pedido.refresh_from_db()
        self.assertEqual(pedido.estado, Pedido.PENDIENTE)
        self.assertIsNone(pedido.lugar)
        self.assertContains(
            response,
            'Selecciona el lugar del pedido antes de finalizar.'
        )

    def test_permite_entregar_si_se_envia_lugar_valido(self):
        pedido = self.crear_pedido(self.repartidor, con_lugar=False)
        self.client.force_login(self.secretaria)

        self.client.post(
            reverse(
                'cambiar_estado_pedido',
                kwargs={
                    'pedido_id': pedido.id,
                    'nuevo_estado': Pedido.ENTREGADO,
                }
            ),
            {
                'metodo_pago': Pedido.PAGO_EFECTIVO,
                'lugar': str(self.lugar_operativo.id),
            }
        )

        pedido.refresh_from_db()
        self.assertEqual(pedido.estado, Pedido.ENTREGADO)
        self.assertEqual(pedido.lugar, self.lugar_operativo)

    def test_no_permite_cancelar_sin_lugar_y_permite_lugar_valido(self):
        pedido_sin_lugar = self.crear_pedido(self.repartidor, con_lugar=False)
        pedido_con_lugar_post = self.crear_pedido(
            self.repartidor,
            con_lugar=False
        )
        self.client.force_login(self.secretaria)

        self.client.post(
            reverse(
                'cambiar_estado_pedido',
                kwargs={
                    'pedido_id': pedido_sin_lugar.id,
                    'nuevo_estado': Pedido.CANCELADO,
                }
            )
        )
        self.client.post(
            reverse(
                'cambiar_estado_pedido',
                kwargs={
                    'pedido_id': pedido_con_lugar_post.id,
                    'nuevo_estado': Pedido.CANCELADO,
                }
            ),
            {'lugar': str(self.lugar_operativo.id)}
        )

        pedido_sin_lugar.refresh_from_db()
        pedido_con_lugar_post.refresh_from_db()
        self.assertEqual(pedido_sin_lugar.estado, Pedido.PENDIENTE)
        self.assertIsNone(pedido_sin_lugar.lugar)
        self.assertEqual(pedido_con_lugar_post.estado, Pedido.CANCELADO)
        self.assertEqual(pedido_con_lugar_post.lugar, self.lugar_operativo)

    def test_no_permite_usar_lugar_inactivo_para_finalizar(self):
        lugar_inactivo = Lugar.objects.create(
            nombre='Zona inactiva',
            activo=False
        )
        pedido = self.crear_pedido(self.repartidor, con_lugar=False)
        self.client.force_login(self.secretaria)

        response = self.client.post(
            reverse(
                'cambiar_estado_pedido',
                kwargs={
                    'pedido_id': pedido.id,
                    'nuevo_estado': Pedido.ENTREGADO,
                }
            ),
            {
                'metodo_pago': Pedido.PAGO_EFECTIVO,
                'lugar': str(lugar_inactivo.id),
            },
            follow=True
        )

        pedido.refresh_from_db()
        self.assertEqual(pedido.estado, Pedido.PENDIENTE)
        self.assertIsNone(pedido.lugar)
        self.assertContains(response, 'El lugar seleccionado no est\u00e1 activo.')

    def test_pedido_historico_sin_lugar_sigue_listandose(self):
        pedido = self.crear_pedido(self.repartidor, con_lugar=False)
        Pedido.objects.filter(pk=pedido.pk).update(
            estado=Pedido.ENTREGADO,
            fecha_entrega=timezone.now()
        )
        self.client.force_login(self.secretaria)

        response = self.client.get(reverse('lista_pedidos'))

        self.assertContains(response, 'Sin lugar asignado')

    def test_repartidor_no_puede_cancelar_sin_lugar(self):
        pedido = self.crear_pedido(self.repartidor, con_lugar=False)
        self.client.force_login(self.repartidor)

        response = self.client.post(
            reverse(
                'cancelar_pedido_repartidor',
                kwargs={'pedido_id': pedido.id}
            ),
            follow=True
        )

        pedido.refresh_from_db()
        self.assertEqual(pedido.estado, Pedido.PENDIENTE)
        self.assertIsNone(pedido.lugar)
        self.assertContains(
            response,
            'Selecciona el lugar del pedido antes de finalizar.'
        )

    def test_lugares_inactivos_no_aparecen_en_formularios_operativos(self):
        Lugar.objects.create(nombre='Zona Activa', activo=True)
        Lugar.objects.create(nombre='Zona Inactiva', activo=False)
        self.client.force_login(self.secretaria)

        cliente_form = self.client.get(reverse('registrar_cliente'))
        pedido_form = self.client.get(reverse('registrar_pedido'))

        self.assertContains(cliente_form, 'Zona Activa')
        self.assertNotContains(cliente_form, 'Zona Inactiva')
        self.assertContains(pedido_form, 'Zona Activa')
        self.assertNotContains(pedido_form, 'Zona Inactiva')

    def test_registrar_pedido_guarda_metodo_pago_seleccionado(self):
        self.client.force_login(self.secretaria)

        self.client.post(
            reverse('registrar_pedido'),
            {
                'cliente': str(self.cliente.id),
                'repartidor': '',
                'cantidad_bidones': '2',
                'precio_unitario': '7.00',
                'metodo_pago': Pedido.PAGO_YAPE,
                'estado': Pedido.PENDIENTE,
                'observacion': '',
                'fecha_programada': '',
            }
        )

        self.assertEqual(
            Pedido.objects.latest('id').metodo_pago,
            Pedido.PAGO_YAPE
        )

    def test_crear_pedido_entregado_exige_metodo_pago_valido(self):
        self.client.force_login(self.secretaria)

        self.client.post(
            reverse('registrar_pedido'),
            {
                'cliente': str(self.cliente.id),
                'repartidor': str(self.repartidor.id),
                'cantidad_bidones': '2',
                'precio_unitario': '7.00',
                'metodo_pago': Pedido.PAGO_PENDIENTE,
                'estado': Pedido.ENTREGADO,
                'observacion': '',
                'fecha_programada': '',
            }
        )

        self.assertFalse(Pedido.objects.exists())

    def test_nuevo_pedido_repartidor_entregado_exige_metodo_pago_valido(self):
        self.client.force_login(self.repartidor)

        self.client.post(
            reverse('nuevo_pedido_repartidor'),
            {
                'cliente': str(self.cliente.id),
                'cantidad_bidones': '2',
                'precio_unitario': '7.00',
                'metodo_pago': Pedido.PAGO_PENDIENTE,
                'entregar_ahora': 'on',
                'observacion': '',
                'fecha_programada': '',
            }
        )

        self.assertFalse(Pedido.objects.exists())

    def test_editar_pedido_actualiza_metodo_pago(self):
        pedido = self.crear_pedido(self.repartidor)
        self.client.force_login(self.secretaria)

        self.client.post(
            reverse('editar_pedido', kwargs={'pedido_id': pedido.id}),
            {
                'cantidad_bidones': '2',
                'precio_unitario': '7.00',
                'metodo_pago': Pedido.PAGO_EFECTIVO,
                'fecha_programada': timezone.localdate().strftime('%Y-%m-%d'),
                'observacion': '',
                'repartidor': str(self.repartidor.id),
            }
        )

        pedido.refresh_from_db()
        self.assertEqual(pedido.metodo_pago, Pedido.PAGO_EFECTIVO)

    def test_entrega_administrativa_con_efectivo_suma_en_pagos(self):
        pedido = self.crear_pedido(self.repartidor)
        self.client.force_login(self.secretaria)

        self.client.post(
            reverse(
                'cambiar_estado_pedido',
                kwargs={
                    'pedido_id': pedido.id,
                    'nuevo_estado': Pedido.ENTREGADO,
                }
            ),
            {'metodo_pago': Pedido.PAGO_EFECTIVO}
        )
        pedido.refresh_from_db()
        response = self.client.get(reverse('pagos'))

        self.assertEqual(pedido.metodo_pago, Pedido.PAGO_EFECTIVO)
        self.assertEqual(response.context['efectivo_hoy'], Decimal('14.00'))
        self.assertEqual(response.context['yape_hoy'], Decimal('0.00'))
        self.assertEqual(response.context['total_cobrado_hoy'], Decimal('14.00'))

    def test_entrega_administrativa_rechaza_pago_pendiente_o_ausente(self):
        self.client.force_login(self.secretaria)

        for datos in ({}, {'metodo_pago': Pedido.PAGO_PENDIENTE}):
            pedido = self.crear_pedido(self.repartidor)
            response = self.client.post(
                reverse(
                    'cambiar_estado_pedido',
                    kwargs={
                        'pedido_id': pedido.id,
                        'nuevo_estado': Pedido.ENTREGADO,
                    }
                ),
                datos,
                follow=True
            )
            pedido.refresh_from_db()

            self.assertEqual(pedido.estado, Pedido.PENDIENTE)
            self.assertIsNone(pedido.fecha_entrega)
            self.assertContains(
                response,
                'Selecciona un m\u00e9todo de pago antes de marcar como entregado.'
            )

    def test_entrega_repartidor_con_yape_suma_en_pagos(self):
        pedido = self.crear_pedido(self.repartidor)
        self.client.force_login(self.repartidor)

        self.client.post(
            reverse(
                'marcar_pedido_entregado_repartidor',
                kwargs={'pedido_id': pedido.id}
            ),
            {'metodo_pago': Pedido.PAGO_YAPE}
        )
        pedido.refresh_from_db()
        self.client.force_login(self.secretaria)
        response = self.client.get(reverse('pagos'))

        self.assertEqual(pedido.metodo_pago, Pedido.PAGO_YAPE)
        self.assertEqual(response.context['efectivo_hoy'], Decimal('0.00'))
        self.assertEqual(response.context['yape_hoy'], Decimal('14.00'))
        self.assertEqual(response.context['total_cobrado_hoy'], Decimal('14.00'))

    def test_entrega_repartidor_rechaza_pago_pendiente(self):
        pedido = self.crear_pedido(self.repartidor)
        self.client.force_login(self.repartidor)

        self.client.post(
            reverse(
                'marcar_pedido_entregado_repartidor',
                kwargs={'pedido_id': pedido.id}
            ),
            {'metodo_pago': Pedido.PAGO_PENDIENTE}
        )

        pedido.refresh_from_db()
        self.assertEqual(pedido.estado, Pedido.PENDIENTE)
        self.assertIsNone(pedido.fecha_entrega)

    def test_entrega_repartidor_con_plin_suma_en_pagos(self):
        pedido = self.crear_pedido(self.repartidor)
        self.client.force_login(self.repartidor)

        self.client.post(
            reverse(
                'marcar_pedido_entregado_repartidor',
                kwargs={'pedido_id': pedido.id}
            ),
            {'metodo_pago': Pedido.PAGO_PLIN}
        )

        self.client.force_login(self.secretaria)
        response = self.client.get(reverse('pagos'))

        self.assertEqual(response.context['plin_hoy'], Decimal('14.00'))
        self.assertEqual(response.context['total_cobrado_hoy'], Decimal('14.00'))

    def test_entrega_con_transferencia_suma_en_pagos_y_reporte_semanal(self):
        pedido = self.crear_pedido(self.repartidor)
        self.client.force_login(self.repartidor)

        self.client.post(
            reverse(
                'marcar_pedido_entregado_repartidor',
                kwargs={'pedido_id': pedido.id}
            ),
            {'metodo_pago': Pedido.PAGO_TRANSFERENCIA}
        )
        pedido.refresh_from_db()
        self.client.force_login(self.secretaria)
        pagos = self.client.get(reverse('pagos'))
        semanal = self.client.get(reverse('reporte_semanal'))

        self.assertEqual(pedido.estado, Pedido.ENTREGADO)
        self.assertEqual(pedido.metodo_pago, Pedido.PAGO_TRANSFERENCIA)
        self.assertEqual(
            pagos.context['transferencia_hoy'],
            Decimal('14.00')
        )
        self.assertEqual(pagos.context['total_cobrado_hoy'], Decimal('14.00'))
        self.assertContains(pagos, 'Transferencia')
        self.assertEqual(semanal.context['transferencia'], Decimal('14.00'))
        self.assertEqual(semanal.context['cobrado'], Decimal('14.00'))
        self.assertContains(semanal, 'Transferencia')

    def test_entrega_repartidor_fiada_genera_deuda_sin_sumar_cobro(self):
        pedido = self.crear_pedido(self.repartidor)
        self.client.force_login(self.repartidor)

        self.client.post(
            reverse(
                'marcar_pedido_entregado_repartidor',
                kwargs={'pedido_id': pedido.id}
            ),
            {'metodo_pago': Pedido.PAGO_FIADO}
        )

        pedido.refresh_from_db()
        self.client.force_login(self.secretaria)
        pagos = self.client.get(reverse('pagos'))
        dashboard = self.client.get(reverse('dashboard'))

        self.assertEqual(pedido.estado, Pedido.ENTREGADO)
        self.assertEqual(pedido.metodo_pago, Pedido.PAGO_FIADO)
        self.assertIsNone(pedido.fecha_pago)
        self.assertEqual(pagos.context['fiado_hoy'], Decimal('14.00'))
        self.assertEqual(pagos.context['efectivo_hoy'], Decimal('0.00'))
        self.assertEqual(pagos.context['yape_hoy'], Decimal('0.00'))
        self.assertEqual(pagos.context['total_cobrado_hoy'], Decimal('0.00'))
        self.assertEqual(pagos.context['total_por_cobrar'], Decimal('14.00'))
        self.assertEqual(len(pagos.context['fiados_pendientes']), 1)
        self.assertEqual(dashboard.context['bidones_hoy'], 2)
        self.assertEqual(dashboard.context['total_por_cobrar'], Decimal('14.00'))

    def test_marcar_fiado_pagado_registra_metodo_fecha_y_usuario(self):
        pedido = self.crear_pedido(self.repartidor)
        Pedido.objects.filter(pk=pedido.pk).update(
            estado=Pedido.ENTREGADO,
            fecha_entrega=timezone.now(),
            metodo_pago=Pedido.PAGO_FIADO
        )
        self.client.force_login(self.secretaria)

        self.client.post(
            reverse('marcar_fiado_pagado', kwargs={'pedido_id': pedido.id}),
            {'metodo_pago_final': Pedido.PAGO_PLIN}
        )

        pedido.refresh_from_db()
        pagos = self.client.get(reverse('pagos'))

        self.assertEqual(pedido.metodo_pago, Pedido.PAGO_FIADO)
        self.assertEqual(pedido.metodo_pago_final, Pedido.PAGO_PLIN)
        self.assertIsNotNone(pedido.fecha_pago)
        self.assertEqual(pedido.usuario_pago, self.secretaria)
        self.assertEqual(pagos.context['plin_hoy'], Decimal('14.00'))
        self.assertEqual(pagos.context['total_por_cobrar'], Decimal('0.00'))
        self.assertTrue(
            PedidoHistorial.objects.filter(
                pedido=pedido,
                usuario=self.secretaria,
                descripcion='Cobro posterior de pedido fiado registrado.'
            ).exists()
        )

    def test_entrega_historica_pendiente_sigue_visible_sin_metodo_registrado(self):
        pedido = self.crear_pedido(self.repartidor)
        Pedido.objects.filter(pk=pedido.pk).update(
            estado=Pedido.ENTREGADO,
            fecha_entrega=timezone.now(),
            metodo_pago=Pedido.PAGO_PENDIENTE
        )
        self.client.force_login(self.secretaria)

        response = self.client.get(reverse('pagos'))

        self.assertEqual(response.context['total_pendientes_pago'], 1)
        self.assertContains(response, 'Sin m&eacute;todo registrado')
        self.assertEqual(response.context['total_cobrado_hoy'], Decimal('0.00'))

    def test_repartidor_no_puede_cerrar_deuda_fiada(self):
        pedido = self.crear_pedido(self.repartidor)
        Pedido.objects.filter(pk=pedido.pk).update(
            estado=Pedido.ENTREGADO,
            fecha_entrega=timezone.now(),
            metodo_pago=Pedido.PAGO_FIADO
        )
        self.client.force_login(self.repartidor)

        response = self.client.post(
            reverse('marcar_fiado_pagado', kwargs={'pedido_id': pedido.id}),
            {'metodo_pago_final': Pedido.PAGO_EFECTIVO}
        )

        pedido.refresh_from_db()
        self.assertEqual(response.status_code, 302)
        self.assertIsNone(pedido.fecha_pago)
        self.assertIsNone(pedido.metodo_pago_final)

    def test_repartidor_puede_cobrar_credito_pendiente_propio(self):
        pedido = self.crear_pedido(self.repartidor)
        Pedido.objects.filter(pk=pedido.pk).update(
            estado=Pedido.ENTREGADO,
            fecha_entrega=timezone.now(),
            metodo_pago=Pedido.PAGO_FIADO
        )
        self.client.force_login(self.repartidor)

        response = self.client.post(
            reverse(
                'cobrar_credito_repartidor',
                kwargs={'pedido_id': pedido.id}
            ),
            {'metodo_pago_final': Pedido.PAGO_YAPE}
        )

        pedido.refresh_from_db()
        self.assertRedirects(response, reverse('pedidos_repartidor'))
        self.assertEqual(pedido.metodo_pago, Pedido.PAGO_FIADO)
        self.assertEqual(pedido.metodo_pago_final, Pedido.PAGO_YAPE)
        self.assertIsNotNone(pedido.fecha_pago)
        self.assertEqual(pedido.usuario_pago, self.repartidor)
        self.assertTrue(
            PedidoHistorial.objects.filter(
                pedido=pedido,
                usuario=self.repartidor,
                descripcion='Cobro posterior de crédito registrado por repartidor.',
                valor_anterior='Crédito pendiente',
                valor_nuevo='Yape'
            ).exists()
        )

    def test_repartidor_no_puede_cobrar_credito_de_otro_repartidor(self):
        pedido = self.crear_pedido(self.otro_repartidor)
        Pedido.objects.filter(pk=pedido.pk).update(
            estado=Pedido.ENTREGADO,
            fecha_entrega=timezone.now(),
            metodo_pago=Pedido.PAGO_FIADO
        )
        self.client.force_login(self.repartidor)

        response = self.client.post(
            reverse(
                'cobrar_credito_repartidor',
                kwargs={'pedido_id': pedido.id}
            ),
            {'metodo_pago_final': Pedido.PAGO_EFECTIVO}
        )

        pedido.refresh_from_db()
        self.assertEqual(response.status_code, 404)
        self.assertIsNone(pedido.fecha_pago)
        self.assertIsNone(pedido.metodo_pago_final)
        self.assertIsNone(pedido.usuario_pago)

    def test_repartidor_no_puede_cobrar_credito_con_metodo_invalido(self):
        pedido = self.crear_pedido(self.repartidor)
        Pedido.objects.filter(pk=pedido.pk).update(
            estado=Pedido.ENTREGADO,
            fecha_entrega=timezone.now(),
            metodo_pago=Pedido.PAGO_FIADO
        )
        self.client.force_login(self.repartidor)

        response = self.client.post(
            reverse(
                'cobrar_credito_repartidor',
                kwargs={'pedido_id': pedido.id}
            ),
            {'metodo_pago_final': Pedido.PAGO_FIADO}
        )

        pedido.refresh_from_db()
        self.assertRedirects(response, reverse('pedidos_repartidor'))
        self.assertIsNone(pedido.fecha_pago)
        self.assertIsNone(pedido.metodo_pago_final)
        self.assertIsNone(pedido.usuario_pago)

    def test_jefe_puede_cerrar_deuda_fiada(self):
        pedido = self.crear_pedido(self.repartidor)
        Pedido.objects.filter(pk=pedido.pk).update(
            estado=Pedido.ENTREGADO,
            fecha_entrega=timezone.now(),
            metodo_pago=Pedido.PAGO_FIADO
        )
        self.client.force_login(self.jefe_reparto)

        self.client.post(
            reverse('marcar_fiado_pagado', kwargs={'pedido_id': pedido.id}),
            {'metodo_pago_final': Pedido.PAGO_YAPE}
        )

        pedido.refresh_from_db()
        self.assertEqual(pedido.metodo_pago_final, Pedido.PAGO_YAPE)
        self.assertEqual(pedido.usuario_pago, self.jefe_reparto)
        self.assertIsNotNone(pedido.fecha_pago)

    def test_plin_suma_correctamente_y_pagos_filtra_por_lugar(self):
        lugar_plin = Lugar.objects.create(nombre='Pichanaki', orden=1)
        lugar_otro = Lugar.objects.create(nombre='Perene', orden=2)
        pedido_plin = self.crear_pedido(self.repartidor)
        pedido_otro = self.crear_pedido(self.repartidor)
        Pedido.objects.filter(pk=pedido_plin.pk).update(
            lugar=lugar_plin,
            estado=Pedido.ENTREGADO,
            fecha_entrega=timezone.now(),
            metodo_pago=Pedido.PAGO_PLIN
        )
        Pedido.objects.filter(pk=pedido_otro.pk).update(
            lugar=lugar_otro,
            estado=Pedido.ENTREGADO,
            fecha_entrega=timezone.now(),
            metodo_pago=Pedido.PAGO_EFECTIVO
        )
        self.client.force_login(self.secretaria)

        response = self.client.get(reverse('pagos'), {'lugar': lugar_plin.id})

        self.assertEqual(response.context['plin_periodo'], Decimal('14.00'))
        self.assertEqual(response.context['efectivo_periodo'], Decimal('0.00'))
        self.assertEqual(response.context['total_cobrado_periodo'], Decimal('14.00'))
        self.assertContains(response, 'Pichanaki')
        self.assertEqual(
            [fila['nombre'] for fila in response.context['resumen_lugares']],
            ['Pichanaki']
        )

    def test_reporte_semanal_usa_fecha_entrega_y_no_suma_no_entregados(self):
        pedido_entregado = self.crear_pedido(self.repartidor)
        Pedido.objects.filter(pk=pedido_entregado.pk).update(
            fecha_pedido=timezone.now() - timedelta(days=30),
            estado=Pedido.ENTREGADO,
            fecha_entrega=timezone.now(),
            metodo_pago=Pedido.PAGO_YAPE
        )
        self.crear_pedido(self.repartidor)
        self.client.force_login(self.secretaria)

        response = self.client.get(reverse('reporte_semanal'))

        self.assertEqual(response.context['total_ventas'], Decimal('14.00'))
        self.assertEqual(response.context['total_bidones'], 2)
        self.assertEqual(response.context['total_entregados'], 1)
        self.assertEqual(response.context['yape'], Decimal('14.00'))
        self.assertEqual(response.context['efectivo'], Decimal('0.00'))

    def test_reporte_semanal_filtra_por_lugar(self):
        lugar_uno = Lugar.objects.create(nombre='Satipo', orden=1)
        lugar_dos = Lugar.objects.create(nombre='Oxapampa', orden=2)
        pedido_uno = self.crear_pedido(self.repartidor)
        pedido_dos = self.crear_pedido(self.repartidor)
        Pedido.objects.filter(pk=pedido_uno.pk).update(
            lugar=lugar_uno,
            estado=Pedido.ENTREGADO,
            fecha_entrega=timezone.now(),
            metodo_pago=Pedido.PAGO_YAPE
        )
        Pedido.objects.filter(pk=pedido_dos.pk).update(
            lugar=lugar_dos,
            estado=Pedido.ENTREGADO,
            fecha_entrega=timezone.now(),
            metodo_pago=Pedido.PAGO_EFECTIVO
        )
        self.client.force_login(self.secretaria)

        response = self.client.get(
            reverse('reporte_semanal'),
            {'lugar': lugar_uno.id}
        )

        self.assertEqual(response.context['total_ventas'], Decimal('14.00'))
        self.assertEqual(response.context['yape'], Decimal('14.00'))
        self.assertEqual(response.context['efectivo'], Decimal('0.00'))
        self.assertContains(response, 'Satipo')
        self.assertEqual(
            list(response.context['pedidos_entregados']),
            [pedido_uno]
        )

    def test_reporte_semanal_separa_venta_fiada_y_cobro_posterior(self):
        pedido_fiado = self.crear_pedido(self.repartidor)
        Pedido.objects.filter(pk=pedido_fiado.pk).update(
            estado=Pedido.ENTREGADO,
            fecha_entrega=timezone.now(),
            metodo_pago=Pedido.PAGO_FIADO
        )
        self.client.force_login(self.secretaria)

        reporte_pendiente = self.client.get(reverse('reporte_semanal'))

        self.assertEqual(reporte_pendiente.context['total_ventas'], Decimal('14.00'))
        self.assertEqual(reporte_pendiente.context['total_bidones'], 2)
        self.assertEqual(reporte_pendiente.context['fiado'], Decimal('14.00'))
        self.assertEqual(reporte_pendiente.context['cobrado'], Decimal('0.00'))
        self.assertEqual(
            reporte_pendiente.context['cuentas_por_cobrar'],
            Decimal('14.00')
        )

        Pedido.objects.filter(pk=pedido_fiado.pk).update(
            fecha_entrega=timezone.now() - timedelta(days=14),
            fecha_pago=timezone.now(),
            metodo_pago_final=Pedido.PAGO_EFECTIVO
        )
        reporte_cobrado = self.client.get(reverse('reporte_semanal'))

        self.assertEqual(reporte_cobrado.context['total_ventas'], Decimal('0.00'))
        self.assertEqual(reporte_cobrado.context['efectivo'], Decimal('14.00'))
        self.assertEqual(reporte_cobrado.context['cobrado'], Decimal('14.00'))

    def test_pagos_y_reporte_semanal_mantienen_permiso_administrativo(self):
        for route_name in ('pagos', 'reporte_semanal', 'lugares', 'egresos'):
            response = self.client.get(reverse(route_name))
            self.assertEqual(response.status_code, 302)

            self.client.force_login(self.repartidor)
            response = self.client.get(reverse(route_name))
            self.assertEqual(response.status_code, 302)
            self.client.logout()

            self.client.force_login(self.secretaria)
            response = self.client.get(reverse(route_name))
            self.assertEqual(response.status_code, 200)
            self.client.logout()

        self.client.force_login(self.jefe_reparto)
        self.assertEqual(self.client.get(reverse('pagos')).status_code, 200)

    def test_egresos_registra_y_valida_monto_obligatorio(self):
        self.client.force_login(self.secretaria)

        response = self.client.post(
            reverse('egresos'),
            {
                'monto': '0',
                'categoria': Egreso.CATEGORIA_MANTENIMIENTO,
                'metodo_pago': Egreso.PAGO_EFECTIVO,
                'concepto': 'Reparacion de tuberia',
            },
            follow=True
        )

        self.assertFalse(Egreso.objects.exists())
        self.assertContains(response, 'El monto debe ser mayor a 0.')

        self.client.post(
            reverse('egresos'),
            {
                'monto': '25.50',
                'categoria': Egreso.CATEGORIA_MANTENIMIENTO,
                'metodo_pago': Egreso.PAGO_EFECTIVO,
                'concepto': 'Reparacion de tuberia',
                'observacion': 'Plomero',
            }
        )

        egreso = Egreso.objects.get()
        self.assertEqual(egreso.monto, Decimal('25.50'))
        self.assertEqual(egreso.usuario_registro, self.secretaria)

    def test_editar_egreso_actualiza_cierre_diario_y_resumen_semanal(self):
        pedido = self.crear_pedido(self.repartidor)
        Pedido.objects.filter(pk=pedido.pk).update(
            estado=Pedido.ENTREGADO,
            fecha_entrega=timezone.now(),
            metodo_pago=Pedido.PAGO_EFECTIVO
        )
        egreso = Egreso.objects.create(
            monto=Decimal('5.00'),
            categoria=Egreso.CATEGORIA_MOVILIDAD,
            metodo_pago=Egreso.PAGO_EFECTIVO,
            concepto='Combustible mal registrado',
            usuario_registro=self.secretaria
        )
        self.client.force_login(self.secretaria)

        page = self.client.get(reverse('egresos'))
        self.assertContains(page, 'Editar')
        self.assertContains(
            page,
            reverse('editar_egreso', kwargs={'egreso_id': egreso.id})
        )
        edit_page = self.client.get(
            reverse('editar_egreso', kwargs={'egreso_id': egreso.id})
        )
        self.assertEqual(edit_page.status_code, 200)
        self.assertTemplateUsed(edit_page, 'core/egresos.html')
        self.assertContains(edit_page, 'Editar egreso')
        self.assertNotContains(edit_page, 'Compra corregida')
        self.assertNotContains(edit_page, 'editar_egreso.html')

        response = self.client.post(
            reverse('editar_egreso', kwargs={'egreso_id': egreso.id}),
            {
                'monto': '3.00',
                'categoria': Egreso.CATEGORIA_COMPRAS,
                'metodo_pago': Egreso.PAGO_YAPE,
                'concepto': 'Compra corregida',
                'observacion': 'Recibo corregido',
            }
        )

        egreso.refresh_from_db()
        diario = self.client.get(reverse('reporte_diario'))
        semanal = self.client.get(reverse('reporte_semanal'))

        self.assertRedirects(response, reverse('egresos'))
        self.assertEqual(egreso.monto, Decimal('3.00'))
        self.assertEqual(egreso.categoria, Egreso.CATEGORIA_COMPRAS)
        self.assertEqual(egreso.metodo_pago, Egreso.PAGO_YAPE)
        self.assertEqual(egreso.concepto, 'Compra corregida')
        self.assertEqual(egreso.observacion, 'Recibo corregido')
        self.assertEqual(egreso.usuario_registro, self.secretaria)
        self.assertEqual(diario.context['total_egresos_hoy'], Decimal('3.00'))
        self.assertEqual(diario.context['egresos_efectivo_hoy'], Decimal('0.00'))
        self.assertEqual(diario.context['efectivo_esperado'], Decimal('14.00'))
        self.assertEqual(semanal.context['total_egresos_semana'], Decimal('3.00'))
        self.assertEqual(semanal.context['neto_semanal'], Decimal('11.00'))

    def test_repartidor_no_puede_editar_egreso(self):
        egreso = Egreso.objects.create(
            monto=Decimal('5.00'),
            categoria=Egreso.CATEGORIA_MOVILIDAD,
            metodo_pago=Egreso.PAGO_EFECTIVO,
            concepto='Movilidad',
            usuario_registro=self.secretaria
        )
        self.client.force_login(self.repartidor)

        response = self.client.get(
            reverse('editar_egreso', kwargs={'egreso_id': egreso.id})
        )

        self.assertEqual(response.status_code, 302)

    def test_reporte_diario_calcula_cierre_sin_restar_egresos_digitales(self):
        pedido_efectivo = self.crear_pedido(self.repartidor)
        pedido_yape = self.crear_pedido(self.repartidor)
        Pedido.objects.filter(pk=pedido_efectivo.pk).update(
            estado=Pedido.ENTREGADO,
            fecha_entrega=timezone.now(),
            metodo_pago=Pedido.PAGO_EFECTIVO
        )
        Pedido.objects.filter(pk=pedido_yape.pk).update(
            estado=Pedido.ENTREGADO,
            fecha_entrega=timezone.now(),
            metodo_pago=Pedido.PAGO_YAPE
        )
        Egreso.objects.create(
            monto=Decimal('5.00'),
            categoria=Egreso.CATEGORIA_MOVILIDAD,
            metodo_pago=Egreso.PAGO_EFECTIVO,
            concepto='Combustible',
            usuario_registro=self.secretaria
        )
        Egreso.objects.create(
            monto=Decimal('3.00'),
            categoria=Egreso.CATEGORIA_COMPRAS,
            metodo_pago=Egreso.PAGO_YAPE,
            concepto='Materiales',
            usuario_registro=self.secretaria
        )
        self.client.force_login(self.secretaria)

        response = self.client.get(reverse('reporte_diario'))

        self.assertContains(response, 'Cierre de caja del dia')
        self.assertEqual(response.context['cobrado_hoy'], Decimal('28.00'))
        self.assertEqual(response.context['efectivo_cobrado_hoy'], Decimal('14.00'))
        self.assertEqual(response.context['digital_cobrado_hoy'], Decimal('14.00'))
        self.assertEqual(response.context['total_egresos_hoy'], Decimal('8.00'))
        self.assertEqual(response.context['egresos_efectivo_hoy'], Decimal('5.00'))
        self.assertEqual(response.context['efectivo_esperado'], Decimal('9.00'))

        self.client.post(
            reverse('registrar_cierre_caja_diario'),
            {
                'efectivo_contado': '8.00',
                'observacion_cierre': 'Falta revisar vuelto',
            }
        )
        faltante = self.client.get(reverse('reporte_diario'))
        self.assertEqual(faltante.context['estado_caja'], 'Faltante')
        self.assertEqual(faltante.context['diferencia_caja'], Decimal('-1.00'))

        self.client.post(
            reverse('registrar_cierre_caja_diario'),
            {
                'efectivo_contado': '9.00',
                'observacion_cierre': 'Cuadre revisado',
            }
        )
        cierre = CierreCajaDiario.objects.get(fecha=timezone.localdate())
        cuadrado = self.client.get(reverse('reporte_diario'))

        self.assertEqual(CierreCajaDiario.objects.count(), 1)
        self.assertEqual(cierre.efectivo_contado, Decimal('9.00'))
        self.assertEqual(cuadrado.context['estado_caja'], 'Cuadra')
        self.assertEqual(cuadrado.context['diferencia_caja'], Decimal('0.00'))

    def test_reporte_semanal_incluye_egresos_y_neto(self):
        pedido = self.crear_pedido(self.repartidor)
        Pedido.objects.filter(pk=pedido.pk).update(
            estado=Pedido.ENTREGADO,
            fecha_entrega=timezone.now(),
            metodo_pago=Pedido.PAGO_TRANSFERENCIA
        )
        Egreso.objects.create(
            monto=Decimal('4.00'),
            categoria=Egreso.CATEGORIA_MATERIALES,
            metodo_pago=Egreso.PAGO_TRANSFERENCIA,
            concepto='Cinta teflon',
            usuario_registro=self.secretaria
        )
        self.client.force_login(self.secretaria)

        response = self.client.get(reverse('reporte_semanal'))

        self.assertContains(response, 'Resumen financiero semanal')
        self.assertContains(response, 'Materiales')
        self.assertEqual(response.context['cobrado'], Decimal('14.00'))
        self.assertEqual(response.context['total_egresos_semana'], Decimal('4.00'))
        self.assertEqual(response.context['neto_semanal'], Decimal('10.00'))

    def test_dashboard_y_reporte_diario_contabilizan_por_fecha_entrega(self):
        pedido = self.crear_pedido(self.repartidor)
        Pedido.objects.filter(pk=pedido.pk).update(
            fecha_pedido=timezone.now() - timedelta(days=1),
            estado=Pedido.ENTREGADO,
            fecha_entrega=timezone.now()
        )
        self.client.force_login(self.secretaria)

        dashboard = self.client.get(reverse('dashboard'))
        diario = self.client.get(reverse('reporte_diario'))

        self.assertEqual(dashboard.context['ventas_hoy'], Decimal('14.00'))
        self.assertEqual(dashboard.context['bidones_hoy'], 2)
        self.assertEqual(diario.context['ventas_hoy'], Decimal('14.00'))
        self.assertEqual(diario.context['bidones_hoy'], 2)

    def test_reporte_diario_filtra_tabla_sin_cambiar_kpis_globales(self):
        pedido_yape = self.crear_pedido(self.repartidor)
        pedido_fiado_cobrado = self.crear_pedido(self.repartidor)
        pedido_fiado_pendiente = self.crear_pedido(self.repartidor)
        Pedido.objects.filter(pk=pedido_yape.pk).update(
            estado=Pedido.ENTREGADO,
            fecha_entrega=timezone.now(),
            metodo_pago=Pedido.PAGO_YAPE,
            observacion='Cliente pidio entrega antes del cierre.'
        )
        Pedido.objects.filter(pk=pedido_fiado_cobrado.pk).update(
            estado=Pedido.ENTREGADO,
            fecha_entrega=timezone.now(),
            metodo_pago=Pedido.PAGO_FIADO,
            metodo_pago_final=Pedido.PAGO_YAPE,
            fecha_pago=timezone.now()
        )
        Pedido.objects.filter(pk=pedido_fiado_pendiente.pk).update(
            estado=Pedido.ENTREGADO,
            fecha_entrega=timezone.now(),
            metodo_pago=Pedido.PAGO_FIADO
        )
        self.client.force_login(self.secretaria)

        response = self.client.get(
            reverse('reporte_diario'),
            {'metodo_pago': Pedido.PAGO_YAPE}
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Cierre operativo del dia')
        self.assertContains(response, 'Metodo de pago')
        self.assertContains(response, 'Repartidor')
        self.assertContains(response, 'Observacion')
        self.assertContains(response, 'Cobrado (Yape)')
        self.assertContains(response, 'filtro: Yape')
        self.assertNotContains(response, 'Ritmo reciente')
        self.assertEqual(response.context['ventas_hoy'], Decimal('42.00'))
        self.assertEqual(response.context['total_pedidos_filtrados'], 2)
        self.assertEqual(response.context['total_filtrado'], Decimal('28.00'))
        self.assertEqual(response.context['bidones_filtrados'], 4)

        fiado_response = self.client.get(
            reverse('reporte_diario'),
            {'metodo_pago': Pedido.PAGO_FIADO}
        )

        self.assertContains(fiado_response, 'Cr\u00e9dito pendiente')
        self.assertContains(fiado_response, 'Cr\u00e9dito')
        self.assertNotContains(fiado_response, 'Ventas de hoy')
        self.assertNotContains(fiado_response, 'Fiado hoy')
        self.assertEqual(fiado_response.context['ventas_hoy'], Decimal('42.00'))
        self.assertEqual(
            fiado_response.context['credito_otorgado_hoy'],
            Decimal('28.00')
        )
        self.assertEqual(fiado_response.context['total_pedidos_filtrados'], 1)
        self.assertEqual(fiado_response.context['fiado_filtrado'], Decimal('14.00'))

    def test_dashboard_resume_pagos_entregados_por_metodo(self):
        pedido = self.crear_pedido(self.repartidor)
        Pedido.objects.filter(pk=pedido.pk).update(
            estado=Pedido.ENTREGADO,
            fecha_entrega=timezone.now(),
            metodo_pago=Pedido.PAGO_EFECTIVO
        )
        self.client.force_login(self.secretaria)

        response = self.client.get(reverse('dashboard'))

        self.assertEqual(response.context['efectivo_hoy'], Decimal('14.00'))
        self.assertEqual(response.context['yape_hoy'], Decimal('0.00'))
        self.assertContains(response, 'Cobrado hoy')

    def test_dashboard_diario_y_pagos_usan_fecha_local_cerca_de_medianoche(self):
        hoy = timezone.localdate()
        entrega_local = timezone.make_aware(
            datetime.combine(hoy, time(23, 0)),
            timezone.get_default_timezone()
        )
        pedido = self.crear_pedido(self.repartidor)
        Pedido.objects.filter(pk=pedido.pk).update(
            estado=Pedido.ENTREGADO,
            fecha_entrega=entrega_local,
            metodo_pago=Pedido.PAGO_EFECTIVO
        )
        self.client.force_login(self.secretaria)

        dashboard = self.client.get(reverse('dashboard'))
        diario = self.client.get(reverse('reporte_diario'))
        semanal = self.client.get(reverse('reporte_semanal'))
        pagos = self.client.get(
            reverse('pagos'),
            {'fecha': hoy.strftime('%Y-%m-%d')}
        )

        self.assertEqual(dashboard.context['ventas_hoy'], Decimal('14.00'))
        self.assertEqual(diario.context['ventas_hoy'], Decimal('14.00'))
        self.assertEqual(semanal.context['total_ventas'], Decimal('14.00'))
        self.assertEqual(pagos.context['efectivo_periodo'], Decimal('14.00'))
        self.assertEqual(pagos.context['total_cobrado_periodo'], Decimal('14.00'))

        pedido_fiado = self.crear_pedido(self.repartidor)
        Pedido.objects.filter(pk=pedido_fiado.pk).update(
            estado=Pedido.ENTREGADO,
            fecha_entrega=entrega_local - timedelta(days=1),
            metodo_pago=Pedido.PAGO_FIADO,
            metodo_pago_final=Pedido.PAGO_YAPE,
            fecha_pago=entrega_local
        )
        pagos_con_cobro_posterior = self.client.get(
            reverse('pagos'),
            {'fecha': hoy.strftime('%Y-%m-%d')}
        )

        self.assertEqual(
            pagos_con_cobro_posterior.context['yape_periodo'],
            Decimal('14.00')
        )
        self.assertEqual(
            pagos_con_cobro_posterior.context['total_cobrado_periodo'],
            Decimal('28.00')
        )


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

    def test_reporte_mensual_contabiliza_por_fecha_entrega(self):
        cliente = Cliente.objects.create(
            nombre='Cliente Mensual',
            telefono='999111999',
            direccion='Av. Mes 1'
        )
        pedido = Pedido.objects.create(
            cliente=cliente,
            cantidad_bidones=3,
            precio_unitario=Decimal('7.00'),
            total=Decimal('21.00'),
            estado=Pedido.ENTREGADO,
            fecha_entrega=timezone.now()
        )
        Pedido.objects.filter(pk=pedido.pk).update(
            fecha_pedido=timezone.now() - timedelta(days=40)
        )
        hoy = timezone.localdate()
        self.client.force_login(self.secretaria)

        response = self.client.get(
            reverse('reporte_mensual'),
            {'mes': str(hoy.month), 'anio': str(hoy.year)}
        )

        self.assertEqual(response.context['ingresos_mes'], Decimal('21.00'))
        self.assertEqual(response.context['bidones_mes'], 3)
        self.assertContains(response, 'diario de ventas')

    def test_reporte_mensual_muestra_resumen_financiero_basico(self):
        cliente = Cliente.objects.create(
            nombre='Cliente Financiero',
            telefono='999111997',
            direccion='Av. Finanzas 1'
        )
        Pedido.objects.create(
            cliente=cliente,
            cantidad_bidones=2,
            precio_unitario=Decimal('7.00'),
            total=Decimal('14.00'),
            estado=Pedido.ENTREGADO,
            metodo_pago=Pedido.PAGO_EFECTIVO,
            fecha_entrega=timezone.now()
        )
        Pedido.objects.create(
            cliente=cliente,
            cantidad_bidones=1,
            precio_unitario=Decimal('7.00'),
            total=Decimal('7.00'),
            estado=Pedido.ENTREGADO,
            metodo_pago=Pedido.PAGO_FIADO,
            metodo_pago_final=Pedido.PAGO_YAPE,
            fecha_entrega=timezone.now() - timedelta(days=5),
            fecha_pago=timezone.now()
        )
        Egreso.objects.create(
            monto=Decimal('6.00'),
            categoria=Egreso.CATEGORIA_MATERIALES,
            metodo_pago=Egreso.PAGO_TRANSFERENCIA,
            concepto='Material mensual',
            usuario_registro=self.secretaria
        )
        hoy = timezone.localdate()
        self.client.force_login(self.secretaria)

        response = self.client.get(
            reverse('reporte_mensual'),
            {'mes': str(hoy.month), 'anio': str(hoy.year)}
        )

        self.assertContains(response, 'Ingresos cobrados del mes')
        self.assertContains(response, 'Egresos del mes')
        self.assertContains(response, 'Neto mensual')
        self.assertEqual(response.context['ingresos_cobrados_mes'], Decimal('21.00'))
        self.assertEqual(response.context['total_egresos_mes'], Decimal('6.00'))
        self.assertEqual(response.context['neto_mensual'], Decimal('15.00'))

    def test_reporte_mensual_agrupa_medianoche_en_fecha_local_peru(self):
        hoy = timezone.localdate()
        dia_entrega = 15
        fecha_local = timezone.make_aware(
            datetime(hoy.year, hoy.month, dia_entrega, 23, 0),
            timezone.get_default_timezone()
        )
        cliente = Cliente.objects.create(
            nombre='Cliente Medianoche',
            telefono='999111998',
            direccion='Av. Noche 1'
        )
        Pedido.objects.create(
            cliente=cliente,
            cantidad_bidones=5,
            precio_unitario=Decimal('7.00'),
            total=Decimal('35.00'),
            estado=Pedido.ENTREGADO,
            fecha_entrega=fecha_local
        )
        Pedido.objects.create(
            cliente=cliente,
            cantidad_bidones=1,
            precio_unitario=Decimal('7.00'),
            total=Decimal('7.00'),
            estado=Pedido.ENTREGADO,
            fecha_entrega=fecha_local + timedelta(hours=12)
        )
        self.client.force_login(self.secretaria)

        response = self.client.get(
            reverse('reporte_mensual'),
            {'mes': str(hoy.month), 'anio': str(hoy.year)}
        )
        detalle_por_dia = {
            dia['dia']: dia['ingresos']
            for dia in response.context['detalle_diario_mes']
        }

        self.assertEqual(
            response.context['dia_mas_ventas']['fecha_larga'].day,
            dia_entrega
        )
        self.assertEqual(detalle_por_dia[dia_entrega], Decimal('35.00'))
        self.assertEqual(detalle_por_dia[dia_entrega + 1], Decimal('7.00'))


class ProductionSettingsTests(SimpleTestCase):
    def test_timezone_operativa_es_lima_con_fechas_aware(self):
        self.assertEqual(settings.TIME_ZONE, 'America/Lima')
        self.assertTrue(settings.USE_TZ)
        self.assertEqual(str(timezone.get_default_timezone()), 'America/Lima')

    def test_debug_es_false_por_defecto(self):
        env = os.environ.copy()
        env.pop('DEBUG', None)
        env['SECRET_KEY'] = 'test-key'
        env['DJANGO_ENV'] = 'development'
        env.pop('RENDER_EXTERNAL_HOSTNAME', None)

        result = subprocess.run(
            [sys.executable, '-c', 'import water_system.settings as s; print(s.DEBUG)'],
            cwd=os.getcwd(),
            env=env,
            capture_output=True,
            text=True,
            check=False
        )

        self.assertEqual(result.returncode, 0)
        self.assertIn('False', result.stdout)

    def test_produccion_rechaza_secret_key_ausente(self):
        env = os.environ.copy()
        env.pop('SECRET_KEY', None)
        env['DJANGO_ENV'] = 'production'

        result = subprocess.run(
            [sys.executable, '-c', 'import water_system.settings'],
            cwd=os.getcwd(),
            env=env,
            capture_output=True,
            text=True,
            check=False
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn('SECRET_KEY es obligatoria', result.stderr)
