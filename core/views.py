from django.http import FileResponse, JsonResponse
from django.conf import settings
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth import get_user_model, login, logout
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.models import Group
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import (
    Sum,
    Count,
    Q,
    Max,
    Case,
    When,
    Value,
    IntegerField,
    DateField,
    DateTimeField,
    F,
)
from django.utils import timezone
from django.views.decorators.http import require_POST
from calendar import monthrange
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from urllib.parse import quote
import json
import logging
import re

from .auth_utils import (
    es_repartidor,
    es_secretaria,
    es_jefe_repartidores,
    jefe_repartidores_required,
    clientes_required,
    puede_ver_crm_operativo,
    puede_ver_panel_repartidor,
    repartidor_required,
    secretaria_required,
    REPARTIDOR_GROUP_NAMES,
    JEFE_REPARTIDORES_GROUP,
)
from .models import Cliente, Pedido, PedidoHistorial, PushSubscription
from .push_notifications import (
    endpoint_for_log,
    send_order_assignment_push,
    vapid_status,
)


PEDIDOS_POR_PAGINA = 15
CLIENTES_POR_PAGINA = 15
PANEL_JEFE_LIMITE_PEDIDOS = 15
HISTORIAL_CLIENTE_LIMITE = 10
logger = logging.getLogger(__name__)


def nombre_usuario_historial(usuario):

    if not usuario:
        return 'Sistema'

    return usuario.get_full_name() or usuario.username


def nombre_repartidor_historial(repartidor):

    if not repartidor:
        return 'Sin asignar'

    return repartidor.get_full_name() or repartidor.username


def formato_fecha_historial(valor):

    if not valor:
        return 'Sin fecha'

    return valor.strftime('%d/%m/%Y')


def resumen_pedido_historial(pedido):

    return (
        f"{pedido.cantidad_bidones} bidones, "
        f"S/ {pedido.total}, "
        f"estado {pedido.estado}, "
        f"programado {formato_fecha_historial(pedido.fecha_programada)}, "
        f"repartidor {nombre_repartidor_historial(pedido.repartidor)}"
    )


def registrar_historial_pedido(
    pedido,
    usuario,
    tipo_accion,
    descripcion,
    valor_anterior='',
    valor_nuevo=''
):

    PedidoHistorial.objects.create(
        pedido=pedido,
        usuario=usuario if usuario and usuario.is_authenticated else None,
        tipo_accion=tipo_accion,
        descripcion=descripcion,
        valor_anterior=valor_anterior or '',
        valor_nuevo=valor_nuevo or ''
    )


def cambios_edicion_pedido(pedido, nuevos_valores):

    campos = [
        ('cantidad_bidones', 'Cantidad'),
        ('precio_unitario', 'Precio unitario'),
        ('total', 'Total'),
        ('fecha_programada', 'Fecha programada'),
        ('observacion', 'Observación'),
    ]
    cambios = []

    for campo, etiqueta in campos:
        anterior = getattr(pedido, campo)
        nuevo = nuevos_valores[campo]

        if anterior != nuevo:
            if campo == 'fecha_programada':
                anterior_texto = formato_fecha_historial(anterior)
                nuevo_texto = formato_fecha_historial(nuevo)
            else:
                anterior_texto = str(anterior or '')
                nuevo_texto = str(nuevo or '')

            cambios.append({
                'etiqueta': etiqueta,
                'anterior': anterior_texto,
                'nuevo': nuevo_texto,
            })

    return cambios


def pwa_manifest(request):

    manifest_path = settings.BASE_DIR / 'static' / 'manifest.json'
    response = FileResponse(
        manifest_path.open('rb'),
        content_type='application/manifest+json'
    )
    response['Cache-Control'] = 'no-cache'

    return response


def service_worker(request):

    service_worker_path = settings.BASE_DIR / 'static' / 'service-worker.js'
    response = FileResponse(
        service_worker_path.open('rb'),
        content_type='application/javascript'
    )
    response['Cache-Control'] = 'no-cache'

    return response


@login_required
def webpush_public_key(request):

    if not puede_ver_panel_repartidor(request.user):
        logger.warning(
            'Web Push public-key rechazado. usuario=%s status_vapid=%s',
            request.user.id,
            vapid_status()
        )
        return JsonResponse({'error': 'No autorizado.'}, status=403)

    logger.info(
        'Web Push public-key solicitado. usuario=%s status_vapid=%s',
        request.user.id,
        vapid_status()
    )

    return JsonResponse({
        'publicKey': settings.WEBPUSH_VAPID_PUBLIC_KEY,
    })


@login_required
@require_POST
def webpush_subscribe(request):

    if not puede_ver_panel_repartidor(request.user):
        logger.warning(
            'Web Push subscribe rechazado. usuario=%s',
            request.user.id
        )
        return JsonResponse({'error': 'No autorizado.'}, status=403)

    try:
        data = json.loads(request.body.decode('utf-8'))
    except (json.JSONDecodeError, UnicodeDecodeError):
        logger.warning(
            'Web Push subscribe con JSON invalido. usuario=%s',
            request.user.id
        )
        return JsonResponse({'error': 'JSON invalido.'}, status=400)

    endpoint = data.get('endpoint', '').strip()
    keys = data.get('keys') or {}
    p256dh = keys.get('p256dh', '').strip()
    auth = keys.get('auth', '').strip()

    if not endpoint or not p256dh or not auth:
        logger.warning(
            'Web Push subscribe incompleto. usuario=%s endpoint=%s '
            'tiene_p256dh=%s tiene_auth=%s',
            request.user.id,
            endpoint_for_log(endpoint),
            bool(p256dh),
            bool(auth)
        )
        return JsonResponse({'error': 'Suscripcion incompleta.'}, status=400)

    subscription, created = PushSubscription.objects.update_or_create(
        endpoint=endpoint,
        defaults={
            'user': request.user,
            'p256dh': p256dh,
            'auth': auth,
            'is_active': True,
            'last_error': '',
        }
    )
    active_count = PushSubscription.objects.filter(
        user=request.user,
        is_active=True
    ).count()

    logger.info(
        'PushSubscription guardada usuario=%s',
        request.user.id
    )
    logger.info(
        'Web Push subscribe guardado. usuario=%s subscription_id=%s '
        'created=%s endpoint=%s suscripciones_activas_usuario=%s',
        request.user.id,
        subscription.id,
        created,
        endpoint_for_log(endpoint),
        active_count
    )

    return JsonResponse({'ok': True})


@login_required
@require_POST
def webpush_unsubscribe(request):

    if not puede_ver_panel_repartidor(request.user):
        logger.warning(
            'Web Push unsubscribe rechazado. usuario=%s',
            request.user.id
        )
        return JsonResponse({'error': 'No autorizado.'}, status=403)

    try:
        data = json.loads(request.body.decode('utf-8'))
    except (json.JSONDecodeError, UnicodeDecodeError):
        logger.warning(
            'Web Push unsubscribe con JSON invalido. usuario=%s',
            request.user.id
        )
        return JsonResponse({'error': 'JSON invalido.'}, status=400)

    endpoint = data.get('endpoint', '').strip()

    if not endpoint:
        logger.warning(
            'Web Push unsubscribe sin endpoint. usuario=%s',
            request.user.id
        )
        return JsonResponse({'error': 'Endpoint requerido.'}, status=400)

    updated = PushSubscription.objects.filter(
        user=request.user,
        endpoint=endpoint
    ).update(is_active=False)
    logger.info(
        'Web Push unsubscribe procesado. usuario=%s endpoint=%s '
        'suscripciones_desactivadas=%s',
        request.user.id,
        endpoint_for_log(endpoint),
        updated
    )

    return JsonResponse({'ok': True})


def paginar_queryset(request, queryset, por_pagina, page_param='page'):

    paginator = Paginator(queryset, por_pagina)
    page_obj = paginator.get_page(request.GET.get(page_param))
    query_params = request.GET.copy()

    query_params.pop(page_param, None)

    return page_obj, query_params.urlencode()


def destino_usuario(user):

    if user.is_superuser:
        return '/'

    if es_jefe_repartidores(user):
        return '/repartidor/jefe/'

    if es_repartidor(user):
        return '/pedidos/repartidor/'

    if es_secretaria(user):
        return '/'

    return '/'


def login_usuario(request):

    if request.user.is_authenticated:
        return redirect(destino_usuario(request.user))

    if request.method == 'POST':
        form = AuthenticationForm(
            request,
            data=request.POST
        )

        if form.is_valid():
            user = form.get_user()
            login(request, user)
            return redirect(destino_usuario(user))
    else:
        form = AuthenticationForm(request)

    return render(
        request,
        'core/login.html',
        {
            'form': form,
        }
    )


def estados_activos_repartidor():

    estados = [Pedido.PENDIENTE]
    estados_modelo = [
        valor
        for valor, _etiqueta in Pedido.ESTADOS_PEDIDO
    ]

    if 'URGENTE' in estados_modelo:
        estados.append('URGENTE')

    return estados


def limpiar_telefono(telefono):

    return re.sub(r'\D', '', telefono or '')


def telefono_whatsapp_peru(telefono):

    numero = limpiar_telefono(telefono)

    if numero.startswith('51') and len(numero) > 9:
        return numero

    return f'51{numero}'


