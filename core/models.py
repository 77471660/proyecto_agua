from django.db import models
from django.conf import settings


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

    latitud = models.FloatField(blank=True, null=True)
    longitud = models.FloatField(blank=True, null=True)

    estado = models.CharField(
        max_length=20,
        choices=ESTADOS,
        default='SIN_HISTORIAL'
    )

    fecha_registro = models.DateTimeField(auto_now_add=True)

    activo = models.BooleanField(default=True)

    def __str__(self):
        return self.nombre


class Pedido(models.Model):

    PENDIENTE = 'PENDIENTE'
    ENTREGADO = 'ENTREGADO'
    CANCELADO = 'CANCELADO'

    ESTADOS_PEDIDO = [
        (PENDIENTE, 'Pendiente'),
        (ENTREGADO, 'Entregado'),
        (CANCELADO, 'Cancelado'),
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

    fecha_pedido = models.DateTimeField(auto_now_add=True)
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

    cantidad_bidones = models.IntegerField()

    precio_unitario = models.DecimalField(
        max_digits=6,
        decimal_places=2
    )

    total = models.DecimalField(
        max_digits=8,
        decimal_places=2
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


class PedidoHistorial(models.Model):

    CREADO = 'creado'
    EDITADO = 'editado'
    ENTREGADO = 'entregado'
    CANCELADO = 'cancelado'
    REASIGNADO = 'reasignado'
    REVERTIDO = 'revertido'

    TIPOS_ACCION = [
        (CREADO, 'Creado'),
        (EDITADO, 'Editado'),
        (ENTREGADO, 'Entregado'),
        (CANCELADO, 'Cancelado'),
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
