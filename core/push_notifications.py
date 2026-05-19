import json
import logging
import time

from django.conf import settings

from .models import PushSubscription


logger = logging.getLogger(__name__)
EXPECTED_VAPID_SUBJECT = 'mailto:villarcalderondaniel@gmail.com'
PUSH_ICON_URL = '/static/img/icons/android-chrome-192x192.png'
PUSH_BADGE_URL = '/static/img/icons/notification-bidon.png'


def endpoint_for_log(endpoint):

    if not endpoint:
        return 'sin-endpoint'

    if len(endpoint) <= 24:
        return endpoint

    return f'{endpoint[:16]}...{endpoint[-8:]}'


def vapid_status():

    subject = settings.WEBPUSH_VAPID_SUBJECT

    return {
        'has_public_key': bool(settings.WEBPUSH_VAPID_PUBLIC_KEY),
        'has_private_key': bool(settings.WEBPUSH_VAPID_PRIVATE_KEY),
        'has_subject': bool(subject),
        'subject_is_mailto': subject.startswith('mailto:'),
        'subject_is_expected': subject == EXPECTED_VAPID_SUBJECT,
    }


def payload_for_log(payload):

    return {
        'title': payload.get('title'),
        'body': payload.get('body'),
        'url': payload.get('url'),
        'tag': payload.get('tag'),
        'icon': payload.get('icon'),
        'badge': payload.get('badge'),
        'renotify': payload.get('renotify'),
        'requireInteraction': payload.get('requireInteraction'),
        'timestamp': payload.get('timestamp'),
        'vibrate': payload.get('vibrate'),
    }


def webpush_configured():

    return (
        bool(settings.WEBPUSH_VAPID_PUBLIC_KEY)
        and bool(settings.WEBPUSH_VAPID_PRIVATE_KEY)
        and bool(settings.WEBPUSH_VAPID_SUBJECT)
    )


def send_push_to_user(user, payload):

    if not webpush_configured():
        logger.warning(
            'Web Push no configurado. status=%s usuario_destino=%s',
            vapid_status(),
            user.id
        )
        return

    try:
        from pywebpush import WebPushException, webpush
    except ImportError:
        logger.exception('pywebpush no esta instalado. usuario_destino=%s', user.id)
        return

    subscriptions = PushSubscription.objects.filter(
        user=user,
        is_active=True
    )
    subscription_count = subscriptions.count()

    logger.info(
        'Web Push envio iniciado. usuario_destino=%s suscripciones_activas=%s '
        'tag=%s status_vapid=%s payload=%s',
        user.id,
        subscription_count,
        payload.get('tag'),
        vapid_status(),
        payload_for_log(payload)
    )

    if subscription_count == 0:
        logger.warning(
            'Web Push sin suscripciones activas. usuario_destino=%s tag=%s',
            user.id,
            payload.get('tag')
        )
        return

    for subscription in subscriptions:
        endpoint_log = endpoint_for_log(subscription.endpoint)
        subscription_info = {
            'endpoint': subscription.endpoint,
            'keys': {
                'p256dh': subscription.p256dh,
                'auth': subscription.auth,
            },
        }

        try:
            logger.info(
                'Web Push llamando pywebpush. usuario_destino=%s '
                'subscription_id=%s endpoint=%s',
                user.id,
                subscription.id,
                endpoint_log
            )
            webpush(
                subscription_info=subscription_info,
                data=json.dumps(payload),
                vapid_private_key=settings.WEBPUSH_VAPID_PRIVATE_KEY,
                vapid_claims={'sub': settings.WEBPUSH_VAPID_SUBJECT},
                ttl=3600,
            )
            if subscription.last_error:
                subscription.last_error = ''
                subscription.save(update_fields=['last_error', 'updated_at'])
            logger.info(
                'Web Push enviado correctamente. usuario_destino=%s '
                'subscription_id=%s endpoint=%s',
                user.id,
                subscription.id,
                endpoint_log
            )
        except WebPushException as exc:
            response = getattr(exc, 'response', None)
            status_code = (
                getattr(response, 'status_code', None)
                or getattr(response, 'status', None)
            )
            error_text = str(exc)[:500]
            logger.exception(
                'Error pywebpush. usuario_destino=%s subscription_id=%s '
                'endpoint=%s status_code=%s exception=%s',
                user.id,
                subscription.id,
                endpoint_log,
                status_code,
                error_text
            )
            subscription.last_error = (
                f'pywebpush status={status_code or "sin-status"} '
                f'error={error_text}'
            )[:1000]
            update_fields = ['last_error', 'updated_at']

            if status_code in {404, 410}:
                subscription.is_active = False
                update_fields.append('is_active')
                logger.warning(
                    'Web Push suscripcion invalida desactivada. '
                    'usuario_destino=%s subscription_id=%s endpoint=%s '
                    'status_code=%s',
                    user.id,
                    subscription.id,
                    endpoint_log,
                    status_code
                )

            subscription.save(update_fields=update_fields)
        except Exception as exc:
            error_text = str(exc)[:500]
            logger.exception(
                'Error inesperado enviando Web Push. usuario_destino=%s '
                'subscription_id=%s endpoint=%s exception=%s',
                user.id,
                subscription.id,
                endpoint_log,
                error_text
            )
            subscription.last_error = f'inesperado error={error_text}'[:1000]
            subscription.save(update_fields=['last_error', 'updated_at'])


def send_order_assignment_push(pedido, previous_repartidor=None):

    if not pedido.repartidor:
        logger.info(
            'Web Push omitido: pedido sin repartidor. pedido_id=%s',
            pedido.id
        )
        return

    is_reassignment = (
        previous_repartidor
        and previous_repartidor.id != pedido.repartidor_id
    )

    if is_reassignment:
        title = 'Pedido reasignado'
        body = f'Pedido #{pedido.id} fue reasignado a tu reparto.'
        tag_suffix = 'reasignado'
        renotify = True
    else:
        title = 'Pedido asignado'
        body = f'Pedido #{pedido.id} fue asignado a tu reparto.'
        tag_suffix = 'asignado'
        renotify = False

    url = f'/pedidos/repartidor/#pedido-{pedido.id}'
    payload = {
        'title': title,
        'body': body,
        'icon': PUSH_ICON_URL,
        'badge': PUSH_BADGE_URL,
        'url': url,
        'tag': f'pedido-{pedido.id}-{tag_suffix}',
        'renotify': renotify,
        'requireInteraction': is_reassignment,
        'timestamp': int(time.time() * 1000),
        'vibrate': [180, 90, 180, 90, 240] if is_reassignment else [160, 80, 160],
    }

    logger.info(
        'Web Push preparando notificacion de pedido. pedido_id=%s '
        'repartidor_destino=%s repartidor_anterior=%s titulo=%s payload=%s',
        pedido.id,
        pedido.repartidor_id,
        previous_repartidor.id if previous_repartidor else None,
        title,
        payload_for_log(payload)
    )
    send_push_to_user(pedido.repartidor, payload)
