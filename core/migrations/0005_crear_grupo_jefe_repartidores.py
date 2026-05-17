from django.db import migrations


def crear_grupo_jefe_repartidores(apps, schema_editor):
    Group = apps.get_model('auth', 'Group')
    Group.objects.get_or_create(name='JefeRepartidores')


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0004_pedido_fecha_cancelacion_pedido_fecha_entrega_and_more'),
        ('auth', '0012_alter_user_first_name_max_length'),
    ]

    operations = [
        migrations.RunPython(
            crear_grupo_jefe_repartidores,
            migrations.RunPython.noop
        ),
    ]
