from django.contrib import admin
from .models import Cliente, FCMToken, Pedido, PedidoHistorial, PushSubscription


@admin.register(Cliente)
class ClienteAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'telefono', 'direccion', 'estado', 'activo', 'fecha_registro')
    search_fields = ('nombre', 'telefono', 'direccion')
    list_filter = ('estado', 'activo')
    ordering = ('nombre',)


@admin.register(Pedido)
class PedidoAdmin(admin.ModelAdmin):
    list_display = ('id', 'cliente', 'repartidor', 'fecha_pedido', 'cantidad_bidones', 'precio_unitario', 'total', 'estado')
    search_fields = ('cliente__nombre', 'repartidor__username')
    list_filter = ('estado', 'repartidor', 'fecha_pedido')
    ordering = ('-fecha_pedido',)


@admin.register(PedidoHistorial)
class PedidoHistorialAdmin(admin.ModelAdmin):
    list_display = ('pedido', 'tipo_accion', 'usuario', 'fecha')
    search_fields = ('pedido__cliente__nombre', 'usuario__username', 'descripcion')
    list_filter = ('tipo_accion', 'fecha')
    ordering = ('-fecha', '-id')


@admin.register(PushSubscription)
class PushSubscriptionAdmin(admin.ModelAdmin):
    list_display = (
        'user',
        'endpoint',
        'is_active',
        'created_at',
        'updated_at',
        'last_error',
    )
    search_fields = ('user__username', 'endpoint')
    list_filter = ('is_active', 'created_at', 'updated_at')
    ordering = ('-updated_at', '-id')


@admin.register(FCMToken)
class FCMTokenAdmin(admin.ModelAdmin):
    list_display = (
        'user',
        'platform',
        'is_active',
        'device_id',
        'created_at',
        'updated_at',
        'last_error',
    )
    search_fields = ('user__username', 'token', 'device_id')
    list_filter = ('platform', 'is_active', 'created_at', 'updated_at')
    ordering = ('-updated_at', '-id')
