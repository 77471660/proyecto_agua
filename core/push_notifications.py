import json
import logging

from django.conf import settings

from .models import PushSubscription


logger = logging.getLogger(__name__)


def webpush_configured():

    return (
        bool(settings.WEBPUSH_VAPID_PUBLIC_KEY)
        and bool(settings.WEBPUSH_VAPID_PRIVATE_KEY)
        and bool(settings.WEBPUSH_VAPID_SUBJECT)
    )


def send_push_to_user(user, payload):

    if not webpush_configured():
        logger.info('Web Push no configurado: faltan credenciales VAPID.')
        return

    try:
        from pywebpush import WebPushException, webpush
    except ImportError:
        logger.exception('pywebpush no esta instalado.')
        return

    subscriptions = PushSubscription.objects.filter(
        user=user,
        is_active=True
    )

    for subscription in subscriptions:
        subscription_info = {
            'endpoint': subscription.endpoint,
            'keys': {
                'p256dh': subscription.p256dh,
                'auth': subscription.auth,
            },
        }

        try:
            webpush(
                subscription_info=subscription_info,
                data=json.dumps(payload),
                vapid_private_key=settings.WEBPUSH_VAPID_PRIVATE_KEY,
                vapid_claims={'sub': settings.WEBPUSH_VAPID_SUBJECT},
                ttl=3600,
            )
        except WebPushException as exc:
            status_code = getattr(getattr(exc, 'response', None), 'status_code', None)
            logger.warning(
                'Error enviando Web Push a usuario %s: %s',
                user.id,
                exc
            )

            if status_code in {404, 410}:
                subscription.is_active = False
                subscription.save(update_fields=['is_active', 'updated_at'])
        except Exception:
            logger.exception(
                'Error inesperado enviando Web Push a usuario %s.',
                user.id
            )


def send_order_assignment_push(pedido, previous_repartidor=None):

    if not pedido.repartidor:
        return

    if previous_repartidor and previous_repartidor.id != pedido.repartidor_id:
        title = 'Pedido reasignado'
        body = f'Pedido #{pedido.id} reasignado a tu reparto.'
    else:
        title = 'Pedido asignado'
        body = f'Pedido #{pedido.id} asignado a tu reparto.'

    send_push_to_user(
        pedido.repartidor,
        {
            'title': title,
            'body': body,
            'url': '/pedidos/repartidor/',
            'tag': f'pedido-{pedido.id}-asignacion',
        }
    )
