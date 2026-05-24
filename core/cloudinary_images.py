from io import BytesIO
import logging
import re
from urllib.parse import unquote, urlparse

import cloudinary
import cloudinary.uploader
from django.conf import settings
from PIL import Image, ImageOps, UnidentifiedImageError


logger = logging.getLogger(__name__)

ALLOWED_CONTENT_TYPES = {
    'image/jpeg',
    'image/jpg',
    'image/png',
    'image/webp',
}
MAX_UPLOAD_SIZE = 5 * 1024 * 1024
MAX_IMAGE_WIDTH = 900
JPEG_QUALITY = 68
CLOUDINARY_CLIENTES_FOLDER = 'aquasmart/clientes'
_CLOUDINARY_UPLOAD_MARKER = '/upload/'


class ClientPhotoError(Exception):
    pass


def cloudinary_configured():
    return all([
        settings.CLOUDINARY_CLOUD_NAME,
        settings.CLOUDINARY_API_KEY,
        settings.CLOUDINARY_API_SECRET,
    ])


def configure_cloudinary():
    cloudinary.config(
        cloud_name=settings.CLOUDINARY_CLOUD_NAME,
        api_key=settings.CLOUDINARY_API_KEY,
        api_secret=settings.CLOUDINARY_API_SECRET,
        secure=True,
    )


def validate_client_photo(uploaded_file):
    if not uploaded_file:
        return

    if uploaded_file.size > MAX_UPLOAD_SIZE:
        raise ClientPhotoError('La foto no debe superar 5 MB.')

    content_type = (uploaded_file.content_type or '').lower()

    if content_type not in ALLOWED_CONTENT_TYPES:
        raise ClientPhotoError(
            'La foto debe estar en formato JPG, PNG o WebP.'
        )


def compress_client_photo(uploaded_file):
    try:
        uploaded_file.seek(0)
        image = Image.open(uploaded_file)
        image.verify()
        uploaded_file.seek(0)
        image = Image.open(uploaded_file)
        image = ImageOps.exif_transpose(image)
    except (UnidentifiedImageError, OSError) as error:
        logger.warning('Foto de referencia invalida. error=%s', error)
        raise ClientPhotoError('La foto seleccionada no es una imagen válida.')

    image = image.convert('RGB')

    if image.width > MAX_IMAGE_WIDTH:
        ratio = MAX_IMAGE_WIDTH / image.width
        new_height = max(1, int(image.height * ratio))
        image = image.resize(
            (MAX_IMAGE_WIDTH, new_height),
            Image.Resampling.LANCZOS
        )

    output = BytesIO()
    image.save(output, format='JPEG', quality=JPEG_QUALITY, optimize=True)
    output.seek(0)
    return output


def upload_client_reference_photo(uploaded_file):
    validate_client_photo(uploaded_file)

    if not cloudinary_configured():
        logger.error('Cloudinary no configurado para foto de cliente.')
        raise ClientPhotoError(
            'No se pudo subir la foto porque Cloudinary no está configurado.'
        )

    configure_cloudinary()
    compressed_file = compress_client_photo(uploaded_file)

    try:
        result = cloudinary.uploader.upload(
            compressed_file,
            folder=CLOUDINARY_CLIENTES_FOLDER,
            resource_type='image',
        )
    except Exception as error:
        logger.exception('Fallo al subir foto de cliente a Cloudinary.')
        raise ClientPhotoError(
            'No se pudo subir la foto. Inténtalo nuevamente.'
        ) from error

    secure_url = result.get('secure_url', '')
    public_id = result.get('public_id', '')

    if not secure_url or not public_id:
        logger.error('Cloudinary no devolvio URL o public_id. result=%s', result)
        raise ClientPhotoError(
            'No se pudo confirmar la foto subida. Inténtalo nuevamente.'
        )

    return {
        'secure_url': secure_url,
        'public_id': public_id,
    }


def extract_cloudinary_public_id(image_url):
    if not image_url:
        return ''

    parsed = urlparse(str(image_url))
    path = unquote(parsed.path or '')

    if _CLOUDINARY_UPLOAD_MARKER not in path:
        return ''

    public_path = path.split(_CLOUDINARY_UPLOAD_MARKER, 1)[1].lstrip('/')

    if not public_path:
        return ''

    parts = public_path.split('/')

    if parts and re.match(r'^v\d+$', parts[0]):
        parts = parts[1:]

    if not parts:
        return ''

    public_id = '/'.join(parts)

    if '.' in public_id.rsplit('/', 1)[-1]:
        public_id = public_id.rsplit('.', 1)[0]

    return public_id


def get_client_reference_photo_public_id(cliente):
    if not cliente:
        return ''

    public_id = getattr(cliente, 'foto_referencia_public_id', '') or ''

    if public_id:
        return public_id

    return extract_cloudinary_public_id(
        getattr(cliente, 'foto_referencia_url', '') or ''
    )


def delete_client_reference_photo(public_id):
    if not public_id or not cloudinary_configured():
        return

    configure_cloudinary()

    try:
        cloudinary.uploader.destroy(
            public_id,
            resource_type='image',
            invalidate=True,
        )
    except Exception as error:
        logger.warning(
            'No se pudo eliminar foto anterior de Cloudinary. public_id=%s error=%s',
            public_id,
            error
        )
