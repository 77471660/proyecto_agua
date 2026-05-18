from django.contrib.auth.decorators import user_passes_test


LOGIN_URL = '/login/'
ADMIN_GROUP_NAMES = ['ADMIN', 'Administrador']
REPARTIDOR_GROUP_NAMES = ['Repartidores', 'Repartidor']
JEFE_REPARTIDORES_GROUP = 'JefeRepartidores'


def es_repartidor(user):
    return (
        user.is_authenticated
        and user.is_active
        and user.groups.filter(name__in=REPARTIDOR_GROUP_NAMES).exists()
    )


def es_secretaria(user):
    return (
        user.is_authenticated
        and user.is_active
        and user.groups.filter(name='Secretaria').exists()
    )


def es_admin_o_superuser(user):
    return (
        user.is_authenticated
        and user.is_active
        and (
            user.is_superuser
            or user.groups.filter(name__in=ADMIN_GROUP_NAMES).exists()
        )
    )


def es_jefe_repartidores(user):
    return (
        user.is_authenticated
        and user.is_active
        and user.groups.filter(name=JEFE_REPARTIDORES_GROUP).exists()
    )


def puede_ver_panel_repartidor(user):
    return es_repartidor(user) or es_jefe_repartidores(user)


def puede_ver_panel_jefe_repartidores(user):
    return es_admin_o_superuser(user) or es_jefe_repartidores(user)


def puede_ver_crm_operativo(user):
    return es_admin_o_superuser(user) or es_secretaria(user)


def puede_ver_clientes(user):
    return (
        puede_ver_crm_operativo(user)
        or puede_ver_panel_repartidor(user)
    )


repartidor_required = user_passes_test(
    puede_ver_panel_repartidor,
    login_url=LOGIN_URL
)


secretaria_required = user_passes_test(
    puede_ver_crm_operativo,
    login_url=LOGIN_URL
)


jefe_repartidores_required = user_passes_test(
    puede_ver_panel_jefe_repartidores,
    login_url=LOGIN_URL
)


clientes_required = user_passes_test(
    puede_ver_clientes,
    login_url=LOGIN_URL
)
