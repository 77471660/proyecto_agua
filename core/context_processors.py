from .auth_utils import (
    es_admin_o_superuser,
    es_jefe_repartidores,
    es_repartidor,
    es_secretaria,
)


def permisos_menu(request):

    user = request.user

    if not user.is_authenticated:
        return {}

    return {
        'menu_es_admin': es_admin_o_superuser(user),
        'menu_es_jefe_repartidores': es_jefe_repartidores(user),
        'menu_es_repartidor': es_repartidor(user),
        'menu_es_secretaria': es_secretaria(user),
        'menu_puede_crm_operativo': (
            es_admin_o_superuser(user)
            or es_secretaria(user)
        ),
        'menu_puede_mi_reparto': (
            es_repartidor(user)
            or es_jefe_repartidores(user)
        ),
    }
