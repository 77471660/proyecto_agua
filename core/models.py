from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone
import logging
import re
from urllib.parse import quote_plus

logger = logging.getLogger(__name__)


class Lugar(models.Model):

    nombre = models.CharField(max_length=100, unique=True)
    activo = models.BooleanField(default=True)
    orden = models.PositiveIntegerField(default=0)
    fecha_creacion = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('orden', 'nombre')

    def __str__(self):
        return self.nombre


class Cliente(models.Model):

    ESTADOS = [
        ('ACTIVO', 'Activo'),
        ('RIESGO', 'En riesgo'),
        ('ABANDONADO', 'Abandonado'),
        ('SIN_HISTORIAL', 'Sin historial'),
    ]

    nombre = models.CharField(max_length=100)
    telefono = models.CharField(max_length=20)
    direccion = models.CharField(max_length=255)
    referencia = models.CharField(max_length=255, blank=True, null=True)
    lugar = models.ForeignKey(
        Lugar,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='clientes'
    )

    latitud = models.DecimalField(
        max_digits=9,
        decimal_places=6,
        blank=True,
        null=True,
        validators=[
            MinValueValidator(-90, message='La latitud debe estar entre -90 y 90.'),
            MaxValueValidator(90, message='La latitud debe estar entre -90 y 90.'),
        ]
    )
    longitud = models.DecimalField(
        max_digits=9,
        decimal_places=6,
        blank=True,
        null=True,
        validators=[
            MinValueValidator(-180, message='La longitud debe estar entre -180 y 180.'),
            MaxValueValidator(180, message='La longitud debe estar entre -180 y 180.'),
        ]
    )
    referencia_ubicacion = models.TextField(blank=True, null=True)
    foto_referencia_url = models.URLField(blank=True, null=True)
    foto_referencia_public_id = models.CharField(
        max_length=255,
        blank=True,
        null=True
    )
    foto_referencia_actualizada_en = models.DateTimeField(
        null=True,
        blank=True
    )
    foto_referencia_actualizada_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='clientes_foto_referencia_actualizadas'
    )

    estado = models.CharField(
        max_length=20,
        choices=ESTADOS,
        default='SIN_HISTORIAL'
    )

    fecha_registro = models.DateTimeField(auto_now_add=True)

    activo = models.BooleanField(default=True)

    def __str__(self):
        return self.nombre

    def clean(self):
        super().clean()

        if (self.latitud is None) != (self.longitud is None):
            logger.warning(
                'Coordenadas incompletas para cliente. cliente_id=%s latitud=%s longitud=%s',
                self.pk,
                self.latitud,
                self.longitud
            )
            raise ValidationError({
                'latitud': 'Latitud y longitud deben registrarse juntas.',
                'longitud': 'Latitud y longitud deben registrarse juntas.',
            })

    def tiene_coordenadas(self):
        return self.latitud is not None and self.longitud is not None

    @property
    def direccion_normalizada(self):
        return re.sub(r'\s+', ' ', (self.direccion or '').strip())

    @property
    def maps_label(self):
        if self.tiene_coordenadas():
            return 'Coordenadas GPS'

        if self.direccion_normalizada:
            return 'Direcci\u00f3n textual'

        return 'Sin ubicaci\u00f3n'

    def obtener_ubicacion_copiable(self):
        if self.tiene_coordenadas():
            return f'{self.latitud},{self.longitud}'

        return self.direccion_normalizada

    def obtener_maps_url(self):
        try:
            query = self.obtener_ubicacion_copiable()

            if not query:
                return ''

            return (
                'https://www.google.com/maps/search/?api=1&query='
                f'{quote_plus(query, safe=",")}'
            )
        except Exception:
            logger.exception(
                'No se pudo generar URL de Google Maps. cliente_id=%s',
                self.pk
            )

        return ''