def tiempo_esperando_pedido(pedido):

    diferencia = timezone.now() - pedido.fecha_pedido
    dias = diferencia.days
    horas = diferencia.seconds // 3600
    minutos = max(1, diferencia.seconds // 60)

    if dias == 1:
        return 'Hace 1 día'

    if dias > 1:
        return f'Hace {dias} días'

    if horas == 1:
        return 'Hace 1 hora'

    if horas > 1:
        return f'Hace {horas} horas'

    if minutos == 1:
        return 'Hace 1 minuto'

    return f'Hace {minutos} minutos'


def tiempo_pedido_panel(pedido):

    return tiempo_esperando_pedido(pedido)


def texto_programacion_pedido(pedido):

    hoy = timezone.localdate()
    manana = hoy + timedelta(days=1)

    if not pedido.fecha_programada:
        return 'Hoy'

    if pedido.fecha_programada < hoy:
        return 'Atrasado'

    if pedido.fecha_programada == hoy:
        return 'Hoy'

    if pedido.fecha_programada == manana:
        return 'Mañana'

    return pedido.fecha_programada.strftime('%d/%m/%Y')


def clase_badge_programacion_pedido(pedido):

    hoy = timezone.localdate()
    manana = hoy + timedelta(days=1)

    if not pedido.fecha_programada or pedido.fecha_programada == hoy:
        return 'badge-hoy'

    if pedido.fecha_programada < hoy:
        return 'badge-atrasado'

    if pedido.fecha_programada == manana:
        return 'badge-manana'

    return 'badge-muted'


def clase_badge_tiempo_panel(pedido):

    clase_espera = clase_tiempo_esperando_pedido(pedido)

    if pedido.fecha_programada and pedido.fecha_programada < timezone.localdate():
        return 'badge-atrasado'

    if clase_espera in ['waiting-critical', 'waiting-danger']:
        return 'badge-atrasado'

    if clase_espera == 'waiting-warning':
        return 'badge-pendiente'

    return 'badge-hoy'


def preparar_pedido_panel_jefe(pedido):

    pedido.tiempo_panel = tiempo_pedido_panel(pedido)
    pedido.badge_tiempo_panel = clase_badge_tiempo_panel(pedido)
    pedido.programacion_panel = texto_programacion_pedido(pedido)
    pedido.badge_programacion_panel = clase_badge_programacion_pedido(pedido)

    return pedido


def preparar_pedido_lista(pedido):

    hoy = timezone.localdate()
    manana = hoy + timedelta(days=1)

    pedido.programacion_texto = ''
    pedido.programacion_clase = 'badge-muted'
    pedido.fila_prioridad_clase = ''

    if pedido.estado == Pedido.PENDIENTE:
        pedido.programacion_texto = texto_programacion_pedido(pedido)
        pedido.programacion_clase = clase_badge_programacion_pedido(pedido)

        if pedido.fecha_programada and pedido.fecha_programada < hoy:
            pedido.fila_prioridad_clase = 'pedido-row-atrasado'
        elif not pedido.fecha_programada or pedido.fecha_programada == hoy:
            pedido.fila_prioridad_clase = 'pedido-row-hoy'
        elif pedido.fecha_programada == manana:
            pedido.fila_prioridad_clase = 'pedido-row-manana'
        else:
            pedido.fila_prioridad_clase = 'pedido-row-futuro'

    return pedido


def clase_tiempo_esperando_pedido(pedido):

    diferencia = timezone.now() - pedido.fecha_pedido
    minutos_totales = int(diferencia.total_seconds() // 60)

    if diferencia.days >= 1:
        return 'waiting-critical'

    if minutos_totales >= 60:
        return 'waiting-danger'

    if minutos_totales >= 30:
        return 'waiting-warning'

    return 'waiting-ok'


def preparar_pedido_repartidor(pedido):

    hoy = timezone.localdate()
    numero = telefono_whatsapp_peru(pedido.cliente.telefono)
    mensaje = (
        'Hola, soy de Eco Agua. Estoy en camino con su pedido de '
        f'{pedido.cantidad_bidones} bidones.'
    )

    pedido.whatsapp_url = (
        f'https://wa.me/{numero}?text={quote(mensaje)}'
    )
    pedido.telefono_limpio = limpiar_telefono(pedido.cliente.telefono)
    pedido.tiempo_esperando = tiempo_esperando_pedido(pedido)
    pedido.tiempo_esperando_clase = clase_tiempo_esperando_pedido(pedido)

    pedido.fecha_programada_texto = texto_programacion_pedido(pedido)
    pedido.fecha_programada_clase = clase_badge_programacion_pedido(pedido)
    pedido.es_atrasado = (
        pedido.fecha_programada
        and pedido.fecha_programada < hoy
    )

    pedidos_entregados_cliente = getattr(
        pedido,
        'cliente_entregados',
        None
    )

    if pedidos_entregados_cliente is None:
        pedidos_entregados_cliente = Pedido.objects.filter(
            cliente=pedido.cliente,
            estado=Pedido.ENTREGADO
        ).count()
    cliente_reciente = (
        timezone.now() - pedido.cliente.fecha_registro
    ).days < 7
    pedido.cliente_nuevo = (
        cliente_reciente
        or pedidos_entregados_cliente < 2
    )

    return pedido


def precio_unitario_rapido(cliente):

    return Decimal('7.00')


def repartidores_disponibles():

    User = get_user_model()

    grupos_reparto = Group.objects.filter(
        name__in=[
            *REPARTIDOR_GROUP_NAMES,
            JEFE_REPARTIDORES_GROUP,
        ]
    )

    if not grupos_reparto.exists():
        return User.objects.none()

    return User.objects.filter(
        is_active=True,
        groups__in=grupos_reparto
    ).distinct().order_by('username')


def puede_editar_pedido(user):

    return (
        user.is_authenticated
        and user.is_active
        and (
            puede_ver_crm_operativo(user)
            or es_jefe_repartidores(user)
        )
    )


def redireccion_edicion_pedido(origen, pedido):

    if origen == 'panel_jefe':
        return redirect('panel_jefe_repartidores')

    return redirect(
        'detalle_cliente',
        cliente_id=pedido.cliente_id
    )


def hora_panel(fecha):

    if fecha:
        return timezone.localtime(fecha).strftime('%H:%M')

    return '-'


def actualizar_estados_clientes():

    hoy = timezone.localdate()
    ahora = timezone.now()
    clientes = Cliente.objects.annotate(
        pedidos_pendientes_vencidos=Count(
            'pedido',
            filter=Q(
                pedido__estado=Pedido.PENDIENTE,
                pedido__fecha_programada__lte=hoy
            )
        ),
        total_entregados=Count(
            'pedido',
            filter=Q(pedido__estado=Pedido.ENTREGADO)
        ),
        ultimo_entregado=Max(
            'pedido__fecha_pedido',
            filter=Q(pedido__estado=Pedido.ENTREGADO)
        )
    )

    for cliente in clientes:

        estado_anterior = cliente.estado

        if cliente.pedidos_pendientes_vencidos:

            cliente.estado = 'RIESGO'

        elif cliente.total_entregados == 0:

            cliente.estado = 'SIN_HISTORIAL'

        else:

            if cliente.ultimo_entregado:

                dias_sin_comprar = (
                    ahora - cliente.ultimo_entregado
                ).days

                if dias_sin_comprar > 30:

                    cliente.estado = 'ABANDONADO'

                elif cliente.total_entregados >= 2:

                    cliente.estado = 'ACTIVO'

                else:

                    cliente.estado = 'RIESGO'

        if cliente.estado != estado_anterior:
            cliente.save(update_fields=['estado'])

@login_required
@secretaria_required
def dashboard(request):

    actualizar_estados_clientes()

    total_clientes = Cliente.objects.count()
    total_pedidos = Pedido.objects.count()

    pedidos_entregados = Pedido.objects.filter(
        estado=Pedido.ENTREGADO
    ).count()

    pedidos_pendientes = Pedido.objects.filter(
        estado=Pedido.PENDIENTE
    ).count()

    pedidos_cancelados = Pedido.objects.filter(
        estado=Pedido.CANCELADO
    ).count()

    clientes_activos = Cliente.objects.filter(
        estado='ACTIVO'
    ).count()

    clientes_riesgo = Cliente.objects.filter(
        estado='RIESGO'
    ).count()

    clientes_abandonados = Cliente.objects.filter(
        estado='ABANDONADO'
    ).count()

    clientes_sin_historial = Cliente.objects.filter(
        estado='SIN_HISTORIAL'
    ).count()

    ingresos_totales = Pedido.objects.filter(
        estado=Pedido.ENTREGADO
    ).aggregate(
        total_ingresos=Sum('total')
    )['total_ingresos'] or 0

    total_bidones_vendidos = Pedido.objects.filter(
        estado=Pedido.ENTREGADO
    ).aggregate(
        total_bidones=Sum('cantidad_bidones')
    )['total_bidones'] or 0

    ahora = timezone.now()
    hoy = timezone.localdate()
    limite_30_minutos = ahora - timedelta(minutes=30)
    limite_1_hora = ahora - timedelta(hours=1)
    limite_2_horas = ahora - timedelta(hours=2)

    pedidos_urgentes_queryset = Pedido.objects.filter(
        estado=Pedido.PENDIENTE,
        fecha_programada__lte=hoy
    ).select_related(
        'cliente'
    ).annotate(
        cliente_entregados=Count(
            'cliente__pedido',
            filter=Q(cliente__pedido__estado=Pedido.ENTREGADO)
        )
    ).order_by(
        'fecha_programada',
        'fecha_pedido'
    )
    total_pedidos_urgentes = pedidos_urgentes_queryset.count()
    pedidos_urgentes = pedidos_urgentes_queryset[:5]

    ventas_hoy = Pedido.objects.filter(
        estado=Pedido.ENTREGADO,
        fecha_pedido__date=hoy
    ).aggregate(
        total=Sum('total')
    )['total'] or 0

    pedidos_hoy = Pedido.objects.filter(
        fecha_pedido__date=hoy
    ).count()

    bidones_hoy = Pedido.objects.filter(
        estado=Pedido.ENTREGADO,
        fecha_pedido__date=hoy
    ).aggregate(
        total=Sum('cantidad_bidones')
    )['total'] or 0

    clientes_nuevos = Cliente.objects.filter(
        fecha_registro__date=hoy
    ).count()

    ayer = hoy - timedelta(days=1)
    antes_ayer = hoy - timedelta(days=2)
    comparativo_ventas = []

    for etiqueta, fecha in [
        ('Hoy', hoy),
        ('Ayer', ayer),
        ('Antes de ayer', antes_ayer),
    ]:
        pedidos_dia = Pedido.objects.filter(
            estado=Pedido.ENTREGADO,
            fecha_pedido__date=fecha
        )

        comparativo_ventas.append({
            'etiqueta': etiqueta,
            'fecha': fecha,
            'ingresos': pedidos_dia.aggregate(total=Sum('total'))['total'] or 0,
            'bidones': pedidos_dia.aggregate(total=Sum('cantidad_bidones'))['total'] or 0,
            'pedidos': pedidos_dia.count(),
        })

    ingresos_hoy = comparativo_ventas[0]['ingresos']
    ingresos_ayer = comparativo_ventas[1]['ingresos']

    if ingresos_hoy > ingresos_ayer:
        lectura_comparativo = 'Hoy vas mejor que ayer.'
    elif ingresos_hoy < ingresos_ayer:
        lectura_comparativo = 'Hoy vas por debajo de ayer.'
    else:
        lectura_comparativo = 'Hoy vas similar a ayer.'

    fecha_comparacion = None
    venta_fecha_comparacion = None
    lectura_fecha_comparacion = ''
    fecha_comparacion_param = request.GET.get('fecha_comparacion', '').strip()

    if fecha_comparacion_param:
        try:
            fecha_comparacion = datetime.strptime(
                fecha_comparacion_param,
                '%Y-%m-%d'
            ).date()
        except ValueError:
            fecha_comparacion = None

    if fecha_comparacion:
        pedidos_fecha_comparacion = Pedido.objects.filter(
            estado=Pedido.ENTREGADO,
            fecha_pedido__date=fecha_comparacion
        )

        venta_fecha_comparacion = {
            'etiqueta': 'Fecha seleccionada',
            'fecha': fecha_comparacion,
            'ingresos': pedidos_fecha_comparacion.aggregate(total=Sum('total'))['total'] or 0,
            'bidones': pedidos_fecha_comparacion.aggregate(total=Sum('cantidad_bidones'))['total'] or 0,
            'pedidos': pedidos_fecha_comparacion.count(),
        }

        ingresos_fecha_comparacion = venta_fecha_comparacion['ingresos']

        if ingresos_hoy > ingresos_fecha_comparacion:
            lectura_fecha_comparacion = 'Hoy vas mejor que la fecha seleccionada.'
        elif ingresos_hoy < ingresos_fecha_comparacion:
            lectura_fecha_comparacion = 'Hoy vas por debajo de la fecha seleccionada.'
        else:
            lectura_fecha_comparacion = 'Hoy vas similar a la fecha seleccionada.'

    inicio_mes = hoy.replace(day=1)

    ventas_mes = Pedido.objects.filter(
        estado=Pedido.ENTREGADO,
        fecha_pedido__date__gte=inicio_mes
    ).aggregate(
        total=Sum('total')
    )['total'] or 0

    recomendaciones = []

    if clientes_abandonados > 0:
        recomendaciones.append(
            'Se recomienda contactar clientes abandonados.'
        )

    if clientes_riesgo >= 3:
        recomendaciones.append(
            'Existen varios clientes en riesgo de abandono.'
        )

    if pedidos_pendientes >= 5:
        recomendaciones.append(
            'Hay muchos pedidos pendientes por atender.'
        )

    if ventas_hoy == 0:
        recomendaciones.append(
            'No se registran ventas entregadas hoy.'
        )

    if clientes_activos >= 5:
        recomendaciones.append(
            'La cartera de clientes frecuentes está creciendo.'
        )

    clientes_frecuentes = Cliente.objects.annotate(
        total_entregados=Count(
            'pedido',
            filter=Q(pedido__estado=Pedido.ENTREGADO)
        )
    ).filter(
        total_entregados__gt=0
    ).order_by('-total_entregados')[:5]

    context = {
        'total_clientes': total_clientes,
        'total_pedidos': total_pedidos,
        'pedidos_entregados': pedidos_entregados,
        'pedidos_pendientes': pedidos_pendientes,
        'pedidos_cancelados': pedidos_cancelados,
        'clientes_activos': clientes_activos,
        'clientes_riesgo': clientes_riesgo,
        'clientes_abandonados': clientes_abandonados,
        'clientes_sin_historial': clientes_sin_historial,
        'ingresos_totales': ingresos_totales,
        'total_bidones_vendidos': total_bidones_vendidos,
        'clientes_frecuentes': clientes_frecuentes,
        'recomendaciones': recomendaciones,
        'ventas_hoy': ventas_hoy,
        'pedidos_hoy': pedidos_hoy,
        'bidones_hoy': bidones_hoy,
        'clientes_nuevos': clientes_nuevos,
        'ventas_mes': ventas_mes,
        'comparativo_ventas': comparativo_ventas,
        'lectura_comparativo': lectura_comparativo,
        'fecha_comparacion': fecha_comparacion,
        'venta_fecha_comparacion': venta_fecha_comparacion,
        'lectura_fecha_comparacion': lectura_fecha_comparacion,
        'pedidos_urgentes': pedidos_urgentes,
        'total_pedidos_urgentes': total_pedidos_urgentes,
        'hay_mas_pedidos_urgentes': total_pedidos_urgentes > 5,
        'hoy': hoy,
        'limite_30_minutos': limite_30_minutos,
        'limite_1_hora': limite_1_hora,
        'limite_2_horas': limite_2_horas,
    }

    return render(request, 'core/dashboard.html', context)

@login_required
@clientes_required
def lista_clientes(request):

    busqueda = request.GET.get('q', '').strip()
    hoy = timezone.localdate()

    clientes = Cliente.objects.annotate(
        orden_operativo=Case(
            When(fecha_registro__date=hoy, then=Value(0)),
            When(estado='RIESGO', then=Value(1)),
            When(estado='SIN_HISTORIAL', then=Value(2)),
            When(estado='ACTIVO', then=Value(3)),
            default=Value(4),
            output_field=IntegerField()
        )
    ).order_by(
        'orden_operativo',
        'nombre'
    )

    if busqueda:
        clientes = clientes.filter(
            Q(nombre__icontains=busqueda) |
            Q(telefono__icontains=busqueda) |
            Q(direccion__icontains=busqueda)
        )

    page_obj, pagination_query = paginar_queryset(
        request,
        clientes,
        CLIENTES_POR_PAGINA
    )

    context = {
        'clientes': page_obj,
        'page_obj': page_obj,
        'pagination_query': pagination_query,
        'busqueda': busqueda,
        'hoy': hoy,
    }

    return render(request, 'core/clientes.html', context)


@login_required
def buscar_clientes(request):

    user = request.user

    if not (
        puede_ver_crm_operativo(user)
        or puede_ver_panel_repartidor(user)
    ):
        return JsonResponse(
            {'results': []},
            status=403
        )

    busqueda = request.GET.get('q', '').strip()

    clientes = Cliente.objects.filter(
        activo=True
    ).order_by('nombre')

    if len(busqueda) >= 2:
        clientes = clientes.filter(
            Q(nombre__icontains=busqueda)
            | Q(telefono__icontains=busqueda)
            | Q(direccion__icontains=busqueda)
            | Q(referencia__icontains=busqueda)
        )
    else:
        clientes = clientes.none()

    clientes = clientes.annotate(
        pedidos_entregados=Count(
            'pedido',
            filter=Q(pedido__estado=Pedido.ENTREGADO)
        ),
        total_gastado=Sum(
            'pedido__total',
            filter=Q(pedido__estado=Pedido.ENTREGADO)
        ),
        ultima_compra=Max(
            'pedido__fecha_pedido',
            filter=Q(pedido__estado=Pedido.ENTREGADO)
        )
    )[:8]

    results = []

    for cliente in clientes:
        results.append({
            'id': cliente.id,
            'nombre': cliente.nombre,
            'telefono': cliente.telefono,
            'direccion': cliente.direccion,
            'referencia': cliente.referencia or '',
            'estado': cliente.estado,
            'pedidos_entregados': cliente.pedidos_entregados,
            'total_gastado': str(cliente.total_gastado or '0.00'),
            'ultima_compra': (
                timezone.localtime(cliente.ultima_compra).strftime('%d/%m/%Y')
                if cliente.ultima_compra
                else 'Sin compras'
            ),
        })

    return JsonResponse({'results': results})


@login_required
@secretaria_required
def reporte_diario(request):

    hoy = timezone.localdate()
    ayer = hoy - timedelta(days=1)
    antes_ayer = hoy - timedelta(days=2)
    hace_3_dias = hoy - timedelta(days=3)
    inicio_mes_actual = hoy.replace(day=1)

    comparativo_ventas = []

    for etiqueta, fecha in [
        ('Hoy', hoy),
        ('Ayer', ayer),
        ('Antes de ayer', antes_ayer),
        ('Hace 3 días', hace_3_dias),
    ]:
        pedidos_dia = Pedido.objects.filter(
            estado=Pedido.ENTREGADO,
            fecha_pedido__date=fecha
        )

        comparativo_ventas.append({
            'etiqueta': etiqueta,
            'fecha': fecha,
            'ingresos': pedidos_dia.aggregate(total=Sum('total'))['total'] or 0,
            'bidones': pedidos_dia.aggregate(total=Sum('cantidad_bidones'))['total'] or 0,
            'pedidos': pedidos_dia.count(),
        })

    ingresos_hoy = comparativo_ventas[0]['ingresos']
    ingresos_ayer = comparativo_ventas[1]['ingresos']

    if ingresos_hoy > ingresos_ayer:
        lectura_comparativo = 'Hoy vas mejor que ayer.'
    elif ingresos_hoy < ingresos_ayer:
        lectura_comparativo = 'Hoy vas por debajo de ayer.'
    else:
        lectura_comparativo = 'Hoy vas similar a ayer.'

    detalle_diario_mes = []
    fecha_iteracion = inicio_mes_actual

    while fecha_iteracion <= hoy:
        pedidos_dia = Pedido.objects.filter(
            estado=Pedido.ENTREGADO,
            fecha_pedido__date=fecha_iteracion
        )

        detalle_diario_mes.append({
            'fecha': fecha_iteracion,
            'ingresos': pedidos_dia.aggregate(total=Sum('total'))['total'] or 0,
            'bidones': pedidos_dia.aggregate(total=Sum('cantidad_bidones'))['total'] or 0,
            'pedidos': pedidos_dia.count(),
        })

        fecha_iteracion += timedelta(days=1)

    context = {
        'ventas_hoy': comparativo_ventas[0]['ingresos'],
        'bidones_hoy': comparativo_ventas[0]['bidones'],
        'pedidos_entregados_hoy': comparativo_ventas[0]['pedidos'],
        'comparativo_ventas': comparativo_ventas,
        'lectura_comparativo': lectura_comparativo,
        'detalle_diario_mes': detalle_diario_mes,
        'hoy': hoy,
    }

    return render(
        request,
        'core/reporte_diario.html',
        context
    )


@login_required
@clientes_required
def detalle_cliente(request, cliente_id):

    cliente = get_object_or_404(
        Cliente,
        id=cliente_id
    )

    pedidos = Pedido.objects.filter(
        cliente=cliente
    ).order_by('-fecha_pedido')

    total_pedidos = pedidos.count()
    pedidos_recientes = pedidos[:HISTORIAL_CLIENTE_LIMITE]
    historial_tiene_mas = total_pedidos > HISTORIAL_CLIENTE_LIMITE

    total_gastado = pedidos.filter(
        estado=Pedido.ENTREGADO
    ).aggregate(
        total=Sum('total')
    )['total'] or 0

    ultimo_pedido = pedidos.first()
    puede_ver_historial_pedidos = (
        puede_ver_crm_operativo(request.user)
        or es_jefe_repartidores(request.user)
    )
    historial_pedidos = []
    historial_pedidos_tiene_mas = False

    if puede_ver_historial_pedidos:
        historial_queryset = PedidoHistorial.objects.filter(
            pedido__cliente=cliente
        ).select_related(
            'pedido',
            'usuario'
        )
        historial_pedidos = historial_queryset[:10]
        historial_pedidos_tiene_mas = historial_queryset.count() > 10

    context = {
        'cliente': cliente,
        'pedidos': pedidos_recientes,
        'total_pedidos': total_pedidos,
        'total_gastado': total_gastado,
        'ultimo_pedido': ultimo_pedido,
        'historial_tiene_mas': historial_tiene_mas,
        'historial_pedidos': historial_pedidos,
        'historial_pedidos_tiene_mas': historial_pedidos_tiene_mas,
        'puede_ver_historial_pedidos': puede_ver_historial_pedidos,
    }

    return render(
        request,
        'core/detalle_cliente.html',
        context
    )
@login_required
@secretaria_required
def eliminar_cliente(request, cliente_id):

    if request.method != 'POST':
        return redirect('detalle_cliente', cliente_id=cliente_id)

    cliente = get_object_or_404(
        Cliente,
        id=cliente_id
    )

    pedidos_cliente = Pedido.objects.filter(
        cliente=cliente
    ).exists()

    if pedidos_cliente:
        messages.error(
            request,
            'No se puede eliminar este cliente porque tiene pedidos registrados.'
        )
        return redirect('lista_clientes')

    cliente.delete()

    messages.success(
        request,
        'Cliente eliminado correctamente.'
    )

    return redirect('lista_clientes')
@login_required
@secretaria_required
def editar_cliente(request, cliente_id):

    cliente = get_object_or_404(
        Cliente,
        id=cliente_id
    )

    if request.method == 'POST':

        nombre = request.POST.get('nombre', '').strip()
        telefono = request.POST.get('telefono', '').strip()
        direccion = request.POST.get('direccion', '').strip()
        referencia = request.POST.get('referencia', '').strip()

        if not nombre or not telefono or not direccion:
            messages.error(
                request,
                'Nombre, teléfono y dirección son obligatorios.'
            )
            return redirect(
                'editar_cliente',
                cliente_id=cliente.id
            )

        if len(nombre) > 100 or len(telefono) > 20 or len(direccion) > 255:
            messages.error(
                request,
                'Revisa la longitud de nombre, teléfono o dirección.'
            )
            return redirect(
                'editar_cliente',
                cliente_id=cliente.id
            )

        if len(telefono) < 6:
            messages.error(request, 'El teléfono debe tener al menos 6 caracteres.')
            return redirect(
                'editar_cliente',
                cliente_id=cliente.id
            )

        telefono_duplicado = Cliente.objects.filter(
            telefono=telefono
        ).exclude(
            id=cliente.id
        ).exists()

        if telefono_duplicado:
            messages.error(request, 'Ya existe otro cliente con ese teléfono.')
            return redirect(
                'editar_cliente',
                cliente_id=cliente.id
            )

        cliente.nombre = nombre
        cliente.telefono = telefono
        cliente.direccion = direccion
        cliente.referencia = referencia

        cliente.save()

        messages.success(
            request,
            'Cliente actualizado correctamente.'
        )

        return redirect(
            'detalle_cliente',
            cliente_id=cliente.id
        )

    context = {
        'cliente': cliente
    }

    return render(
        request,
        'core/editar_cliente.html',
        context
    )
@login_required
@secretaria_required
def registrar_cliente(request):

    if request.method == 'POST':

        nombre = request.POST.get('nombre', '').strip()
        telefono = request.POST.get('telefono', '').strip()
        direccion = request.POST.get('direccion', '').strip()
        referencia = request.POST.get('referencia', '').strip()

        if not nombre or not telefono or not direccion:
            messages.error(request, 'Nombre, teléfono y dirección son obligatorios.')
            return redirect('registrar_cliente')

        if len(nombre) > 100 or len(telefono) > 20 or len(direccion) > 255:
            messages.error(request, 'Revisa la longitud de nombre, teléfono o dirección.')
            return redirect('registrar_cliente')

        if len(telefono) < 6:
            messages.error(request, 'El teléfono debe tener al menos 6 caracteres.')
            return redirect('registrar_cliente')

        if Cliente.objects.filter(telefono=telefono).exists():
            messages.error(request, 'Ya existe un cliente con ese teléfono.')
            return redirect('registrar_cliente')

        Cliente.objects.create(
            nombre=nombre,
            telefono=telefono,
            direccion=direccion,
            referencia=referencia
        )

        messages.success(request, 'Cliente registrado correctamente.')
        return redirect('lista_clientes')

    return render(request, 'core/registrar_cliente.html')

@login_required
@secretaria_required
def registrar_pedido(request):

    clientes = Cliente.objects.filter(
        activo=True
    ).annotate(
        pedidos_entregados=Count(
            'pedido',
            filter=Q(pedido__estado=Pedido.ENTREGADO)
        ),
        total_gastado=Sum(
            'pedido__total',
            filter=Q(pedido__estado=Pedido.ENTREGADO)
        ),
        ultima_compra=Max(
            'pedido__fecha_pedido',
            filter=Q(pedido__estado=Pedido.ENTREGADO)
        )
    ).order_by('nombre')
    repartidores = repartidores_disponibles()
    cliente_preseleccionado = None
    cliente_preseleccionado_id = request.GET.get('cliente', '').strip()

    if cliente_preseleccionado_id.isdigit():
        cliente_preseleccionado = clientes.filter(
            id=int(cliente_preseleccionado_id)
        ).first()

    if request.method == 'POST':

        cliente_id = request.POST.get('cliente', '').strip()
        repartidor_id = request.POST.get('repartidor', '').strip()
        cantidad_bidones = request.POST.get('cantidad_bidones', '').strip()
        precio_unitario = request.POST.get('precio_unitario', '').strip()
        estado = request.POST.get('estado', '').strip()
        observacion = request.POST.get('observacion', '').strip()
        fecha_programada = request.POST.get('fecha_programada', '').strip()

        estados_validos = [
            Pedido.PENDIENTE,
            Pedido.ENTREGADO,
            Pedido.CANCELADO,
        ]

        if not cliente_id:
            messages.error(request, 'Debe seleccionar un cliente.')
            return redirect('registrar_pedido')

        if not cliente_id.isdigit():
            messages.error(request, 'Cliente seleccionado inválido.')
            return redirect('registrar_pedido')

        if estado not in estados_validos:
            messages.error(request, 'Estado de pedido inválido.')
            return redirect('registrar_pedido')

        if not cantidad_bidones or not precio_unitario:
            messages.error(request, 'Cantidad y precio unitario son obligatorios.')
            return redirect('registrar_pedido')

        try:
            cantidad_bidones = int(cantidad_bidones)
            precio_unitario = Decimal(precio_unitario)

        except (ValueError, InvalidOperation):
            messages.error(request, 'Cantidad o precio inválido.')
            return redirect('registrar_pedido')

        if cantidad_bidones < 1:
            messages.error(request, 'La cantidad mínima es 1 bidón.')
            return redirect('registrar_pedido')

        if cantidad_bidones > 100:
            messages.error(request, 'La cantidad máxima permitida es 100 bidones.')
            return redirect('registrar_pedido')

        if precio_unitario < Decimal('1.00'):
            messages.error(request, 'El precio unitario mínimo permitido es S/ 1.00.')
            return redirect('registrar_pedido')

        if precio_unitario > Decimal('50.00'):
            messages.error(request, 'El precio unitario máximo permitido es S/ 50.00.')
            return redirect('registrar_pedido')

        fecha_programada_valor = timezone.localdate()

        if fecha_programada:
            try:
                fecha_programada_valor = datetime.strptime(
                    fecha_programada,
                    '%Y-%m-%d'
                ).date()
            except ValueError:
                messages.error(request, 'Fecha programada inválida.')
                return redirect('registrar_pedido')

        if fecha_programada_valor < timezone.localdate():
            messages.error(request, 'La fecha programada no puede ser anterior a hoy.')
            return redirect('registrar_pedido')

        if estado == Pedido.ENTREGADO and fecha_programada_valor > timezone.localdate():
            messages.error(request, 'Un pedido entregado no puede tener fecha programada futura.')
            return redirect('registrar_pedido')

        cliente = Cliente.objects.filter(
            id=int(cliente_id),
            activo=True
        ).first()

        if not cliente:
            messages.error(request, 'El cliente seleccionado no existe o está inactivo.')
            return redirect('registrar_pedido')

        repartidor = None

        if repartidor_id:
            if not repartidor_id.isdigit():
                messages.error(request, 'Repartidor seleccionado inválido.')
                return redirect('registrar_pedido')

            repartidor = repartidores.filter(
                id=int(repartidor_id)
            ).first()

            if not repartidor:
                messages.error(request, 'El repartidor seleccionado no está activo.')
                return redirect('registrar_pedido')

        total = cantidad_bidones * precio_unitario
        fecha_entrega = None
        fecha_cancelacion = None

        if estado == Pedido.ENTREGADO:
            fecha_entrega = timezone.now()
        elif estado == Pedido.CANCELADO:
            fecha_cancelacion = timezone.now()

        pedido = Pedido.objects.create(
            cliente=cliente,
            repartidor=repartidor,
            cantidad_bidones=cantidad_bidones,
            precio_unitario=precio_unitario,
            total=total,
            estado=estado,
            observacion=observacion,
            fecha_programada=fecha_programada_valor,
            fecha_entrega=fecha_entrega,
            fecha_cancelacion=fecha_cancelacion,
        )
        registrar_historial_pedido(
            pedido,
            request.user,
            PedidoHistorial.CREADO,
            (
                'Pedido creado por '
                f'{nombre_usuario_historial(request.user)}.'
            ),
            valor_nuevo=resumen_pedido_historial(pedido)
        )

        actualizar_estados_clientes()

        messages.success(request, 'Pedido registrado correctamente.')
        return redirect('lista_pedidos')

    context = {
        'clientes': clientes,
        'repartidores': repartidores,
        'hoy': timezone.localdate(),
        'cliente_preseleccionado': cliente_preseleccionado,
    }

    return render(request, 'core/registrar_pedido.html', context)


@login_required
def editar_pedido(request, pedido_id):

    if not puede_editar_pedido(request.user):
        messages.error(request, 'No tienes permiso para editar pedidos.')
        return redirect(destino_usuario(request.user))

    pedido = get_object_or_404(
        Pedido.objects.select_related(
            'cliente',
            'repartidor'
        ),
        id=pedido_id
    )
    origen = (
        request.POST.get('origen', '').strip()
        or request.GET.get('origen', '').strip()
    )
    repartidores = repartidores_disponibles()

    if pedido.estado != Pedido.PENDIENTE:
        messages.error(
            request,
            'Solo se pueden editar pedidos pendientes.'
        )
        return redireccion_edicion_pedido(origen, pedido)

    if request.method == 'POST':
        cantidad_bidones = request.POST.get('cantidad_bidones', '').strip()
        precio_unitario = request.POST.get('precio_unitario', '').strip()
        fecha_programada = request.POST.get('fecha_programada', '').strip()
        observacion = request.POST.get('observacion', '').strip()
        repartidor_id = request.POST.get('repartidor', '').strip()

        if not cantidad_bidones or not precio_unitario:
            messages.error(
                request,
                'Cantidad y precio unitario son obligatorios.'
            )
            return redirect('editar_pedido', pedido_id=pedido.id)

        try:
            cantidad_bidones = int(cantidad_bidones)
            precio_unitario = Decimal(precio_unitario)
        except (ValueError, InvalidOperation):
            messages.error(request, 'Cantidad o precio inválido.')
            return redirect('editar_pedido', pedido_id=pedido.id)

        if cantidad_bidones < 1:
            messages.error(request, 'La cantidad mínima es 1 bidón.')
            return redirect('editar_pedido', pedido_id=pedido.id)

        if cantidad_bidones > 100:
            messages.error(
                request,
                'La cantidad máxima permitida es 100 bidones.'
            )
            return redirect('editar_pedido', pedido_id=pedido.id)

        if precio_unitario < Decimal('1.00'):
            messages.error(
                request,
                'El precio unitario mínimo permitido es S/ 1.00.'
            )
            return redirect('editar_pedido', pedido_id=pedido.id)

        if precio_unitario > Decimal('50.00'):
            messages.error(
                request,
                'El precio unitario máximo permitido es S/ 50.00.'
            )
            return redirect('editar_pedido', pedido_id=pedido.id)

        fecha_programada_valor = timezone.localdate()

        if fecha_programada:
            try:
                fecha_programada_valor = datetime.strptime(
                    fecha_programada,
                    '%Y-%m-%d'
                ).date()
            except ValueError:
                messages.error(request, 'Fecha programada inválida.')
                return redirect('editar_pedido', pedido_id=pedido.id)

        if fecha_programada_valor < timezone.localdate():
            messages.error(
                request,
                'La fecha programada no puede ser anterior a hoy.'
            )
            return redirect('editar_pedido', pedido_id=pedido.id)

        repartidor = None

        if repartidor_id:
            if not repartidor_id.isdigit():
                messages.error(request, 'Repartidor seleccionado inválido.')
                return redirect('editar_pedido', pedido_id=pedido.id)

            repartidor = repartidores.filter(
                id=int(repartidor_id)
            ).first()

            if not repartidor:
                messages.error(
                    request,
                    'El repartidor seleccionado no está activo.'
                )
                return redirect('editar_pedido', pedido_id=pedido.id)

        nuevo_total = cantidad_bidones * precio_unitario
        nuevos_valores = {
            'cantidad_bidones': cantidad_bidones,
            'precio_unitario': precio_unitario,
            'total': nuevo_total,
            'fecha_programada': fecha_programada_valor,
            'observacion': observacion,
        }
        cambios = cambios_edicion_pedido(pedido, nuevos_valores)
        repartidor_anterior = pedido.repartidor
        repartidor_cambio = pedido.repartidor_id != (
            repartidor.id if repartidor else None
        )

        pedido.cantidad_bidones = cantidad_bidones
        pedido.precio_unitario = precio_unitario
        pedido.total = nuevo_total
        pedido.fecha_programada = fecha_programada_valor
        pedido.observacion = observacion
        pedido.repartidor = repartidor
        pedido.save(
            update_fields=[
                'cantidad_bidones',
                'precio_unitario',
                'total',
                'fecha_programada',
                'observacion',
                'repartidor',
            ]
        )

        if cambios:
            descripcion_cambios = [
                (
                    f"{cambio['etiqueta']}: "
                    f"{cambio['anterior']} -> {cambio['nuevo']}"
                )
                for cambio in cambios
            ]
            registrar_historial_pedido(
                pedido,
                request.user,
                PedidoHistorial.EDITADO,
                'Pedido editado: ' + '; '.join(descripcion_cambios),
                valor_anterior='; '.join(
                    f"{cambio['etiqueta']}: {cambio['anterior']}"
                    for cambio in cambios
                ),
                valor_nuevo='; '.join(
                    f"{cambio['etiqueta']}: {cambio['nuevo']}"
                    for cambio in cambios
                )
            )

        if repartidor_cambio:
            anterior_texto = nombre_repartidor_historial(repartidor_anterior)
            nuevo_texto = nombre_repartidor_historial(repartidor)
            logger.info(
                'Web Push diagnostico: reasignacion desde editar_pedido. '
                'pedido_id=%s repartidor_anterior=%s repartidor_destino=%s',
                pedido.id,
                repartidor_anterior.id if repartidor_anterior else None,
                repartidor.id if repartidor else None
            )
            registrar_historial_pedido(
                pedido,
                request.user,
                PedidoHistorial.REASIGNADO,
                f'Repartidor reasignado: {anterior_texto} -> {nuevo_texto}.',
                valor_anterior=anterior_texto,
                valor_nuevo=nuevo_texto
            )
            send_order_assignment_push(
                pedido,
                previous_repartidor=repartidor_anterior
            )

        messages.success(
            request,
            'Pedido actualizado correctamente.'
        )
        return redireccion_edicion_pedido(origen, pedido)

    context = {
        'pedido': pedido,
        'repartidores': repartidores,
        'origen': origen,
        'hoy': timezone.localdate(),
    }

    return render(
        request,
        'core/editar_pedido.html',
        context
    )


@login_required
@secretaria_required
def lista_pedidos(request):

    hoy = timezone.localdate()
    manana = hoy + timedelta(days=1)

    pedidos = Pedido.objects.select_related(
        'cliente',
        'repartidor'
    ).annotate(
        prioridad_operativa=Case(
            When(
                estado=Pedido.PENDIENTE,
                fecha_programada__lt=hoy,
                then=Value(1)
            ),
            When(
                estado=Pedido.PENDIENTE,
                fecha_programada=hoy,
                then=Value(2)
            ),
            When(
                estado=Pedido.PENDIENTE,
                fecha_programada__isnull=True,
                then=Value(2)
            ),
            When(
                estado=Pedido.PENDIENTE,
                fecha_programada=manana,
                then=Value(3)
            ),
            When(
                estado=Pedido.PENDIENTE,
                fecha_programada__gt=manana,
                then=Value(4)
            ),
            When(
                estado=Pedido.ENTREGADO,
                then=Value(5)
            ),
            When(
                estado=Pedido.CANCELADO,
                then=Value(6)
            ),
            default=Value(7),
            output_field=IntegerField()
        ),
        fecha_programada_orden=Case(
            When(
                estado=Pedido.PENDIENTE,
                then=F('fecha_programada')
            ),
            output_field=DateField()
        ),
        fecha_pedido_pendiente_orden=Case(
            When(
                estado=Pedido.PENDIENTE,
                then=F('fecha_pedido')
            ),
            output_field=DateTimeField()
        ),
        fecha_pedido_cerrado_orden=Case(
            When(
                estado__in=[
                    Pedido.ENTREGADO,
                    Pedido.CANCELADO,
                ],
                then=F('fecha_pedido')
            ),
            output_field=DateTimeField()
        )
    )

    busqueda = request.GET.get('busqueda', '').strip()
    estado = request.GET.get('estado', '').strip()
    filtro = request.GET.get('filtro', '').strip()

    if busqueda:
        pedidos = pedidos.filter(
            cliente__nombre__icontains=busqueda
        )

    if estado:
        estados_validos = [
            Pedido.PENDIENTE,
            Pedido.ENTREGADO,
            Pedido.CANCELADO,
        ]

        if estado in estados_validos:
            pedidos = pedidos.filter(
                estado=estado
            )
        else:
            messages.error(request, 'Filtro de estado inválido.')
            estado = ''

    filtros_validos = [
        'hoy',
        'atrasados',
        'programados',
        'entregados',
        'cancelados',
    ]

    if filtro:
        if filtro == 'hoy':
            pedidos = pedidos.filter(
                estado=Pedido.PENDIENTE
            ).filter(
                Q(fecha_programada=hoy)
                | Q(fecha_programada__isnull=True)
            )
        elif filtro == 'atrasados':
            pedidos = pedidos.filter(
                estado=Pedido.PENDIENTE,
                fecha_programada__lt=hoy
            )
        elif filtro == 'programados':
            pedidos = pedidos.filter(
                estado=Pedido.PENDIENTE,
                fecha_programada__gt=hoy
            )
        elif filtro == 'entregados':
            pedidos = pedidos.filter(
                estado=Pedido.ENTREGADO
            )
        elif filtro == 'cancelados':
            pedidos = pedidos.filter(
                estado=Pedido.CANCELADO
            )
        elif filtro not in filtros_validos:
            messages.error(request, 'Filtro rápido inválido.')
            filtro = ''

    pedidos = pedidos.order_by(
        'prioridad_operativa',
        F('fecha_programada_orden').asc(nulls_last=True),
        F('fecha_pedido_pendiente_orden').asc(nulls_last=True),
        F('fecha_pedido_cerrado_orden').desc(nulls_last=True),
        '-id'
    )

    page_obj, pagination_query = paginar_queryset(
        request,
        pedidos,
        PEDIDOS_POR_PAGINA
    )

    for pedido in page_obj:
        preparar_pedido_lista(pedido)

    context = {
        'pedidos': page_obj,
        'page_obj': page_obj,
        'pagination_query': pagination_query,
        'busqueda': busqueda,
        'estado_actual': estado,
        'filtro_actual': filtro,
    }

    return render(request, 'core/pedidos.html', context)


@login_required
@repartidor_required
def pedidos_repartidor(request):

    hoy = timezone.localdate()

    pedidos_base = Pedido.objects.filter(
        estado=Pedido.PENDIENTE,
        repartidor=request.user
    ).select_related(
        'cliente'
    ).annotate(
        cliente_entregados=Count(
            'cliente__pedido',
            filter=Q(cliente__pedido__estado=Pedido.ENTREGADO)
        )
    )

    pedidos_hoy = pedidos_base.filter(
        Q(fecha_programada__lte=hoy)
        | Q(fecha_programada__isnull=True)
    ).order_by(
        'fecha_programada',
        'fecha_pedido'
    )

    pedidos_hoy = [
        preparar_pedido_repartidor(pedido)
        for pedido in pedidos_hoy
    ]

    pedidos_programados = pedidos_base.filter(
        fecha_programada__gt=hoy
    ).order_by(
        'fecha_programada',
        'fecha_pedido'
    )

    pedidos_programados = [
        preparar_pedido_repartidor(pedido)
        for pedido in pedidos_programados
    ]

    entregas_hoy = Pedido.objects.filter(
        estado=Pedido.ENTREGADO,
        repartidor=request.user,
        fecha_entrega__date=hoy
    ).select_related(
        'cliente'
    ).order_by(
        '-fecha_entrega',
        '-fecha_pedido'
    )

    entregas_hoy = list(entregas_hoy)

    for entrega in entregas_hoy:
        entrega.hora_entrega_panel = hora_panel(entrega.fecha_entrega)

    cancelados_hoy = Pedido.objects.filter(
        estado=Pedido.CANCELADO,
        repartidor=request.user,
        fecha_cancelacion__date=hoy
    ).select_related(
        'cliente'
    ).order_by(
        '-fecha_cancelacion',
        '-fecha_pedido'
    )

    cancelados_hoy = list(cancelados_hoy)

    for cancelado in cancelados_hoy:
        cancelado.hora_cancelacion_panel = hora_panel(cancelado.fecha_cancelacion)

    total_bidones_hoy = sum(
        entrega.cantidad_bidones
        for entrega in entregas_hoy
    )
    total_dinero_hoy = sum(
        entrega.total
        for entrega in entregas_hoy
    )
    total_pedidos_hoy = len(entregas_hoy)

    context = {
        'pedidos': pedidos_hoy,
        'pedidos_hoy': pedidos_hoy,
        'pedidos_programados': pedidos_programados,
        'total_pedidos_programados': len(pedidos_programados),
        'entregas_hoy': entregas_hoy,
        'cancelados_hoy': cancelados_hoy,
        'total_bidones_hoy': total_bidones_hoy,
        'total_dinero_hoy': total_dinero_hoy,
        'total_pedidos_hoy': total_pedidos_hoy,
        'hoy': hoy,
        'grupo_repartidor': 'Repartidores',
    }

    return render(
        request,
        'core/pedidos_repartidor.html',
        context
    )


@login_required
@jefe_repartidores_required
def panel_jefe_repartidores(request):

    hoy = timezone.localdate()
    repartidores = repartidores_disponibles()

    pedidos_sin_asignar_queryset = Pedido.objects.filter(
        estado=Pedido.PENDIENTE,
        repartidor__isnull=True,
    ).filter(
        Q(fecha_programada__lte=hoy)
        | Q(fecha_programada__isnull=True)
    ).select_related(
        'cliente'
    ).order_by(
        'fecha_programada',
        'fecha_pedido'
    )

    total_pedidos_sin_asignar = pedidos_sin_asignar_queryset.count()
    pedidos_sin_asignar = list(
        pedidos_sin_asignar_queryset[:PANEL_JEFE_LIMITE_PEDIDOS]
    )

    pedidos_asignados_queryset = Pedido.objects.filter(
        estado=Pedido.PENDIENTE,
        repartidor__isnull=False,
    ).filter(
        Q(fecha_programada__lte=hoy)
        | Q(fecha_programada__isnull=True)
    ).select_related(
        'cliente',
        'repartidor'
    ).order_by(
        'fecha_programada',
        'fecha_pedido',
        'repartidor__username'
    )

    total_pedidos_asignados = pedidos_asignados_queryset.count()
    pedidos_asignados = list(
        pedidos_asignados_queryset[:PANEL_JEFE_LIMITE_PEDIDOS]
    )

    pedidos_programados_queryset = Pedido.objects.filter(
        estado=Pedido.PENDIENTE,
        fecha_programada__gt=hoy
    ).select_related(
        'cliente',
        'repartidor'
    ).order_by(
        'fecha_programada',
        'fecha_pedido',
        'repartidor__username'
    )

    total_pedidos_programados = pedidos_programados_queryset.count()
    pedidos_programados = list(
        pedidos_programados_queryset[:PANEL_JEFE_LIMITE_PEDIDOS]
    )

    for pedido in pedidos_sin_asignar:
        preparar_pedido_panel_jefe(pedido)

    for pedido in pedidos_asignados:
        preparar_pedido_panel_jefe(pedido)

    for pedido in pedidos_programados:
        preparar_pedido_panel_jefe(pedido)

    resumen_repartidores = []

    for repartidor in repartidores:
        pendientes_asignados = Pedido.objects.filter(
            estado=Pedido.PENDIENTE,
            repartidor=repartidor
        ).count()

        entregados_hoy = Pedido.objects.filter(
            estado=Pedido.ENTREGADO,
            repartidor=repartidor,
            fecha_entrega__date=hoy
        )

        cancelados_hoy = Pedido.objects.filter(
            estado=Pedido.CANCELADO,
            repartidor=repartidor,
            fecha_cancelacion__date=hoy
        ).count()

        resumen_repartidores.append({
            'usuario': repartidor,
            'pendientes': pendientes_asignados,
            'entregados_hoy': entregados_hoy.count(),
            'cancelados_hoy': cancelados_hoy,
            'bidones_hoy': entregados_hoy.aggregate(
                total=Sum('cantidad_bidones')
            )['total'] or 0,
            'dinero_hoy': entregados_hoy.aggregate(
                total=Sum('total')
            )['total'] or 0,
        })

    context = {
        'pedidos_sin_asignar': pedidos_sin_asignar,
        'pedidos_asignados': pedidos_asignados,
        'pedidos_programados': pedidos_programados,
        'repartidores': repartidores,
        'resumen_repartidores': resumen_repartidores,
        'total_pedidos_sin_asignar': total_pedidos_sin_asignar,
        'total_pedidos_asignados': total_pedidos_asignados,
        'total_pedidos_programados': total_pedidos_programados,
        'hay_mas_sin_asignar': total_pedidos_sin_asignar > PANEL_JEFE_LIMITE_PEDIDOS,
        'hay_mas_asignados': total_pedidos_asignados > PANEL_JEFE_LIMITE_PEDIDOS,
        'hay_mas_programados': total_pedidos_programados > PANEL_JEFE_LIMITE_PEDIDOS,
    }

    return render(
        request,
        'core/panel_jefe_repartidores.html',
        context
    )


@login_required
@jefe_repartidores_required
@require_POST
def asignar_pedido_repartidor(request, pedido_id):

    repartidor_id = request.POST.get('repartidor', '').strip()

    if not repartidor_id or not repartidor_id.isdigit():
        messages.error(request, 'Debe seleccionar un repartidor valido.')
        return redirect('panel_jefe_repartidores')

    repartidor = repartidores_disponibles().filter(
        id=int(repartidor_id)
    ).first()

    if not repartidor:
        messages.error(request, 'El repartidor seleccionado no está activo.')
        return redirect('panel_jefe_repartidores')

    pedido = get_object_or_404(
        Pedido,
        id=pedido_id,
        estado=Pedido.PENDIENTE
    )

    repartidor_anterior = pedido.repartidor
    pedido.repartidor = repartidor
    pedido.save(update_fields=['repartidor'])
    logger.info(
        'Web Push diagnostico: asignacion desde panel jefe. pedido_id=%s '
        'repartidor_anterior=%s repartidor_destino=%s',
        pedido.id,
        repartidor_anterior.id if repartidor_anterior else None,
        repartidor.id
    )
    registrar_historial_pedido(
        pedido,
        request.user,
        PedidoHistorial.REASIGNADO,
        (
            'Repartidor asignado: '
            f'{nombre_repartidor_historial(repartidor_anterior)} -> '
            f'{nombre_repartidor_historial(repartidor)}.'
        ),
        valor_anterior=nombre_repartidor_historial(repartidor_anterior),
        valor_nuevo=nombre_repartidor_historial(repartidor)
    )
    send_order_assignment_push(
        pedido,
        previous_repartidor=repartidor_anterior
    )

    messages.success(
        request,
        'Pedido asignado correctamente.'
    )

    return redirect('panel_jefe_repartidores')


@login_required
@repartidor_required
@require_POST
def marcar_pedido_entregado_repartidor(request, pedido_id):

    pedido = get_object_or_404(
        Pedido,
        id=pedido_id,
        estado=Pedido.PENDIENTE,
        repartidor=request.user
    )

    estado_anterior = pedido.estado
    pedido.estado = Pedido.ENTREGADO
    pedido.repartidor = request.user
    pedido.fecha_entrega = timezone.now()
    pedido.save(update_fields=['estado', 'repartidor', 'fecha_entrega'])
    registrar_historial_pedido(
        pedido,
        request.user,
        PedidoHistorial.ENTREGADO,
        'Pedido marcado como entregado por repartidor.',
        valor_anterior=estado_anterior,
        valor_nuevo=Pedido.ENTREGADO
    )

    actualizar_estados_clientes()

    messages.success(
        request,
        'Pedido entregado correctamente.'
    )

    return redirect('pedidos_repartidor')


@login_required
@repartidor_required
def nuevo_pedido_repartidor(request):

    clientes = Cliente.objects.filter(
        activo=True
    ).order_by('nombre')
    cliente_preseleccionado = None
    cliente_preseleccionado_id = request.GET.get('cliente', '').strip()

    if cliente_preseleccionado_id.isdigit():
        cliente_preseleccionado = clientes.filter(
            id=int(cliente_preseleccionado_id)
        ).first()

    if request.method == 'POST':

        cliente_id = request.POST.get('cliente', '').strip()
        cantidad_bidones = request.POST.get('cantidad_bidones', '').strip()
        precio_unitario = request.POST.get('precio_unitario', '').strip()
        fecha_programada = request.POST.get('fecha_programada', '').strip()
        observacion = request.POST.get('observacion', '').strip()
        entregar_ahora = request.POST.get('entregar_ahora') == 'on'

        if not cliente_id or not cliente_id.isdigit():
            messages.error(request, 'Debe seleccionar un cliente válido.')
            return redirect('nuevo_pedido_repartidor')

        if not cantidad_bidones or not precio_unitario:
            messages.error(
                request,
                'Cantidad y precio unitario son obligatorios.'
            )
            return redirect('nuevo_pedido_repartidor')

        try:
            cantidad_bidones = int(cantidad_bidones)
            precio_unitario = Decimal(precio_unitario)
        except (ValueError, InvalidOperation):
            messages.error(request, 'Cantidad o precio inválido.')
            return redirect('nuevo_pedido_repartidor')

        if cantidad_bidones < 1:
            messages.error(request, 'La cantidad mínima es 1 bidón.')
            return redirect('nuevo_pedido_repartidor')

        if precio_unitario <= Decimal('0'):
            messages.error(request, 'El precio unitario debe ser mayor a 0.')
            return redirect('nuevo_pedido_repartidor')

        fecha_programada_valor = timezone.localdate()

        if fecha_programada and not entregar_ahora:
            try:
                fecha_programada_valor = datetime.strptime(
                    fecha_programada,
                    '%Y-%m-%d'
                ).date()
            except ValueError:
                messages.error(request, 'Fecha programada inválida.')
                return redirect('nuevo_pedido_repartidor')

        if fecha_programada_valor < timezone.localdate():
            messages.error(request, 'La fecha programada no puede ser anterior a hoy.')
            return redirect('nuevo_pedido_repartidor')

        cliente = Cliente.objects.filter(
            id=int(cliente_id),
            activo=True
        ).first()

        if not cliente:
            messages.error(request, 'El cliente seleccionado no existe o está inactivo.')
            return redirect('nuevo_pedido_repartidor')

        total = cantidad_bidones * precio_unitario
        estado = Pedido.ENTREGADO if entregar_ahora else Pedido.PENDIENTE
        fecha_entrega = timezone.now() if entregar_ahora else None

        pedido = Pedido.objects.create(
            cliente=cliente,
            repartidor=request.user,
            cantidad_bidones=cantidad_bidones,
            precio_unitario=precio_unitario,
            total=total,
            estado=estado,
            observacion=observacion,
            fecha_programada=fecha_programada_valor,
            fecha_entrega=fecha_entrega,
        )
        registrar_historial_pedido(
            pedido,
            request.user,
            PedidoHistorial.CREADO,
            (
                'Pedido creado desde panel repartidor por '
                f'{nombre_usuario_historial(request.user)}.'
            ),
            valor_nuevo=resumen_pedido_historial(pedido)
        )

        actualizar_estados_clientes()

        if entregar_ahora:
            messages.success(
                request,
                'Pedido rápido registrado como entregado.'
            )
        else:
            messages.success(
                request,
                'Pedido rápido registrado como pendiente.'
            )

        return redirect('pedidos_repartidor')

    context = {
        'clientes': clientes,
        'precio_sugerido': precio_unitario_rapido(None),
        'hoy': timezone.localdate(),
        'cliente_preseleccionado': cliente_preseleccionado,
    }

    return render(
        request,
        'core/nuevo_pedido_repartidor.html',
        context
    )


@login_required
@repartidor_required
def nuevo_cliente_repartidor(request):

    if request.method == 'POST':

        nombre = request.POST.get('nombre', '').strip()
        telefono = request.POST.get('telefono', '').strip()
        direccion = request.POST.get('direccion', '').strip()
        referencia = request.POST.get('referencia', '').strip()

        if not nombre or not telefono or not direccion:
            messages.error(
                request,
                'Nombre, teléfono y dirección son obligatorios.'
            )
            return redirect('nuevo_cliente_repartidor')

        if len(nombre) > 100 or len(telefono) > 20 or len(direccion) > 255:
            messages.error(
                request,
                'Revisa la longitud de nombre, teléfono o dirección.'
            )
            return redirect('nuevo_cliente_repartidor')

        if len(limpiar_telefono(telefono)) < 6:
            messages.error(
                request,
                'El teléfono debe tener al menos 6 dígitos.'
            )
            return redirect('nuevo_cliente_repartidor')

        if Cliente.objects.filter(telefono=telefono).exists():
            messages.error(
                request,
                'Ya existe un cliente con ese teléfono.'
            )
            return redirect('nuevo_cliente_repartidor')

        Cliente.objects.create(
            nombre=nombre,
            telefono=telefono,
            direccion=direccion,
            referencia=referencia
        )

        messages.success(
            request,
            'Cliente registrado correctamente.'
        )
        return redirect('pedidos_repartidor')

    return render(
        request,
        'core/nuevo_cliente_repartidor.html'
    )


@login_required
@repartidor_required
@require_POST
def cancelar_pedido_repartidor(request, pedido_id):

    pedido = get_object_or_404(
        Pedido,
        id=pedido_id,
        estado=Pedido.PENDIENTE,
        repartidor=request.user
    )

    estado_anterior = pedido.estado
    pedido.estado = Pedido.CANCELADO
    pedido.repartidor = request.user
    pedido.fecha_cancelacion = timezone.now()
    pedido.save(update_fields=['estado', 'repartidor', 'fecha_cancelacion'])
    registrar_historial_pedido(
        pedido,
        request.user,
        PedidoHistorial.CANCELADO,
        'Pedido cancelado por repartidor.',
        valor_anterior=estado_anterior,
        valor_nuevo=Pedido.CANCELADO
    )

    actualizar_estados_clientes()

    messages.success(
        request,
        'Pedido cancelado correctamente.'
    )

    return redirect('pedidos_repartidor')


@login_required
@repartidor_required
@require_POST
def cerrar_sesion_repartidor(request):

    logout(request)

    return redirect('/login/')


@login_required
@require_POST
def cerrar_sesion_usuario(request):

    logout(request)

    return redirect('/login/')


@login_required
@secretaria_required
@require_POST
def cambiar_estado_pedido(request, pedido_id, nuevo_estado):

    pedido = get_object_or_404(
        Pedido,
        id=pedido_id
    )

    estados_validos = [
        Pedido.ENTREGADO,
        Pedido.CANCELADO,
    ]

    if nuevo_estado not in estados_validos:
        messages.error(request, 'Estado inválido.')
        return redirect('lista_pedidos')

    if pedido.estado != Pedido.PENDIENTE:
        messages.error(request, 'Solo se pueden modificar pedidos pendientes desde esta acción.')
        return redirect('lista_pedidos')

    if pedido.estado == nuevo_estado:
        messages.error(request, 'El pedido ya tiene ese estado.')
        return redirect('lista_pedidos')

    estado_anterior = pedido.estado
    pedido.estado = nuevo_estado
    update_fields = ['estado']

    if nuevo_estado == Pedido.ENTREGADO:
        pedido.fecha_entrega = timezone.now()
        update_fields.append('fecha_entrega')
    elif nuevo_estado == Pedido.CANCELADO:
        pedido.fecha_cancelacion = timezone.now()
        update_fields.append('fecha_cancelacion')

    pedido.save(update_fields=update_fields)
    tipo_historial = (
        PedidoHistorial.ENTREGADO
        if nuevo_estado == Pedido.ENTREGADO
        else PedidoHistorial.CANCELADO
    )
    registrar_historial_pedido(
        pedido,
        request.user,
        tipo_historial,
        f'Estado de pedido actualizado a {nuevo_estado}.',
        valor_anterior=estado_anterior,
        valor_nuevo=nuevo_estado
    )

    actualizar_estados_clientes()

    if nuevo_estado == Pedido.ENTREGADO:
        messages.success(
            request,
            'Pedido marcado como ENTREGADO correctamente.'
        )

    elif nuevo_estado == Pedido.CANCELADO:
        messages.success(
            request,
            'Pedido cancelado correctamente.'
        )

    else:
        messages.success(
            request,
            'Estado actualizado correctamente.'
        )

    return redirect('lista_pedidos')


@login_required
@secretaria_required
@require_POST
def revertir_entrega(request, pedido_id):

    pedido = get_object_or_404(
        Pedido,
        id=pedido_id
    )

    if pedido.estado != Pedido.ENTREGADO:
        messages.error(
            request,
            'Solo se puede revertir un pedido entregado.'
        )
        return redirect('lista_pedidos')

    estado_anterior = pedido.estado
    pedido.estado = Pedido.PENDIENTE
    pedido.fecha_entrega = None
    pedido.save(update_fields=['estado', 'fecha_entrega'])
    registrar_historial_pedido(
        pedido,
        request.user,
        PedidoHistorial.REVERTIDO,
        'Entrega revertida a estado pendiente.',
        valor_anterior=estado_anterior,
        valor_nuevo=Pedido.PENDIENTE
    )

    actualizar_estados_clientes()

    messages.success(
        request,
        'Entrega revertida. El pedido volvió a estado PENDIENTE.'
    )

    return redirect('lista_pedidos')

@login_required
@secretaria_required
def reporte_mensual(request):

    hoy = timezone.localdate()

    mes = request.GET.get('mes')
    anio = request.GET.get('anio')

    try:
        mes = int(mes)
    except (TypeError, ValueError):
        mes = hoy.month

    if mes < 1 or mes > 12:
        mes = hoy.month

    try:
        anio = int(anio)
    except (TypeError, ValueError):
        anio = hoy.year

    anios = range(hoy.year - 5, hoy.year + 1)

    if anio not in anios:
        anio = hoy.year

    pedidos_mes = Pedido.objects.filter(
        fecha_pedido__year=anio,
        fecha_pedido__month=mes
    )

    pedidos_entregados = pedidos_mes.filter(
        estado=Pedido.ENTREGADO
    )

    ingresos_mes = pedidos_entregados.aggregate(
        total=Sum('total')
    )['total'] or 0

    bidones_mes = pedidos_entregados.aggregate(
        total=Sum('cantidad_bidones')
    )['total'] or 0

    total_entregados = pedidos_entregados.count()

    clientes_unicos = pedidos_entregados.values(
        'cliente'
    ).distinct().count()

    pendientes = pedidos_mes.filter(
        estado=Pedido.PENDIENTE
    ).count()

    cancelados = pedidos_mes.filter(
        estado=Pedido.CANCELADO
    ).count()

    total_estados = total_entregados + pendientes + cancelados

    if total_estados > 0:
        porcentaje_entregados = int((total_entregados / total_estados) * 100)
        porcentaje_pendientes = int((pendientes / total_estados) * 100)
        porcentaje_cancelados = int((cancelados / total_estados) * 100)
    else:
        porcentaje_entregados = 0
        porcentaje_pendientes = 0
        porcentaje_cancelados = 0

    top_cliente_mes = pedidos_entregados.values(
        'cliente__nombre'
    ).annotate(
        bidones=Sum('cantidad_bidones'),
        total=Sum('total')
    ).order_by(
        '-bidones',
        '-total'
    ).first()

    dia_mas_ventas = pedidos_entregados.extra(
        select={'fecha': 'DATE(fecha_pedido)'}
    ).values(
        'fecha'
    ).annotate(
        total=Sum('total'),
        bidones=Sum('cantidad_bidones')
    ).order_by(
        '-total',
        '-bidones'
    ).first()

    if dia_mas_ventas:
        fecha_dia_mas_ventas = dia_mas_ventas.get('fecha')

        if isinstance(fecha_dia_mas_ventas, str):
            fecha_dia_mas_ventas = datetime.strptime(
                fecha_dia_mas_ventas,
                '%Y-%m-%d'
            ).date()

        dia_mas_ventas['fecha_larga'] = fecha_dia_mas_ventas

    detalle_diario_mes = []
    ultimo_dia_mes = monthrange(anio, mes)[1]

    for dia in range(1, ultimo_dia_mes + 1):
        fecha = datetime(anio, mes, dia).date()
        pedidos_dia = pedidos_entregados.filter(
            fecha_pedido__date=fecha
        )
        ingresos_dia = pedidos_dia.aggregate(
            total=Sum('total')
        )['total'] or 0

        detalle_diario_mes.append({
            'fecha': fecha,
            'dia': dia,
            'ingresos': ingresos_dia,
            'bidones': pedidos_dia.aggregate(
                total=Sum('cantidad_bidones')
            )['total'] or 0,
            'pedidos': pedidos_dia.count(),
        })

    max_ingresos_grafico = max(
        [dia['ingresos'] for dia in detalle_diario_mes],
        default=0
    )

    for dia in detalle_diario_mes:
        if max_ingresos_grafico:
            dia['porcentaje'] = int(
                (dia['ingresos'] / max_ingresos_grafico) * 100
            )
        else:
            dia['porcentaje'] = 0

    if total_entregados == 0:
        lectura_mes = 'No hay pedidos entregados en este periodo.'
    elif porcentaje_cancelados >= 25:
        lectura_mes = 'El mes tuvo muchas cancelaciones; conviene revisar motivos y zonas.'
    elif pendientes > total_entregados:
        lectura_mes = 'Hay más pedidos pendientes que entregados; conviene priorizar cierres.'
    elif ingresos_mes > 0 and top_cliente_mes:
        lectura_mes = 'El mes muestra ventas activas y un cliente principal claro.'
    else:
        lectura_mes = 'El mes se mantiene operativo.'

    meses = [
        (1, 'Enero'),
        (2, 'Febrero'),
        (3, 'Marzo'),
        (4, 'Abril'),
        (5, 'Mayo'),
        (6, 'Junio'),
        (7, 'Julio'),
        (8, 'Agosto'),
        (9, 'Septiembre'),
        (10, 'Octubre'),
        (11, 'Noviembre'),
        (12, 'Diciembre'),
    ]

    nombre_mes_analizado = meses[mes - 1][1]
    pedidos_mes_ordenados = pedidos_mes.select_related(
        'cliente'
    ).order_by('-fecha_pedido')
    pedidos_mes_page, pedidos_mes_query = paginar_queryset(
        request,
        pedidos_mes_ordenados,
        PEDIDOS_POR_PAGINA,
        page_param='pedidos_page'
    )

    context = {
        'hoy': hoy,
        'mes_actual': mes,
        'anio_actual': anio,
        'meses': meses,
        'anios': anios,
        'nombre_mes_analizado': nombre_mes_analizado,

        'ingresos_mes': ingresos_mes,
        'bidones_mes': bidones_mes,
        'total_entregados': total_entregados,
        'clientes_unicos': clientes_unicos,
        'pendientes': pendientes,
        'cancelados': cancelados,
        'porcentaje_entregados': porcentaje_entregados,
        'porcentaje_pendientes': porcentaje_pendientes,
        'porcentaje_cancelados': porcentaje_cancelados,
        'top_cliente_mes': top_cliente_mes,
        'dia_mas_ventas': dia_mas_ventas,
        'detalle_diario_mes': detalle_diario_mes,
        'max_ingresos_grafico': max_ingresos_grafico,
        'lectura_mes': lectura_mes,
        'pedidos_mes': pedidos_mes_page,
        'pedidos_mes_page': pedidos_mes_page,
        'pedidos_mes_query': pedidos_mes_query,
    }

    return render(
        request,
        'core/reporte_mensual.html',
        context
    )

