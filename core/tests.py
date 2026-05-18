from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import Cliente, Pedido, PedidoHistorial
from .views import repartidores_disponibles


class PermisosRolesTests(TestCase):
    def setUp(self):
        User = get_user_model()

        self.grupo_secretaria, _ = Group.objects.get_or_create(name='Secretaria')
        self.grupo_repartidor, _ = Group.objects.get_or_create(name='Repartidor')
        self.grupo_jefe_repartidores, _ = Group.objects.get_or_create(
            name='JefeRepartidores'
        )
        self.grupo_administrador, _ = Group.objects.get_or_create(
            name='Administrador'
        )

        self.secretaria = User.objects.create_user(
            username='ADMINISTRADOR',
            password='clave-secreta'
        )
        self.secretaria.groups.add(self.grupo_secretaria)

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
        self.assertContains(response, 'Pedidos para hoy / atrasados')
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
                valor_anterior=Pedido.PENDIENTE,
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