class Pedido(models.Model):

    PENDIENTE = 'PENDIENTE'
    ASIGNADO = 'ASIGNADO'
    EN_RUTA = 'EN_RUTA'
    ENTREGADO = 'ENTREGADO'
    CANCELADO = 'CANCELADO'
    REPROGRAMADO = 'REPROGRAMADO'

    PAGO_PENDIENTE = 'PENDIENTE'
    PAGO_EFECTIVO = 'EFECTIVO'
    PAGO_YAPE = 'YAPE'
    PAGO_PLIN = 'PLIN'
    PAGO_TRANSFERENCIA = 'TRANSFERENCIA'
    PAGO_FIADO = 'FIADO'

    ESTADOS_PEDIDO = [
        (PENDIENTE, 'Pendiente'),
        (ASIGNADO, 'Asignado'),
        (EN_RUTA, 'En ruta'),
        (ENTREGADO, 'Entregado'),
        (CANCELADO, 'Cancelado'),
        (REPROGRAMADO, 'Reprogramado'),
    ]

    ESTADOS_ACTIVOS = [
        PENDIENTE,
        ASIGNADO,
        EN_RUTA,
        REPROGRAMADO,
    ]

    METODOS_PAGO = [
        (PAGO_PENDIENTE, 'Pendiente'),
        (PAGO_EFECTIVO, 'Efectivo'),
        (PAGO_YAPE, 'Yape'),
        (PAGO_PLIN, 'Plin'),
        (PAGO_TRANSFERENCIA, 'Transferencia'),
        (PAGO_FIADO, 'Fiado'),
    ]

    METODOS_COBRO = [
        (PAGO_EFECTIVO, 'Efectivo'),
        (PAGO_YAPE, 'Yape'),
        (PAGO_PLIN, 'Plin'),
        (PAGO_TRANSFERENCIA, 'Transferencia'),
    ]

    cliente = models.ForeignKey(
        Cliente,
        on_delete=models.CASCADE
    )

    repartidor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL
    )

    lugar = models.ForeignKey(
        Lugar,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='pedidos'
    )

    fecha_pedido = models.DateTimeField(auto_now_add=True)
    fecha_pendiente = models.DateTimeField(
        null=True,
        blank=True
    )
    usuario_pendiente = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='pedidos_pendientes_registrados'
    )
    fecha_asignado = models.DateTimeField(
        null=True,
        blank=True
    )
    usuario_asignado = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='pedidos_asignados_estado'
    )
    fecha_en_ruta = models.DateTimeField(
        null=True,
        blank=True
    )
    usuario_en_ruta = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='pedidos_en_ruta_estado'
    )
    fecha_programada = models.DateField(
    null=True,
    blank=True
    )
    fecha_entrega = models.DateTimeField(
        null=True,
        blank=True
    )
    fecha_cancelacion = models.DateTimeField(
        null=True,
        blank=True
    )
    usuario_entrega = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='pedidos_entregados_estado'
    )
    usuario_cancelacion = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='pedidos_cancelados_estado'
    )
    fecha_reprogramado = models.DateTimeField(
        null=True,
        blank=True
    )
    usuario_reprogramado = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='pedidos_reprogramados_estado'
    )
    fecha_estado_actualizado = models.DateTimeField(
        null=True,
        blank=True
    )
    usuario_estado_actualizado = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='pedidos_estado_actualizado'
    )

    cantidad_bidones = models.IntegerField()

    precio_unitario = models.DecimalField(
        max_digits=6,
        decimal_places=2
    )

    total = models.DecimalField(
        max_digits=8,
        decimal_places=2
    )

    metodo_pago = models.CharField(
        max_length=20,
        choices=METODOS_PAGO,
        default=PAGO_PENDIENTE
    )

    fecha_pago = models.DateTimeField(
        null=True,
        blank=True
    )

    metodo_pago_final = models.CharField(
        max_length=20,
        choices=METODOS_COBRO,
        null=True,
        blank=True
    )

    usuario_pago = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='pedidos_pago_registrado'
    )

    estado = models.CharField(
        max_length=20,
        choices=ESTADOS_PEDIDO,
        default=PENDIENTE
    )

    observacion = models.TextField(
        blank=True,
        null=True
    )

    def __str__(self):
        return f"Pedido #{self.id} - {self.cliente.nombre}"

    def esta_activo(self):
        return self.estado in self.ESTADOS_ACTIVOS

    def registrar_estado(self, nuevo_estado, usuario=None, momento=None):
        momento = momento or timezone.now()
        self.estado = nuevo_estado
        self.fecha_estado_actualizado = momento
        self.usuario_estado_actualizado = (
            usuario if usuario and getattr(usuario, 'is_authenticated', False) else None
        )

        if nuevo_estado == self.PENDIENTE:
            self.fecha_pendiente = momento
            self.usuario_pendiente = self.usuario_estado_actualizado
        elif nuevo_estado == self.ASIGNADO:
            self.fecha_asignado = momento
            self.usuario_asignado = self.usuario_estado_actualizado
        elif nuevo_estado == self.EN_RUTA:
            self.fecha_en_ruta = momento
            self.usuario_en_ruta = self.usuario_estado_actualizado
        elif nuevo_estado == self.ENTREGADO:
            self.fecha_entrega = momento
            self.usuario_entrega = self.usuario_estado_actualizado
        elif nuevo_estado == self.CANCELADO:
            self.fecha_cancelacion = momento
            self.usuario_cancelacion = self.usuario_estado_actualizado
        elif nuevo_estado == self.REPROGRAMADO:
            self.fecha_reprogramado = momento
            self.usuario_reprogramado = self.usuario_estado_actualizado

    @property
    def badge_estado_clase(self):
        return {
            self.PENDIENTE: 'badge-pendiente',
            self.ASIGNADO: 'badge-asignado',
            self.EN_RUTA: 'badge-en-ruta',
            self.ENTREGADO: 'badge-entregado',
            self.CANCELADO: 'badge-cancelado',
            self.REPROGRAMADO: 'badge-reprogramado',
        }.get(self.estado, 'badge-muted')


