from django.http import FileResponse, JsonResponse
from django.conf import settings
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth import get_user_model, login, logout
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.models import Group
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction
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
from django.db.models.functions import TruncDate
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
from .fcm_notifications import token_for_log
from .cloudinary_images import (
    ClientPhotoError,
    delete_client_reference_photo,
    get_client_reference_photo_public_id,
    upload_client_reference_photo,
    validate_client_photo,
)
from .models import Cliente, FCMToken, Lugar, Pedido, PedidoHistorial, PushSubscription
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
REFERENCIA_CASA_INCOMPLETA_MENSAJE = (
    'Para guardar referencia de casa debes capturar ubicación GPS y tomar foto.'
)


def leer_ubicacion_cliente_post(post_data):

    latitud_texto = post_data.get('latitud', '').strip()
    longitud_texto = post_data.get('longitud', '').strip()
    referencia_ubicacion = post_data.get(
        'referencia_ubicacion',
        ''
    ).strip()

    if not latitud_texto and not longitud_texto:
        return None, None, referencia_ubicacion, ''

    if not latitud_texto or not longitud_texto:
        logger.warning(
            'Coordenadas incompletas recibidas. latitud=%s longitud=%s',
            latitud_texto,
            longitud_texto
        )
        return None, None, referencia_ubicacion, (
            'Latitud y longitud deben registrarse juntas.'
        )

    try:
        latitud = Decimal(latitud_texto)
        longitud = Decimal(longitud_texto)
    except InvalidOperation:
        logger.warning(
            'Coordenadas invalidas recibidas. latitud=%s longitud=%s',
            latitud_texto,
            longitud_texto
        )
        return None, None, referencia_ubicacion, (
            'Las coordenadas de ubicación no son válidas.'
        )

    if latitud < Decimal('-90') or latitud > Decimal('90'):
        logger.warning('Latitud fuera de rango recibida. latitud=%s', latitud)
        return None, None, referencia_ubicacion, (
            'La latitud debe estar entre -90 y 90.'
        )

    if longitud < Decimal('-180') or longitud > Decimal('180'):
        logger.warning('Longitud fuera de rango recibida. longitud=%s', longitud)
        return None, None, referencia_ubicacion, (
            'La longitud debe estar entre -180 y 180.'
        )

    return latitud, longitud, referencia_ubicacion, ''


def validar_referencia_casa_completa(latitud, longitud, foto_referencia):

    tiene_gps = latitud is not None and longitud is not None
    tiene_foto = bool(
        foto_referencia
        and getattr(foto_referencia, 'name', '')
        and getattr(foto_referencia, 'size', 0) > 0
    )

    if tiene_gps != tiene_foto:
        return REFERENCIA_CASA_INCOMPLETA_MENSAJE

    return ''


def mensaje_validacion_modelo(error):

    if hasattr(error, 'message_dict'):
        mensajes = []

        for lista_mensajes in error.message_dict.values():
            mensajes.extend(lista_mensajes)

        if mensajes:
            return ' '.join(mensajes)

    return 'Revisa los datos ingresados.'


def puede_repartidor_actualizar_foto_cliente(cliente):

    if not cliente.foto_referencia_public_id:
        return True

    if not cliente.foto_referencia_actualizada_en:
        return True

    limite = timezone.now() - timedelta(hours=24)
    return cliente.foto_referencia_actualizada_en >= limite


def aplicar_foto_referencia_cliente(cliente, uploaded_file, usuario=None):

    if not uploaded_file:
        return ''

    public_id_anterior = get_client_reference_photo_public_id(cliente)
    resultado = upload_client_reference_photo(uploaded_file)
    cliente.foto_referencia_url = resultado['secure_url']
    cliente.foto_referencia_public_id = resultado['public_id']
    cliente.foto_referencia_actualizada_en = timezone.now()
    cliente.foto_referencia_actualizada_por = (
        usuario if usuario and usuario.is_authenticated else None
    )
    return public_id_anterior


def limpiar_foto_nueva_si_falla_guardado(public_id_nuevo, public_id_anterior=''):

    if public_id_nuevo and public_id_nuevo != public_id_anterior:
        delete_client_reference_photo(public_id_nuevo)


def limpiar_foto_referencia_cliente(cliente):

    public_id_anterior = get_client_reference_photo_public_id(cliente)
    cliente.foto_referencia_url = ''
    cliente.foto_referencia_public_id = ''
    cliente.foto_referencia_actualizada_en = None
    cliente.foto_referencia_actualizada_por = None
    return public_id_anterior


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
        f"pago {pedido.get_metodo_pago_display()}, "
        f"lugar {pedido.lugar or 'Sin lugar'}, "
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


def campos_estado_pedido():

    return [
        'estado',
        'fecha_pendiente',
        'usuario_pendiente',
        'fecha_asignado',
        'usuario_asignado',
        'fecha_en_ruta',
        'usuario_en_ruta',
        'fecha_entrega',
        'usuario_entrega',
        'fecha_cancelacion',
        'usuario_cancelacion',
        'fecha_reprogramado',
        'usuario_reprogramado',
        'fecha_estado_actualizado',
        'usuario_estado_actualizado',
    ]


def tipo_historial_estado(nuevo_estado):

    return {
        Pedido.PENDIENTE: PedidoHistorial.REVERTIDO,
        Pedido.ASIGNADO: PedidoHistorial.ASIGNADO,
        Pedido.EN_RUTA: PedidoHistorial.EN_RUTA,
        Pedido.ENTREGADO: PedidoHistorial.ENTREGADO,
        Pedido.CANCELADO: PedidoHistorial.CANCELADO,
        Pedido.REPROGRAMADO: PedidoHistorial.REPROGRAMADO,
    }.get(nuevo_estado, PedidoHistorial.EDITADO)


def cambiar_estado_operativo(pedido, nuevo_estado, usuario, descripcion):

    estado_anterior = pedido.estado
    pedido.registrar_estado(nuevo_estado, usuario)
    pedido.save(update_fields=campos_estado_pedido())
    registrar_historial_pedido(
        pedido,
        usuario,
        tipo_historial_estado(nuevo_estado),
        descripcion,
        valor_anterior=estado_anterior,
        valor_nuevo=nuevo_estado
    )


def programar_notificacion_asignacion(pedido, repartidor_anterior=None):

    pedido_id = pedido.id

    def enviar_notificacion():
        pedido_actualizado = Pedido.objects.select_related(
            'cliente',
            'repartidor'
        ).get(id=pedido_id)
        send_order_assignment_push(
            pedido_actualizado,
            previous_repartidor=repartidor_anterior
        )

    transaction.on_commit(enviar_notificacion)


