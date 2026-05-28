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
        'webpush/public-key/',
        views.webpush_public_key,
        name='webpush_public_key'
    ),

    path(
        'webpush/subscribe/',
        views.webpush_subscribe,
        name='webpush_subscribe'
    ),

    path(
        'webpush/unsubscribe/',
        views.webpush_unsubscribe,
        name='webpush_unsubscribe'
    ),

    path(
        'fcm/register-token/',
        views.fcm_register_token,
        name='fcm_register_token'
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
        'dashboard/fragmento/',
        views.dashboard,
        {'template_name': 'core/includes/dashboard_admin_fragmento.html'},
        name='dashboard_fragmento'
    ),

    path(
        'clientes/',
        views.lista_clientes,
        name='lista_clientes'
    ),

    path(
        'clientes/fragmento/',
        views.lista_clientes,
        {'template_name': 'core/includes/clientes_lista_fragmento.html'},
        name='clientes_fragmento'
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
        'pedidos/fragmento/',
        views.lista_pedidos,
        {'template_name': 'core/includes/pedidos_lista_fragmento.html'},
        name='pedidos_fragmento'
    ),

    path(
        'pedidos/repartidor/fragmento/',
        views.pedidos_repartidor,
        {'template_name': 'core/includes/pedidos_repartidor_fragmento.html'},
        name='pedidos_repartidor_fragmento'
    ),

    path(
        'reporte-diario/',
        views.reporte_diario,
        name='reporte_diario'
    ),

    path(
        'reporte-semanal/',
        views.reporte_semanal,
        name='reporte_semanal'
    ),

    path(
        'egresos/',
        views.egresos,
        name='egresos'
    ),

    path(
        'cierre-caja/diario/',
        views.registrar_cierre_caja_diario,
        name='registrar_cierre_caja_diario'
    ),

    path(
        'pagos/',
        views.pagos,
        name='pagos'
    ),

    path(
        'pagos/fiado/<int:pedido_id>/registrar/',
        views.marcar_fiado_pagado,
        name='marcar_fiado_pagado'
    ),

    path(
        'lugares/',
        views.lugares,
        name='lugares'
    ),

    path(
        'lugares/<int:lugar_id>/editar/',
        views.editar_lugar,
        name='editar_lugar'
    ),

    path(
        'lugares/<int:lugar_id>/estado/',
        views.cambiar_estado_lugar,
        name='cambiar_estado_lugar'
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
        'pedidos/repartidor/<int:pedido_id>/en-ruta/',
        views.marcar_pedido_en_ruta_repartidor,
        name='marcar_pedido_en_ruta_repartidor'
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
        'repartidor/clientes/<int:cliente_id>/referencia/',
        views.actualizar_referencia_cliente_repartidor,
        name='actualizar_referencia_cliente_repartidor'
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
