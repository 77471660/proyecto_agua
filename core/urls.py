from django.urls import path
from . import views


urlpatterns = [

    path(
        'manifest.json',
        views.pwa_manifest,
        name='pwa_manifest'
    ),

    path(
        'service-worker.js',
        views.service_worker,
        name='service_worker'
    ),

    path(
        'login/',
        views.login_usuario,
        name='login'
    ),

    path(
        'logout/',
        views.cerrar_sesion_usuario,
        name='logout'
    ),

    path(
        '',
        views.dashboard,
        name='dashboard'
    ),

    path(
        'clientes/',
        views.lista_clientes,
        name='lista_clientes'
    ),

    path(
        'clientes/buscar/',
        views.buscar_clientes,
        name='buscar_clientes'
    ),

    path(
        'cliente/<int:cliente_id>/',
        views.detalle_cliente,
        name='detalle_cliente'
    ),

    path(
        'cliente/<int:cliente_id>/editar/',
        views.editar_cliente,
        name='editar_cliente'
    ),

    path(
        'cliente/<int:cliente_id>/eliminar/',
        views.eliminar_cliente,
        name='eliminar_cliente'
    ),

    path(
        'registrar-cliente/',
        views.registrar_cliente,
        name='registrar_cliente'
    ),

    path(
        'registrar-pedido/',
        views.registrar_pedido,
        name='registrar_pedido'
    ),

    path(
        'pedido/<int:pedido_id>/editar/',
        views.editar_pedido,
        name='editar_pedido'
    ),

    path(
        'pedidos/',
        views.lista_pedidos,
        name='lista_pedidos'
    ),

    path(
        'reporte-diario/',
        views.reporte_diario,
        name='reporte_diario'
    ),

    path(
        'pedidos/repartidor/',
        views.pedidos_repartidor,
        name='pedidos_repartidor'
    ),

    path(
        'pedidos/repartidor/<int:pedido_id>/entregado/',
        views.marcar_pedido_entregado_repartidor,
        name='marcar_pedido_entregado_repartidor'
    ),

    path(
        'pedidos/repartidor/<int:pedido_id>/cancelado/',
        views.cancelar_pedido_repartidor,
        name='cancelar_pedido_repartidor'
    ),

    path(
        'repartidor/pedidos/nuevo/',
        views.nuevo_pedido_repartidor,
        name='nuevo_pedido_repartidor'
    ),

    path(
        'repartidor/clientes/nuevo/',
        views.nuevo_cliente_repartidor,
        name='nuevo_cliente_repartidor'
    ),

    path(
        'repartidor/logout/',
        views.cerrar_sesion_repartidor,
        name='cerrar_sesion_repartidor'
    ),

    path(
        'repartidor/jefe/',
        views.panel_jefe_repartidores,
        name='panel_jefe_repartidores'
    ),

    path(
        'repartidor/jefe/pedido/<int:pedido_id>/asignar/',
        views.asignar_pedido_repartidor,
        name='asignar_pedido_repartidor'
    ),

    path(
        'pedido/<int:pedido_id>/estado/<str:nuevo_estado>/',
        views.cambiar_estado_pedido,
        name='cambiar_estado_pedido'
    ),

    path(
        'pedido/<int:pedido_id>/revertir-entrega/',
        views.revertir_entrega,
        name='revertir_entrega'
    ),

    path(
        'reporte-mensual/',
        views.reporte_mensual,
        name='reporte_mensual'
    ),

]