def cambios_edicion_pedido(pedido, nuevos_valores):

    campos = [
        ('cantidad_bidones', 'Cantidad'),
        ('precio_unitario', 'Precio unitario'),
        ('total', 'Total'),
        ('metodo_pago', 'Metodo de pago'),
        ('lugar', 'Lugar'),
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

    logger.info(
        'POST /webpush/subscribe/ recibido. usuario=%s',
        request.user.id
    )

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


@login_required
@require_POST
def fcm_register_token(request):

    logger.info(
        'POST /fcm/register-token/ recibido. usuario=%s username=%s',
        request.user.id,
        request.user.get_username()
    )

    try:
        data = json.loads(request.body.decode('utf-8'))
    except (json.JSONDecodeError, UnicodeDecodeError):
        logger.warning(
            'FCM register-token con JSON invalido. usuario=%s',
            request.user.id
        )
        return JsonResponse({'error': 'JSON invalido.'}, status=400)

    token = data.get('token', '').strip()
    platform = data.get('platform', 'android').strip() or 'android'
    device_id = data.get('deviceId', '').strip()

    if not token:
        logger.warning('FCM register-token sin token. usuario=%s', request.user.id)
        return JsonResponse({'error': 'Token requerido.'}, status=400)

    if len(platform) > 30:
        platform = platform[:30]

    if len(device_id) > 120:
        device_id = device_id[:120]

    logger.info(
        'FCM token recibido. usuario=%s username=%s token=%s '
        'platform=%s device_id=%s',
        request.user.id,
        request.user.get_username(),
        token_for_log(token),
        platform,
        device_id or 'sin-device-id'
    )

    fcm_token, created = FCMToken.objects.update_or_create(
        token=token,
        defaults={
            'user': request.user,
            'platform': platform,
            'device_id': device_id,
            'is_active': True,
            'last_error': '',
        }
    )
    active_count = FCMToken.objects.filter(
        user=request.user,
        is_active=True
    ).count()

    logger.info(
        'FCM token guardado. usuario=%s token_id=%s created=%s '
        'token=%s platform=%s device_id=%s tokens_activos_usuario=%s',
        request.user.id,
        fcm_token.id,
        created,
        token_for_log(token),
        platform,
        device_id or 'sin-device-id',
        active_count
    )

    return JsonResponse({
        'ok': True,
        'token_id': fcm_token.id,
        'user_id': request.user.id,
        'platform': platform,
    })


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

    return Pedido.ESTADOS_ACTIVOS


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

    if pedido.esta_activo():
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


def leer_metodo_pago_post(post_data, default=Pedido.PAGO_PENDIENTE):

    metodo_pago = post_data.get('metodo_pago', default).strip().upper()
    metodos_validos = {valor for valor, etiqueta in Pedido.METODOS_PAGO}

    if metodo_pago not in metodos_validos:
        return None

    return metodo_pago


def leer_metodo_pago_entrega_post(post_data):

    metodo_pago = post_data.get('metodo_pago', '').strip().upper()
    metodos_validos = {
        valor
        for valor, etiqueta in Pedido.METODOS_COBRO
        if valor != Pedido.PAGO_PENDIENTE
    } | {Pedido.PAGO_FIADO}

    if metodo_pago not in metodos_validos:
        return None

    return metodo_pago


def leer_lugar_activo_post(post_data, campo='lugar'):

    lugar_id = post_data.get(campo, '').strip()

    if not lugar_id:
        return None, ''

    if not lugar_id.isdigit():
        return None, 'Lugar seleccionado inválido.'

    lugar = Lugar.objects.filter(id=int(lugar_id), activo=True).first()

    if not lugar:
        return None, 'El lugar seleccionado no está activo.'

    return lugar, ''


def asignar_lugar_para_finalizar(pedido, post_data):

    if pedido.lugar_id:
        return False, ''

    lugar, error_lugar = leer_lugar_activo_post(post_data)

    if error_lugar:
        return False, error_lugar

    if lugar is None:
        return False, 'Selecciona el lugar del pedido antes de finalizar.'

    pedido.lugar = lugar
    return True, ''


def totales_por_metodo_pago(pedidos_entregados):

    montos = {
        Pedido.PAGO_EFECTIVO: Decimal('0.00'),
        Pedido.PAGO_YAPE: Decimal('0.00'),
        Pedido.PAGO_PLIN: Decimal('0.00'),
        Pedido.PAGO_TRANSFERENCIA: Decimal('0.00'),
        Pedido.PAGO_FIADO: Decimal('0.00'),
        Pedido.PAGO_PENDIENTE: Decimal('0.00'),
    }

    for item in pedidos_entregados.values('metodo_pago').annotate(total=Sum('total')):
        if item['metodo_pago'] in montos:
            montos[item['metodo_pago']] = item['total'] or Decimal('0.00')

    return {
        'efectivo': montos[Pedido.PAGO_EFECTIVO],
        'yape': montos[Pedido.PAGO_YAPE],
        'plin': montos[Pedido.PAGO_PLIN],
        'transferencia': montos[Pedido.PAGO_TRANSFERENCIA],
        'fiado': montos[Pedido.PAGO_FIADO],
        'pendiente': montos[Pedido.PAGO_PENDIENTE],
        'cobrado': (
            montos[Pedido.PAGO_EFECTIVO]
            + montos[Pedido.PAGO_YAPE]
            + montos[Pedido.PAGO_PLIN]
            + montos[Pedido.PAGO_TRANSFERENCIA]
        ),
    }


def totales_cobros_periodo(
    inicio_periodo,
    fin_periodo,
    lugar=None,
    repartidor=None
):

    metodos_cobro = [valor for valor, etiqueta in Pedido.METODOS_COBRO]
    pagos_inmediatos = Pedido.objects.filter(
        estado=Pedido.ENTREGADO,
        metodo_pago__in=metodos_cobro,
        fecha_entrega__date__range=(inicio_periodo, fin_periodo)
    )
    pagos_fiados = Pedido.objects.filter(
        estado=Pedido.ENTREGADO,
        metodo_pago=Pedido.PAGO_FIADO,
        metodo_pago_final__in=metodos_cobro,
        fecha_pago__date__range=(inicio_periodo, fin_periodo)
    )

    if lugar:
        pagos_inmediatos = pagos_inmediatos.filter(lugar=lugar)
        pagos_fiados = pagos_fiados.filter(lugar=lugar)

    if repartidor:
        pagos_inmediatos = pagos_inmediatos.filter(repartidor=repartidor)
        pagos_fiados = pagos_fiados.filter(repartidor=repartidor)

    montos = {}
    for metodo in metodos_cobro:
        inmediato = pagos_inmediatos.filter(
            metodo_pago=metodo
        ).aggregate(total=Sum('total'))['total'] or Decimal('0.00')
        posterior = pagos_fiados.filter(
            metodo_pago_final=metodo
        ).aggregate(total=Sum('total'))['total'] or Decimal('0.00')
        montos[metodo] = inmediato + posterior

    return {
        'efectivo': montos[Pedido.PAGO_EFECTIVO],
        'yape': montos[Pedido.PAGO_YAPE],
        'plin': montos[Pedido.PAGO_PLIN],
        'transferencia': montos[Pedido.PAGO_TRANSFERENCIA],
        'cobrado': sum(montos.values(), Decimal('0.00')),
    }


def fiados_por_cobrar(lugar=None):

    pedidos = Pedido.objects.filter(
        estado=Pedido.ENTREGADO,
        metodo_pago=Pedido.PAGO_FIADO,
        fecha_pago__isnull=True
    ).select_related(
        'cliente',
        'repartidor',
        'lugar'
    ).order_by('fecha_entrega')

    if lugar:
        pedidos = pedidos.filter(lugar=lugar)

    return pedidos


def resumen_pagos_por_lugar(pedidos_entregados):

    resumen = []
    for lugar in Lugar.objects.order_by('orden', 'nombre'):
        pedidos_lugar = pedidos_entregados.filter(lugar=lugar)
        if pedidos_lugar.exists():
            pagos_lugar = totales_por_metodo_pago(pedidos_lugar)
            resumen.append({
                'nombre': lugar.nombre,
                'total': pedidos_lugar.aggregate(total=Sum('total'))['total'] or 0,
                'bidones': pedidos_lugar.aggregate(
                    total=Sum('cantidad_bidones')
                )['total'] or 0,
                'entregados': pedidos_lugar.count(),
                **pagos_lugar,
            })

    pedidos_sin_lugar = pedidos_entregados.filter(lugar__isnull=True)
    if pedidos_sin_lugar.exists():
        pagos_sin_lugar = totales_por_metodo_pago(pedidos_sin_lugar)
        resumen.append({
            'nombre': 'Sin lugar asignado',
            'total': pedidos_sin_lugar.aggregate(total=Sum('total'))['total'] or 0,
            'bidones': pedidos_sin_lugar.aggregate(
                total=Sum('cantidad_bidones')
            )['total'] or 0,
            'entregados': pedidos_sin_lugar.count(),
            **pagos_sin_lugar,
        })

    return resumen


def actualizar_estados_clientes():

    hoy = timezone.localdate()
    ahora = timezone.now()
    clientes = Cliente.objects.annotate(
        pedidos_pendientes_vencidos=Count(
            'pedido',
            filter=Q(
                pedido__estado__in=Pedido.ESTADOS_ACTIVOS,
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
def dashboard(request, template_name='core/dashboard.html'):

    actualizar_estados_clientes()

    total_clientes = Cliente.objects.count()
    total_pedidos = Pedido.objects.count()

    pedidos_entregados = Pedido.objects.filter(
        estado=Pedido.ENTREGADO
    ).count()

    pedidos_pendientes = Pedido.objects.filter(
        estado__in=Pedido.ESTADOS_ACTIVOS
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
        estado__in=Pedido.ESTADOS_ACTIVOS,
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
        fecha_entrega__date=hoy
    ).aggregate(
        total=Sum('total')
    )['total'] or 0
    pagos_hoy = totales_por_metodo_pago(
        Pedido.objects.filter(
            estado=Pedido.ENTREGADO,
            fecha_entrega__date=hoy
        )
    )
    cobros_hoy = totales_cobros_periodo(hoy, hoy)
    total_por_cobrar = fiados_por_cobrar().aggregate(
        total=Sum('total')
    )['total'] or 0

    pedidos_hoy = Pedido.objects.filter(
        fecha_pedido__date=hoy
    ).count()

    bidones_hoy = Pedido.objects.filter(
        estado=Pedido.ENTREGADO,
        fecha_entrega__date=hoy
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
            fecha_entrega__date=fecha
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
            fecha_entrega__date=fecha_comparacion
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
        fecha_entrega__date__gte=inicio_mes
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
        'efectivo_hoy': cobros_hoy['efectivo'],
        'yape_hoy': cobros_hoy['yape'],
        'fiado_hoy': pagos_hoy['fiado'],
        'total_por_cobrar': total_por_cobrar,
        'total_cobrado_hoy': cobros_hoy['cobrado'],
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

    return render(request, template_name, context)

@login_required
@clientes_required
def lista_clientes(request, template_name='core/clientes.html'):

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

    return render(request, template_name, context)


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
            | Q(referencia_ubicacion__icontains=busqueda)
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
            'referencia_ubicacion': cliente.referencia_ubicacion or '',
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
            fecha_entrega__date=fecha
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
            fecha_entrega__date=fecha_iteracion
        )

        detalle_diario_mes.append({
            'fecha': fecha_iteracion,
            'ingresos': pedidos_dia.aggregate(total=Sum('total'))['total'] or 0,
            'bidones': pedidos_dia.aggregate(total=Sum('cantidad_bidones'))['total'] or 0,
            'pedidos': pedidos_dia.count(),
        })

        fecha_iteracion += timedelta(days=1)

    cancelados_hoy = Pedido.objects.filter(
        estado=Pedido.CANCELADO,
        fecha_cancelacion__date=hoy
    ).count()
    pagos_hoy = totales_por_metodo_pago(
        Pedido.objects.filter(
            estado=Pedido.ENTREGADO,
            fecha_entrega__date=hoy
        )
    )
    cobros_hoy = totales_cobros_periodo(hoy, hoy)
    pedidos_hoy = Pedido.objects.filter(
        fecha_pedido__date=hoy
    ).select_related(
        'cliente',
        'lugar',
        'repartidor'
    ).order_by('-fecha_pedido')

    context = {
        'ventas_hoy': comparativo_ventas[0]['ingresos'],
        'bidones_hoy': comparativo_ventas[0]['bidones'],
        'pedidos_entregados_hoy': comparativo_ventas[0]['pedidos'],
        'cancelados_hoy': cancelados_hoy,
        'cobrado_hoy': cobros_hoy['cobrado'],
        'fiado_hoy': pagos_hoy['fiado'],
        'comparativo_ventas': comparativo_ventas,
        'lectura_comparativo': lectura_comparativo,
        'detalle_diario_mes': detalle_diario_mes,
        'detalle_diario_reciente': detalle_diario_mes[-7:],
        'pedidos_hoy': pedidos_hoy,
        'total_pedidos_hoy': pedidos_hoy.count(),
        'hoy': hoy,
    }

    return render(
        request,
        'core/reporte_diario.html',
        context
    )


@login_required
@secretaria_required
def reporte_semanal(request):

    hoy = timezone.localdate()
    fecha_param = request.GET.get('semana', '').strip()

    try:
        fecha_referencia = datetime.strptime(fecha_param, '%Y-%m-%d').date()
    except ValueError:
        fecha_referencia = hoy

    inicio_semana = fecha_referencia - timedelta(days=fecha_referencia.weekday())
    fin_semana = inicio_semana + timedelta(days=6)
    lugares = Lugar.objects.order_by('orden', 'nombre')
    lugar_actual = None
    lugar_id = request.GET.get('lugar', '').strip()

    if lugar_id.isdigit():
        lugar_actual = lugares.filter(id=int(lugar_id)).first()

    pedidos_entregados = Pedido.objects.filter(
        estado=Pedido.ENTREGADO,
        fecha_entrega__date__range=(inicio_semana, fin_semana)
    ).select_related('cliente', 'lugar').order_by('-fecha_entrega')

    if lugar_actual:
        pedidos_entregados = pedidos_entregados.filter(lugar=lugar_actual)

    total_cancelados = Pedido.objects.filter(
        estado=Pedido.CANCELADO,
        fecha_cancelacion__date__range=(inicio_semana, fin_semana)
    )

    if lugar_actual:
        total_cancelados = total_cancelados.filter(lugar=lugar_actual)

    total_cancelados = total_cancelados.count()
    pagos_semana = totales_por_metodo_pago(pedidos_entregados)
    cobros_semana = totales_cobros_periodo(
        inicio_semana,
        fin_semana,
        lugar_actual
    )
    cuentas_por_cobrar = pedidos_entregados.filter(
        metodo_pago=Pedido.PAGO_FIADO,
        fecha_pago__isnull=True
    ).aggregate(total=Sum('total'))['total'] or 0

    context = {
        'hoy': hoy,
        'inicio_semana': inicio_semana,
        'fin_semana': fin_semana,
        'lugares': lugares,
        'lugar_actual': lugar_actual,
        'pedidos_entregados': pedidos_entregados,
        'total_ventas': pedidos_entregados.aggregate(total=Sum('total'))['total'] or 0,
        'total_bidones': pedidos_entregados.aggregate(
            total=Sum('cantidad_bidones')
        )['total'] or 0,
        'total_entregados': pedidos_entregados.count(),
        'total_cancelados': total_cancelados,
        'efectivo': cobros_semana['efectivo'],
        'yape': cobros_semana['yape'],
        'plin': cobros_semana['plin'],
        'transferencia': cobros_semana['transferencia'],
        'fiado': pagos_semana['fiado'],
        'pendiente': pagos_semana['pendiente'],
        'cobrado': cobros_semana['cobrado'],
        'cuentas_por_cobrar': cuentas_por_cobrar,
    }

    return render(request, 'core/reporte_semanal.html', context)


@login_required
def pagos(request):

    if not (
        puede_ver_crm_operativo(request.user)
        or es_jefe_repartidores(request.user)
    ):
        return redirect(destino_usuario(request.user))

    hoy = timezone.localdate()
    fecha_param = request.GET.get('fecha', '').strip()
    periodo = request.GET.get('periodo', 'dia').strip()

    try:
        fecha_referencia = datetime.strptime(fecha_param, '%Y-%m-%d').date()
    except ValueError:
        fecha_referencia = hoy

    if periodo == 'semana':
        inicio_periodo = fecha_referencia - timedelta(
            days=fecha_referencia.weekday()
        )
        fin_periodo = inicio_periodo + timedelta(days=6)
    else:
        periodo = 'dia'
        inicio_periodo = fecha_referencia
        fin_periodo = fecha_referencia

    lugares = Lugar.objects.order_by('orden', 'nombre')
    lugar_actual = None
    lugar_id = request.GET.get('lugar', '').strip()

    if lugar_id.isdigit():
        lugar_actual = lugares.filter(id=int(lugar_id)).first()

    entregados_hoy = Pedido.objects.filter(
        estado=Pedido.ENTREGADO,
        fecha_entrega__date=hoy
    )
    entregados_periodo = Pedido.objects.filter(
        estado=Pedido.ENTREGADO,
        fecha_entrega__date__range=(inicio_periodo, fin_periodo)
    ).select_related('cliente', 'lugar').order_by('-fecha_entrega')

    if lugar_actual:
        entregados_periodo = entregados_periodo.filter(lugar=lugar_actual)

    ventas_hoy = totales_por_metodo_pago(entregados_hoy)
    ventas_periodo = totales_por_metodo_pago(entregados_periodo)
    pagos_hoy = totales_cobros_periodo(hoy, hoy)
    pagos_periodo = totales_cobros_periodo(
        inicio_periodo,
        fin_periodo,
        lugar_actual
    )
    pendientes_pago = entregados_periodo.filter(
        metodo_pago=Pedido.PAGO_PENDIENTE
    )
    fiados_pendientes = list(fiados_por_cobrar(lugar_actual))
    for pedido in fiados_pendientes:
        pedido.dias_pendiente = max(
            0,
            (hoy - timezone.localdate(pedido.fecha_entrega)).days
        )

    context = {
        'hoy': hoy,
        'fecha_referencia': fecha_referencia,
        'periodo': periodo,
        'inicio_periodo': inicio_periodo,
        'fin_periodo': fin_periodo,
        'lugares': lugares,
        'lugar_actual': lugar_actual,
        'efectivo_hoy': pagos_hoy['efectivo'],
        'yape_hoy': pagos_hoy['yape'],
        'plin_hoy': pagos_hoy['plin'],
        'transferencia_hoy': pagos_hoy['transferencia'],
        'fiado_hoy': ventas_hoy['fiado'],
        'total_cobrado_hoy': pagos_hoy['cobrado'],
        'efectivo_periodo': pagos_periodo['efectivo'],
        'yape_periodo': pagos_periodo['yape'],
        'plin_periodo': pagos_periodo['plin'],
        'transferencia_periodo': pagos_periodo['transferencia'],
        'fiado_periodo': ventas_periodo['fiado'],
        'pendiente_periodo': ventas_periodo['pendiente'],
        'total_cobrado_periodo': pagos_periodo['cobrado'],
        'pendientes_pago': pendientes_pago,
        'total_pendientes_pago': pendientes_pago.count(),
        'fiados_pendientes': fiados_pendientes,
        'total_fiados_pendientes': len(fiados_pendientes),
        'total_por_cobrar': sum(
            (pedido.total for pedido in fiados_pendientes),
            Decimal('0.00')
        ),
        'resumen_lugares': resumen_pagos_por_lugar(entregados_periodo),
    }

    return render(request, 'core/pagos.html', context)


@login_required
@require_POST
@transaction.atomic
def marcar_fiado_pagado(request, pedido_id):

    if not (
        puede_ver_crm_operativo(request.user)
        or es_jefe_repartidores(request.user)
    ):
        return redirect(destino_usuario(request.user))

    pedido = get_object_or_404(
        Pedido,
        id=pedido_id,
        estado=Pedido.ENTREGADO,
        metodo_pago=Pedido.PAGO_FIADO,
        fecha_pago__isnull=True
    )
    metodo_pago_final = request.POST.get('metodo_pago_final', '').strip().upper()
    metodos_cobro = {valor for valor, etiqueta in Pedido.METODOS_COBRO}

    if metodo_pago_final not in metodos_cobro:
        messages.error(request, 'Método de pago final inválido.')
        return redirect('pagos')

    pedido.metodo_pago_final = metodo_pago_final
    pedido.fecha_pago = timezone.now()
    pedido.usuario_pago = request.user
    pedido.save(update_fields=[
        'metodo_pago_final',
        'fecha_pago',
        'usuario_pago',
    ])
    registrar_historial_pedido(
        pedido,
        request.user,
        PedidoHistorial.EDITADO,
        'Cobro posterior de pedido fiado registrado.',
        valor_anterior='Fiado pendiente',
        valor_nuevo=pedido.get_metodo_pago_final_display()
    )
    messages.success(request, 'Pago del fiado registrado correctamente.')

    return redirect('pagos')


@login_required
@secretaria_required
def lugares(request):

    if request.method == 'POST':
        nombre = request.POST.get('nombre', '').strip()
        orden = request.POST.get('orden', '0').strip()

        if not nombre:
            messages.error(request, 'El nombre del lugar es obligatorio.')
            return redirect('lugares')

        try:
            orden = max(0, int(orden or 0))
        except ValueError:
            messages.error(request, 'El orden debe ser un número válido.')
            return redirect('lugares')

        if Lugar.objects.filter(nombre__iexact=nombre).exists():
            messages.error(request, 'Ya existe un lugar con ese nombre.')
            return redirect('lugares')

        Lugar.objects.create(nombre=nombre, orden=orden)
        messages.success(request, 'Lugar registrado correctamente.')
        return redirect('lugares')

    return render(
        request,
        'core/lugares.html',
        {'lugares': Lugar.objects.order_by('orden', 'nombre')}
    )


@login_required
@secretaria_required
@require_POST
def editar_lugar(request, lugar_id):

    lugar = get_object_or_404(Lugar, id=lugar_id)
    nombre = request.POST.get('nombre', '').strip()
    orden = request.POST.get('orden', '0').strip()

    if not nombre:
        messages.error(request, 'El nombre del lugar es obligatorio.')
        return redirect('lugares')

    try:
        orden = max(0, int(orden or 0))
    except ValueError:
        messages.error(request, 'El orden debe ser un número válido.')
        return redirect('lugares')

    if Lugar.objects.filter(nombre__iexact=nombre).exclude(id=lugar.id).exists():
        messages.error(request, 'Ya existe un lugar con ese nombre.')
        return redirect('lugares')

    lugar.nombre = nombre
    lugar.orden = orden
    lugar.save(update_fields=['nombre', 'orden'])
    messages.success(request, 'Lugar actualizado correctamente.')
    return redirect('lugares')


@login_required
@secretaria_required
@require_POST
def cambiar_estado_lugar(request, lugar_id):

    lugar = get_object_or_404(Lugar, id=lugar_id)
    lugar.activo = not lugar.activo
    lugar.save(update_fields=['activo'])
    messages.success(request, 'Estado del lugar actualizado correctamente.')
    return redirect('lugares')


@login_required
@clientes_required
def detalle_cliente(request, cliente_id):

    cliente = get_object_or_404(
        Cliente,
        id=cliente_id
    )

    pedidos = Pedido.objects.filter(
        cliente=cliente
    ).select_related('lugar').order_by('-fecha_pedido')

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
        'lugares': Lugar.objects.filter(activo=True).order_by('orden', 'nombre'),
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

    public_id_foto = get_client_reference_photo_public_id(cliente)
    cliente.delete()
    delete_client_reference_photo(public_id_foto)

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
        lugar_id_post = request.POST.get('lugar', '').strip()
        if 'lugar' not in request.POST or (
            cliente.lugar_id and lugar_id_post == str(cliente.lugar_id)
        ):
            lugar, error_lugar = cliente.lugar, ''
        else:
            lugar, error_lugar = leer_lugar_activo_post(request.POST)
        foto_referencia = request.FILES.get('foto_referencia')
        eliminar_foto_referencia = (
            request.POST.get('eliminar_foto_referencia') == '1'
        )
        latitud, longitud, referencia_ubicacion, error_ubicacion = (
            leer_ubicacion_cliente_post(request.POST)
        )

        if error_lugar:
            messages.error(request, error_lugar)
            return redirect('editar_cliente', cliente_id=cliente.id)

        if error_ubicacion:
            messages.error(request, error_ubicacion)
            return redirect(
                'editar_cliente',
                cliente_id=cliente.id
            )

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

        if foto_referencia:
            try:
                validate_client_photo(foto_referencia)
                foto_referencia.seek(0)
            except ClientPhotoError as error:
                messages.error(request, str(error))
                return redirect(
                    'editar_cliente',
                    cliente_id=cliente.id
                )

        cliente.nombre = nombre
        cliente.telefono = telefono
        cliente.direccion = direccion
        cliente.referencia = referencia
        cliente.lugar = lugar
        cliente.latitud = latitud
        cliente.longitud = longitud
        cliente.referencia_ubicacion = referencia_ubicacion

        public_id_anterior = ''
        public_id_nuevo = ''

        try:
            cliente.full_clean()
            public_id_anterior = aplicar_foto_referencia_cliente(
                cliente,
                foto_referencia,
                request.user
            )
            if foto_referencia:
                public_id_nuevo = cliente.foto_referencia_public_id
            if eliminar_foto_referencia and not foto_referencia:
                public_id_anterior = limpiar_foto_referencia_cliente(cliente)
            cliente.save()
            if (
                public_id_anterior
                and public_id_anterior != cliente.foto_referencia_public_id
            ):
                delete_client_reference_photo(public_id_anterior)
        except ValidationError as error:
            logger.warning(
                'Validacion de cliente fallida al editar. cliente_id=%s error=%s',
                cliente.id,
                error
            )
            messages.error(request, mensaje_validacion_modelo(error))
            return redirect(
                'editar_cliente',
                cliente_id=cliente.id
            )
        except ClientPhotoError as error:
            messages.error(request, str(error))
            return redirect(
                'editar_cliente',
                cliente_id=cliente.id
            )
        except Exception:
            limpiar_foto_nueva_si_falla_guardado(
                public_id_nuevo,
                public_id_anterior
            )
            raise

        messages.success(
            request,
            'Cliente actualizado correctamente.'
        )

        return redirect(
            'detalle_cliente',
            cliente_id=cliente.id
        )

    context = {
        'cliente': cliente,
        'lugares': Lugar.objects.filter(activo=True).order_by('orden', 'nombre')
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
        lugar, error_lugar = leer_lugar_activo_post(request.POST)
        foto_referencia = request.FILES.get('foto_referencia')
        latitud, longitud, referencia_ubicacion, error_ubicacion = (
            leer_ubicacion_cliente_post(request.POST)
        )

        if error_lugar:
            messages.error(request, error_lugar)
            return redirect('registrar_cliente')

        if error_ubicacion:
            messages.error(request, error_ubicacion)
            return redirect('registrar_cliente')

        error_referencia_casa = validar_referencia_casa_completa(
            latitud,
            longitud,
            foto_referencia
        )

        if error_referencia_casa:
            messages.error(request, error_referencia_casa)
            return redirect('registrar_cliente')

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

        if foto_referencia:
            try:
                validate_client_photo(foto_referencia)
                foto_referencia.seek(0)
            except ClientPhotoError as error:
                messages.error(request, str(error))
                return redirect('registrar_cliente')

        cliente = Cliente(
            nombre=nombre,
            telefono=telefono,
            direccion=direccion,
            referencia=referencia,
            lugar=lugar,
            latitud=latitud,
            longitud=longitud,
            referencia_ubicacion=referencia_ubicacion
        )

        public_id_nuevo = ''

        try:
            cliente.full_clean()
            aplicar_foto_referencia_cliente(cliente, foto_referencia, request.user)
            if foto_referencia:
                public_id_nuevo = cliente.foto_referencia_public_id
            cliente.save()
        except ValidationError as error:
            logger.warning(
                'Validacion de cliente fallida al registrar. error=%s',
                error
            )
            messages.error(request, mensaje_validacion_modelo(error))
            return redirect('registrar_cliente')
        except ClientPhotoError as error:
            messages.error(request, str(error))
            return redirect('registrar_cliente')
        except Exception:
            limpiar_foto_nueva_si_falla_guardado(public_id_nuevo)
            raise

        messages.success(request, 'Cliente registrado correctamente.')
        return redirect('lista_clientes')

    return render(
        request,
        'core/registrar_cliente.html',
        {'lugares': Lugar.objects.filter(activo=True).order_by('orden', 'nombre')}
    )

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
    lugares = Lugar.objects.filter(activo=True).order_by('orden', 'nombre')
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
        metodo_pago = leer_metodo_pago_post(request.POST)
        lugar, error_lugar = leer_lugar_activo_post(request.POST)
        estado = request.POST.get('estado', '').strip()
        observacion = request.POST.get('observacion', '').strip()
        fecha_programada = request.POST.get('fecha_programada', '').strip()

        estados_validos = [
            Pedido.PENDIENTE,
            Pedido.ASIGNADO,
            Pedido.ENTREGADO,
            Pedido.CANCELADO,
            Pedido.REPROGRAMADO,
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

        if metodo_pago is None:
            messages.error(request, 'Método de pago inválido.')
            return redirect('registrar_pedido')

        if (
            estado == Pedido.ENTREGADO
            and leer_metodo_pago_entrega_post(request.POST) is None
        ):
            messages.error(
                request,
                'Selecciona un método de pago antes de marcar como entregado.'
            )
            return redirect('registrar_pedido')

        if error_lugar:
            messages.error(request, error_lugar)
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
        estado_inicial = estado

        if estado == Pedido.PENDIENTE and repartidor:
            estado_inicial = Pedido.ASIGNADO

        if estado == Pedido.ASIGNADO and not repartidor:
            messages.error(request, 'Un pedido asignado debe tener repartidor.')
            return redirect('registrar_pedido')

        if lugar is None:
            lugar = cliente.lugar

        if (
            estado_inicial in [Pedido.ENTREGADO, Pedido.CANCELADO]
            and lugar is None
        ):
            messages.error(
                request,
                'Selecciona el lugar del pedido antes de finalizar.'
            )
            return redirect('registrar_pedido')

        if estado == Pedido.REPROGRAMADO and fecha_programada_valor <= timezone.localdate():
            messages.error(request, 'Un pedido reprogramado debe tener fecha futura.')
            return redirect('registrar_pedido')

        with transaction.atomic():
            pedido = Pedido(
                cliente=cliente,
                repartidor=repartidor,
                cantidad_bidones=cantidad_bidones,
                precio_unitario=precio_unitario,
                total=total,
                metodo_pago=metodo_pago,
                lugar=lugar,
                observacion=observacion,
                fecha_programada=fecha_programada_valor,
            )
            pedido.registrar_estado(estado_inicial, request.user)
            pedido.save()
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
        'lugares': lugares,
    }

    return render(request, 'core/registrar_pedido.html', context)


@login_required
@transaction.atomic
def editar_pedido(request, pedido_id):

    if not puede_editar_pedido(request.user):
        messages.error(request, 'No tienes permiso para editar pedidos.')
        return redirect(destino_usuario(request.user))

    pedido = get_object_or_404(
        Pedido.objects.select_related(
            'cliente',
            'repartidor',
            'lugar'
        ),
        id=pedido_id
    )
    origen = (
        request.POST.get('origen', '').strip()
        or request.GET.get('origen', '').strip()
    )
    repartidores = repartidores_disponibles()
    lugares = Lugar.objects.filter(activo=True).order_by('orden', 'nombre')

    if not pedido.esta_activo():
        messages.error(
            request,
            'Solo se pueden editar pedidos activos.'
        )
        return redireccion_edicion_pedido(origen, pedido)

    if request.method == 'POST':
        cantidad_bidones = request.POST.get('cantidad_bidones', '').strip()
        precio_unitario = request.POST.get('precio_unitario', '').strip()
        metodo_pago = leer_metodo_pago_post(
            request.POST,
            pedido.metodo_pago
        )
        lugar_id_post = request.POST.get('lugar', '').strip()
        if 'lugar' not in request.POST or (
            pedido.lugar_id and lugar_id_post == str(pedido.lugar_id)
        ):
            lugar, error_lugar = pedido.lugar, ''
        else:
            lugar, error_lugar = leer_lugar_activo_post(request.POST)
        fecha_programada = request.POST.get('fecha_programada', '').strip()
        observacion = request.POST.get('observacion', '').strip()
        repartidor_id = request.POST.get('repartidor', '').strip()

        if not cantidad_bidones or not precio_unitario:
            messages.error(
                request,
                'Cantidad y precio unitario son obligatorios.'
            )
            return redirect('editar_pedido', pedido_id=pedido.id)

        if metodo_pago is None:
            messages.error(request, 'Método de pago inválido.')
            return redirect('editar_pedido', pedido_id=pedido.id)

        if error_lugar:
            messages.error(request, error_lugar)
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
            'metodo_pago': metodo_pago,
            'lugar': lugar,
            'fecha_programada': fecha_programada_valor,
            'observacion': observacion,
        }
        cambios = cambios_edicion_pedido(pedido, nuevos_valores)
        fecha_programada_anterior = pedido.fecha_programada
        repartidor_anterior = pedido.repartidor
        repartidor_cambio = pedido.repartidor_id != (
            repartidor.id if repartidor else None
        )
        fecha_reprogramada = fecha_programada_anterior != fecha_programada_valor

        pedido.cantidad_bidones = cantidad_bidones
        pedido.precio_unitario = precio_unitario
        pedido.total = nuevo_total
        pedido.metodo_pago = metodo_pago
        pedido.lugar = lugar
        pedido.fecha_programada = fecha_programada_valor
        pedido.observacion = observacion
        pedido.repartidor = repartidor
        estado_anterior_edicion = pedido.estado

        if fecha_reprogramada:
            pedido.registrar_estado(Pedido.REPROGRAMADO, request.user)
        elif repartidor and repartidor_cambio:
            pedido.registrar_estado(Pedido.ASIGNADO, request.user)
        pedido.save(
            update_fields=[
                'cantidad_bidones',
                'precio_unitario',
                'total',
                'metodo_pago',
                'lugar',
                'fecha_programada',
                'observacion',
                'repartidor',
                *campos_estado_pedido(),
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
            programar_notificacion_asignacion(
                pedido,
                repartidor_anterior=repartidor_anterior
            )

        if fecha_reprogramada:
            registrar_historial_pedido(
                pedido,
                request.user,
                PedidoHistorial.REPROGRAMADO,
                'Pedido reprogramado por cambio de fecha.',
                valor_anterior=estado_anterior_edicion,
                valor_nuevo=Pedido.REPROGRAMADO
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
        'lugares': lugares,
    }

    return render(
        request,
        'core/editar_pedido.html',
        context
    )


@login_required
@secretaria_required
def lista_pedidos(request, template_name='core/pedidos.html'):

    hoy = timezone.localdate()
    manana = hoy + timedelta(days=1)

    pedidos = Pedido.objects.select_related(
        'cliente',
        'repartidor',
        'lugar'
    ).annotate(
        prioridad_operativa=Case(
            When(
                estado__in=Pedido.ESTADOS_ACTIVOS,
                fecha_programada__lt=hoy,
                then=Value(1)
            ),
            When(
                estado=Pedido.EN_RUTA,
                then=Value(2)
            ),
            When(
                estado__in=Pedido.ESTADOS_ACTIVOS,
                fecha_programada=hoy,
                then=Value(3)
            ),
            When(
                estado__in=Pedido.ESTADOS_ACTIVOS,
                fecha_programada__isnull=True,
                then=Value(3)
            ),
            When(
                estado__in=Pedido.ESTADOS_ACTIVOS,
                fecha_programada=manana,
                then=Value(4)
            ),
            When(
                estado__in=Pedido.ESTADOS_ACTIVOS,
                fecha_programada__gt=manana,
                then=Value(5)
            ),
            When(
                estado=Pedido.ENTREGADO,
                then=Value(6)
            ),
            When(
                estado=Pedido.CANCELADO,
                then=Value(7)
            ),
            default=Value(8),
            output_field=IntegerField()
        ),
        fecha_programada_orden=Case(
            When(
                estado__in=Pedido.ESTADOS_ACTIVOS,
                then=F('fecha_programada')
            ),
            output_field=DateField()
        ),
        fecha_pedido_pendiente_orden=Case(
            When(
                estado__in=Pedido.ESTADOS_ACTIVOS,
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
            valor for valor, _etiqueta in Pedido.ESTADOS_PEDIDO
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
                estado__in=Pedido.ESTADOS_ACTIVOS
            ).filter(
                Q(fecha_programada=hoy)
                | Q(fecha_programada__isnull=True)
            )
        elif filtro == 'atrasados':
            pedidos = pedidos.filter(
                estado__in=Pedido.ESTADOS_ACTIVOS,
                fecha_programada__lt=hoy
            )
        elif filtro == 'programados':
            pedidos = pedidos.filter(
                estado__in=Pedido.ESTADOS_ACTIVOS,
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
        'lugares': Lugar.objects.filter(activo=True).order_by('orden', 'nombre'),
    }

    return render(request, template_name, context)


@login_required
@repartidor_required
def pedidos_repartidor(request, template_name='core/pedidos_repartidor.html'):

    hoy = timezone.localdate()

    pedidos_base = Pedido.objects.filter(
        estado__in=estados_activos_repartidor(),
        repartidor=request.user
    ).select_related(
        'cliente',
        'lugar'
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
    total_dinero_hoy = totales_cobros_periodo(
        hoy,
        hoy,
        repartidor=request.user
    )['cobrado']
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
        'lugares': Lugar.objects.filter(activo=True).order_by('orden', 'nombre'),
    }

    return render(
        request,
        template_name,
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
        estado__in=[
            Pedido.PENDIENTE,
            Pedido.ASIGNADO,
            Pedido.EN_RUTA,
            Pedido.REPROGRAMADO,
        ],
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
        estado__in=Pedido.ESTADOS_ACTIVOS,
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
            estado__in=Pedido.ESTADOS_ACTIVOS,
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
            'dinero_hoy': totales_cobros_periodo(
                hoy,
                hoy,
                repartidor=repartidor
            )['cobrado'],
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
@transaction.atomic
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
        estado__in=Pedido.ESTADOS_ACTIVOS
    )

    repartidor_anterior = pedido.repartidor
    pedido.repartidor = repartidor
    pedido.registrar_estado(Pedido.ASIGNADO, request.user)
    pedido.save(update_fields=['repartidor', *campos_estado_pedido()])
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
        (
            PedidoHistorial.REASIGNADO
            if repartidor_anterior
            else PedidoHistorial.ASIGNADO
        ),
        (
            'Repartidor asignado: '
            f'{nombre_repartidor_historial(repartidor_anterior)} -> '
            f'{nombre_repartidor_historial(repartidor)}.'
        ),
        valor_anterior=nombre_repartidor_historial(repartidor_anterior),
        valor_nuevo=nombre_repartidor_historial(repartidor)
    )
    programar_notificacion_asignacion(
        pedido,
        repartidor_anterior=repartidor_anterior
    )

    messages.success(
        request,
        'Pedido asignado correctamente.'
    )

    return redirect('panel_jefe_repartidores')


@login_required
@repartidor_required
@require_POST
@transaction.atomic
def marcar_pedido_entregado_repartidor(request, pedido_id):

    pedido = get_object_or_404(
        Pedido,
        id=pedido_id,
        estado__in=estados_activos_repartidor(),
        repartidor=request.user
    )

    metodo_pago = leer_metodo_pago_entrega_post(request.POST)

    if metodo_pago is None:
        messages.error(
            request,
            'Selecciona un método de pago antes de marcar como entregado.'
        )
        return redirect('pedidos_repartidor')

    lugar_asignado, error_lugar = asignar_lugar_para_finalizar(
        pedido,
        request.POST
    )

    if error_lugar:
        messages.error(request, error_lugar)
        return redirect('pedidos_repartidor')

    update_fields = ['repartidor', 'metodo_pago', *campos_estado_pedido()]

    if lugar_asignado:
        update_fields = ['lugar', *update_fields]

    pedido.metodo_pago = metodo_pago

    pedido.repartidor = request.user
    estado_anterior = pedido.estado
    pedido.registrar_estado(Pedido.ENTREGADO, request.user)
    pedido.save(update_fields=update_fields)
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
@require_POST
@transaction.atomic
def marcar_pedido_en_ruta_repartidor(request, pedido_id):

    pedido = get_object_or_404(
        Pedido,
        id=pedido_id,
        estado__in=[
            Pedido.ASIGNADO,
            Pedido.REPROGRAMADO,
            Pedido.PENDIENTE,
        ],
        repartidor=request.user
    )

    cambiar_estado_operativo(
        pedido,
        Pedido.EN_RUTA,
        request.user,
        'Pedido marcado en ruta por repartidor.'
    )

    messages.success(
        request,
        'Pedido marcado como en ruta.'
    )

    return redirect('pedidos_repartidor')


@login_required
@repartidor_required
def nuevo_pedido_repartidor(request):

    clientes = Cliente.objects.filter(
        activo=True
    ).order_by('nombre')
    lugares = Lugar.objects.filter(activo=True).order_by('orden', 'nombre')
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
        metodo_pago = leer_metodo_pago_post(request.POST)
        lugar, error_lugar = leer_lugar_activo_post(request.POST)
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

        if cantidad_bidones > 100:
            messages.error(request, 'La cantidad máxima permitida es 100 bidones.')
            return redirect('nuevo_pedido_repartidor')

        if metodo_pago is None:
            messages.error(request, 'Método de pago inválido.')
            return redirect('nuevo_pedido_repartidor')

        if (
            entregar_ahora
            and leer_metodo_pago_entrega_post(request.POST) is None
        ):
            messages.error(
                request,
                'Selecciona un método de pago antes de marcar como entregado.'
            )
            return redirect('nuevo_pedido_repartidor')

        if error_lugar:
            messages.error(request, error_lugar)
            return redirect('nuevo_pedido_repartidor')

        if precio_unitario < Decimal('1.00'):
            messages.error(request, 'El precio unitario mínimo permitido es S/ 1.00.')
            return redirect('nuevo_pedido_repartidor')

        if precio_unitario > Decimal('50.00'):
            messages.error(request, 'El precio unitario máximo permitido es S/ 50.00.')
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

        if lugar is None:
            lugar = cliente.lugar

        total = cantidad_bidones * precio_unitario
        estado = Pedido.ENTREGADO if entregar_ahora else Pedido.ASIGNADO

        if entregar_ahora and lugar is None:
            messages.error(
                request,
                'Selecciona el lugar del pedido antes de finalizar.'
            )
            return redirect('nuevo_pedido_repartidor')

        with transaction.atomic():
            pedido = Pedido(
                cliente=cliente,
                repartidor=request.user,
                cantidad_bidones=cantidad_bidones,
                precio_unitario=precio_unitario,
                total=total,
                metodo_pago=metodo_pago,
                lugar=lugar,
                observacion=observacion,
                fecha_programada=fecha_programada_valor,
            )
            pedido.registrar_estado(estado, request.user)
            pedido.save()
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
                'Pedido rápido registrado como asignado.'
            )

        return redirect('pedidos_repartidor')

    context = {
        'clientes': clientes,
        'precio_sugerido': precio_unitario_rapido(None),
        'hoy': timezone.localdate(),
        'cliente_preseleccionado': cliente_preseleccionado,
        'lugares': lugares,
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
        lugar, error_lugar = leer_lugar_activo_post(request.POST)
        foto_referencia = request.FILES.get('foto_referencia')
        latitud, longitud, referencia_ubicacion, error_ubicacion = (
            leer_ubicacion_cliente_post(request.POST)
        )

        if error_lugar:
            messages.error(request, error_lugar)
            return redirect('nuevo_cliente_repartidor')

        if error_ubicacion:
            messages.error(request, error_ubicacion)
            return redirect('nuevo_cliente_repartidor')

        error_referencia_casa = validar_referencia_casa_completa(
            latitud,
            longitud,
            foto_referencia
        )

        if error_referencia_casa:
            messages.error(request, error_referencia_casa)
            return redirect('nuevo_cliente_repartidor')

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

        if foto_referencia:
            try:
                validate_client_photo(foto_referencia)
                foto_referencia.seek(0)
            except ClientPhotoError as error:
                messages.error(request, str(error))
                return redirect('nuevo_cliente_repartidor')

        cliente = Cliente(
            nombre=nombre,
            telefono=telefono,
            direccion=direccion,
            referencia=referencia,
            lugar=lugar,
            latitud=latitud,
            longitud=longitud,
            referencia_ubicacion=referencia_ubicacion
        )

        public_id_nuevo = ''

        try:
            cliente.full_clean()
            aplicar_foto_referencia_cliente(cliente, foto_referencia, request.user)
            if foto_referencia:
                public_id_nuevo = cliente.foto_referencia_public_id
            cliente.save()
        except ValidationError as error:
            logger.warning(
                'Validacion de cliente repartidor fallida. error=%s',
                error
            )
            messages.error(request, mensaje_validacion_modelo(error))
            return redirect('nuevo_cliente_repartidor')
        except ClientPhotoError as error:
            messages.error(request, str(error))
            return redirect('nuevo_cliente_repartidor')
        except Exception:
            limpiar_foto_nueva_si_falla_guardado(public_id_nuevo)
            raise

        messages.success(
            request,
            'Cliente registrado correctamente.'
        )
        return redirect('pedidos_repartidor')

    return render(
        request,
        'core/nuevo_cliente_repartidor.html',
        {'lugares': Lugar.objects.filter(activo=True).order_by('orden', 'nombre')}
    )


@login_required
@repartidor_required
def actualizar_referencia_cliente_repartidor(request, cliente_id):

    limite_reciente = timezone.now() - timedelta(hours=24)
    cliente = get_object_or_404(
        Cliente.objects.filter(
            id=cliente_id,
            pedido__repartidor=request.user
        ).filter(
            Q(pedido__estado__in=Pedido.ESTADOS_ACTIVOS)
            | Q(pedido__fecha_entrega__gte=limite_reciente)
            | Q(pedido__fecha_cancelacion__gte=limite_reciente)
        ).distinct()
    )
    puede_cambiar_foto = puede_repartidor_actualizar_foto_cliente(cliente)

    if request.method == 'POST':
        foto_referencia = request.FILES.get('foto_referencia')
        latitud, longitud, referencia_ubicacion, error_ubicacion = (
            leer_ubicacion_cliente_post(request.POST)
        )

        if error_ubicacion:
            messages.error(request, error_ubicacion)
            return redirect(
                'actualizar_referencia_cliente_repartidor',
                cliente_id=cliente.id
            )

        error_referencia_casa = validar_referencia_casa_completa(
            latitud,
            longitud,
            foto_referencia
        )

        if error_referencia_casa:
            messages.error(request, error_referencia_casa)
            return redirect(
                'actualizar_referencia_cliente_repartidor',
                cliente_id=cliente.id
            )

        if foto_referencia and not puede_cambiar_foto:
            messages.error(
                request,
                'La foto de referencia solo puede cambiarse durante las primeras 24 horas.'
            )
            return redirect(
                'actualizar_referencia_cliente_repartidor',
                cliente_id=cliente.id
            )

        if foto_referencia:
            try:
                validate_client_photo(foto_referencia)
                foto_referencia.seek(0)
            except ClientPhotoError as error:
                messages.error(request, str(error))
                return redirect(
                    'actualizar_referencia_cliente_repartidor',
                    cliente_id=cliente.id
                )

        cliente.latitud = latitud
        cliente.longitud = longitud
        cliente.referencia_ubicacion = referencia_ubicacion

        public_id_anterior = ''
        public_id_nuevo = ''

        try:
            cliente.full_clean()
            public_id_anterior = aplicar_foto_referencia_cliente(
                cliente,
                foto_referencia,
                request.user
            )
            if foto_referencia:
                public_id_nuevo = cliente.foto_referencia_public_id
            cliente.save()
            if (
                public_id_anterior
                and public_id_anterior != cliente.foto_referencia_public_id
            ):
                delete_client_reference_photo(public_id_anterior)
        except ValidationError as error:
            logger.warning(
                'Validacion de referencia de casa fallida. cliente_id=%s error=%s',
                cliente.id,
                error
            )
            messages.error(request, mensaje_validacion_modelo(error))
            return redirect(
                'actualizar_referencia_cliente_repartidor',
                cliente_id=cliente.id
            )
        except ClientPhotoError as error:
            messages.error(request, str(error))
            return redirect(
                'actualizar_referencia_cliente_repartidor',
                cliente_id=cliente.id
            )
        except Exception:
            limpiar_foto_nueva_si_falla_guardado(
                public_id_nuevo,
                public_id_anterior
            )
            raise

        messages.success(request, 'Referencia de casa actualizada correctamente.')
        return redirect('pedidos_repartidor')

    context = {
        'cliente': cliente,
        'puede_cambiar_foto': puede_cambiar_foto,
    }

    return render(
        request,
        'core/actualizar_referencia_cliente_repartidor.html',
        context
    )


@login_required
@repartidor_required
@require_POST
@transaction.atomic
def cancelar_pedido_repartidor(request, pedido_id):

    pedido = get_object_or_404(
        Pedido,
        id=pedido_id,
        estado__in=estados_activos_repartidor(),
        repartidor=request.user
    )

    lugar_asignado, error_lugar = asignar_lugar_para_finalizar(
        pedido,
        request.POST
    )

    if error_lugar:
        messages.error(request, error_lugar)
        return redirect('pedidos_repartidor')

    estado_anterior = pedido.estado
    pedido.repartidor = request.user
    pedido.registrar_estado(Pedido.CANCELADO, request.user)
    update_fields = ['repartidor', *campos_estado_pedido()]

    if lugar_asignado:
        update_fields = ['lugar', *update_fields]

    pedido.save(update_fields=update_fields)
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
@transaction.atomic
def cambiar_estado_pedido(request, pedido_id, nuevo_estado):

    pedido = get_object_or_404(
        Pedido,
        id=pedido_id
    )

    estados_validos = [
        Pedido.ASIGNADO,
        Pedido.EN_RUTA,
        Pedido.ENTREGADO,
        Pedido.CANCELADO,
        Pedido.REPROGRAMADO,
    ]

    if nuevo_estado not in estados_validos:
        messages.error(request, 'Estado inválido.')
        return redirect('lista_pedidos')

    if not pedido.esta_activo():
        messages.error(request, 'Solo se pueden modificar pedidos activos desde esta acción.')
        return redirect('lista_pedidos')

    if pedido.estado == nuevo_estado:
        messages.error(request, 'El pedido ya tiene ese estado.')
        return redirect('lista_pedidos')

    update_fields = campos_estado_pedido()

    if nuevo_estado in [Pedido.ASIGNADO, Pedido.EN_RUTA] and not pedido.repartidor_id:
        messages.error(
            request,
            'Debes asignar un repartidor antes de usar este estado.'
        )
        return redirect('lista_pedidos')

    if nuevo_estado in [Pedido.ENTREGADO, Pedido.CANCELADO]:
        lugar_asignado, error_lugar = asignar_lugar_para_finalizar(
            pedido,
            request.POST
        )

        if error_lugar:
            messages.error(request, error_lugar)
            return redirect('lista_pedidos')

        if lugar_asignado:
            update_fields = ['lugar', *update_fields]

    if nuevo_estado == Pedido.REPROGRAMADO:
        fecha_programada = request.POST.get('fecha_programada', '').strip()

        if not fecha_programada:
            messages.error(
                request,
                'Debes indicar una nueva fecha para reprogramar el pedido.'
            )
            return redirect('lista_pedidos')

        try:
            nueva_fecha_programada = datetime.strptime(
                fecha_programada,
                '%Y-%m-%d'
            ).date()
        except ValueError:
            messages.error(request, 'Fecha programada inválida.')
            return redirect('lista_pedidos')

        if nueva_fecha_programada <= timezone.localdate():
            messages.error(
                request,
                'La nueva fecha de reprogramación debe ser futura.'
            )
            return redirect('lista_pedidos')

        if nueva_fecha_programada == pedido.fecha_programada:
            messages.error(
                request,
                'La nueva fecha debe ser diferente a la fecha actual.'
            )
            return redirect('lista_pedidos')

        pedido.fecha_programada = nueva_fecha_programada
        update_fields = ['fecha_programada', *update_fields]

    if nuevo_estado == Pedido.ENTREGADO:
        metodo_pago = leer_metodo_pago_entrega_post(request.POST)

        if metodo_pago is None:
            messages.error(
                request,
                'Selecciona un método de pago antes de marcar como entregado.'
            )
            return redirect('lista_pedidos')

        pedido.metodo_pago = metodo_pago
        update_fields = ['metodo_pago', *update_fields]

    estado_anterior = pedido.estado
    pedido.registrar_estado(nuevo_estado, request.user)
    pedido.save(update_fields=update_fields)
    tipo_historial = tipo_historial_estado(nuevo_estado)
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

    elif nuevo_estado == Pedido.ASIGNADO:
        messages.success(
            request,
            'Pedido marcado como ASIGNADO correctamente.'
        )

    elif nuevo_estado == Pedido.EN_RUTA:
        messages.success(
            request,
            'Pedido marcado como EN RUTA correctamente.'
        )

    elif nuevo_estado == Pedido.REPROGRAMADO:
        messages.success(
            request,
            'Pedido marcado como REPROGRAMADO correctamente.'
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
@transaction.atomic
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
    pedido.fecha_entrega = None
    pedido.usuario_entrega = None
    pedido.fecha_pago = None
    pedido.metodo_pago_final = None
    pedido.usuario_pago = None
    pedido.registrar_estado(Pedido.PENDIENTE, request.user)
    pedido.save(update_fields=[
        'fecha_entrega',
        'usuario_entrega',
        'fecha_pago',
        'metodo_pago_final',
        'usuario_pago',
        *campos_estado_pedido(),
    ])
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
    zona_horaria_operativa = timezone.get_default_timezone()

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

    inicio_mes = datetime(anio, mes, 1).date()
    fin_mes = datetime(anio, mes, monthrange(anio, mes)[1]).date()
    pedidos_mes = Pedido.objects.annotate(
        fecha_pedido_local=TruncDate(
            'fecha_pedido',
            tzinfo=zona_horaria_operativa
        )
    ).filter(
        fecha_pedido_local__range=(inicio_mes, fin_mes)
    )

    pedidos_entregados = Pedido.objects.filter(
        estado=Pedido.ENTREGADO
    ).annotate(
        fecha_entrega_local=TruncDate(
            'fecha_entrega',
            tzinfo=zona_horaria_operativa
        )
    ).filter(
        fecha_entrega_local__range=(inicio_mes, fin_mes)
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
        estado__in=Pedido.ESTADOS_ACTIVOS
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

    dia_mas_ventas = pedidos_entregados.values(
        fecha=F('fecha_entrega_local')
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
            fecha_entrega_local=fecha
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