class PedidoHistorial(models.Model):

    CREADO = 'creado'
    EDITADO = 'editado'
    ASIGNADO = 'asignado'
    EN_RUTA = 'en_ruta'
    ENTREGADO = 'entregado'
    CANCELADO = 'cancelado'
    REPROGRAMADO = 'reprogramado'
    REASIGNADO = 'reasignado'
    REVERTIDO = 'revertido'

    TIPOS_ACCION = [
        (CREADO, 'Creado'),
        (EDITADO, 'Editado'),
        (ASIGNADO, 'Asignado'),
        (EN_RUTA, 'En ruta'),
        (ENTREGADO, 'Entregado'),
        (CANCELADO, 'Cancelado'),
        (REPROGRAMADO, 'Reprogramado'),
        (REASIGNADO, 'Reasignado'),
        (REVERTIDO, 'Revertido'),
    ]

    pedido = models.ForeignKey(
        Pedido,
        on_delete=models.CASCADE,
        related_name='historial'
    )
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL
    )
    fecha = models.DateTimeField(auto_now_add=True)
    tipo_accion = models.CharField(
        max_length=20,
        choices=TIPOS_ACCION
    )
    descripcion = models.TextField()
    valor_anterior = models.TextField(blank=True)
    valor_nuevo = models.TextField(blank=True)

    class Meta:
        ordering = ['-fecha', '-id']

    def __str__(self):
        return f"{self.get_tipo_accion_display()} - Pedido #{self.pedido_id}"


class PushSubscription(models.Model):

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='push_subscriptions'
    )
    endpoint = models.URLField(unique=True, max_length=500)
    p256dh = models.TextField()
    auth = models.TextField()
    is_active = models.BooleanField(default=True)
    last_error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at', '-id']

    def __str__(self):
        return f"Push subscription {self.id} - {self.user}"


class FCMToken(models.Model):

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='fcm_tokens'
    )
    token = models.TextField(unique=True)
    platform = models.CharField(max_length=30, default='android')
    device_id = models.CharField(max_length=120, blank=True)
    is_active = models.BooleanField(default=True)
    last_error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at', '-id']

    def __str__(self):
        return f"FCM token {self.id} - {self.user}"
