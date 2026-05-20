import json
import logging

from django.conf import settings

from .models import FCMToken
from .push_notifications import payload_for_log


logger = logging.getLogger(__name__)
_firebase_initialized = False


def token_for_log(token):

    if not token:
        return 'sin-token'

    if len(token) <= 24:
        return token

    return f'{token[:12]}...{token[-8:]}'


def firebase_configured():

    return bool(
        settings.FIREBASE_CREDENTIALS_JSON
        or settings.FIREBASE_CREDENTIALS_PATH
    )


def initialize_firebase():

    global _firebase_initialized

    if _firebase_initialized:
        return True

    if not firebase_configured():
        logger.warning('FCM no configurado: faltan credenciales Firebase.')
        return False

    try:
        import firebase_admin
        from firebase_admin import credentials
    except ImportError:
        logger.exception('firebase-admin no esta instalado.')
        return False

    try:
        if firebase_admin._apps:
            _firebase_initialized = True
            return True

        if settings.FIREBASE_CREDENTIALS_JSON:
            credential_data = json.loads(settings.FIREBASE_CREDENTIALS_JSON)
            credential = credentials.Certificate(credential_data)
        else:
            credential = credentials.Certificate(settings.FIREBASE_CREDENTIALS_PATH)

        firebase_admin.initialize_app(credential)
        _firebase_initialized = True
        logger.info('FCM inicializado correctamente.')
        return True
    except Exception as exc:
        logger.exception('No se pudo inicializar FCM. exception=%s', str(exc)[:500])
        return False


def is_invalid_fcm_error(exc):

    class_name = exc.__class__.__name__

    return class_name in {
        'InvalidArgumentError',
        'NotFoundError',
        'SenderIdMismatchError',
        'UnregisteredError',
    }


def send_fcm_to_user(user, payload):

    if not initialize_firebase():
        logger.warning(
            'FCM omitido por configuracion. usuario_destino=%s payload=%s',
            user.id,
            payload_for_log(payload)
        )
        return

    try:
        from firebase_admin import messaging
    except ImportError:
        logger.exception('firebase-admin messaging no esta disponible.')
        return

    tokens = FCMToken.objects.filter(
        user=user,
        is_active=True
    )
    token_count = tokens.count()
    logger.info(
        'FCM envio iniciado. usuario_destino=%s tokens_activos=%s payload=%s',
        user.id,
        token_count,
        payload_for_log(payload)
    )

    if token_count == 0:
        logger.warning(
            'FCM sin tokens activos. usuario_destino=%s tag=%s',
            user.id,
            payload.get('tag')
        )
        return

    for token in tokens:
        token_log = token_for_log(token.token)
        message = messaging.Message(
            token=token.token,
            notification=messaging.Notification(
                title=payload.get('title') or 'AquaSmart',
                body=payload.get('body') or 'Tienes una nueva actualizacion.',
            ),
            data={
                'url': payload.get('url') or '/pedidos/repartidor/',
                'tag': payload.get('tag') or 'aquasmart-fcm',
                'type': 'order_assignment',
            },
            android=messaging.AndroidConfig(
                priority='high',
                notification=messaging.AndroidNotification(
                    icon='ic_launcher',
                    color='#0d6efd',
                    tag=payload.get('tag') or 'aquasmart-fcm',
                ),
            ),
        )

        try:
            response = messaging.send(message)
            if token.last_error:
                token.last_error = ''
                token.save(update_fields=['last_error', 'updated_at'])
            logger.info(
                'FCM enviado correctamente. usuario_destino=%s token_id=%s '
                'token=%s response=%s',
                user.id,
                token.id,
                token_log,
                response
            )
        except Exception as exc:
            error_text = str(exc)[:500]
            logger.exception(
                'Error enviando FCM. usuario_destino=%s token_id=%s '
                'token=%s exception=%s',
                user.id,
                token.id,
                token_log,
                error_text
            )
            token.last_error = (
                f'fcm error={exc.__class__.__name__}: {error_text}'
            )[:1000]
            update_fields = ['last_error', 'updated_at']

            if is_invalid_fcm_error(exc):
                token.is_active = False
                update_fields.append('is_active')
                logger.warning(
                    'FCM token invalido desactivado. usuario_destino=%s '
                    'token_id=%s token=%s error=%s',
                    user.id,
                    token.id,
                    token_log,
                    exc.__class__.__name__
                )

            token.save(update_fields=update_fields)
