from django.contrib import admin
from .models import Cliente, Pedido


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
