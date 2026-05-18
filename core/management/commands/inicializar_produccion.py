import os

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = 'Inicializa grupos base y superusuario para produccion.'

    def handle(self, *args, **options):
        for group_name in ['ADMIN', 'Repartidores', 'JefeRepartidores']:
            _group, created = Group.objects.get_or_create(name=group_name)

            if created:
                self.stdout.write(
                    self.style.SUCCESS(f'Grupo creado: {group_name}')
                )
            else:
                self.stdout.write(f'Grupo existente: {group_name}')

        User = get_user_model()
        username = 'DANIEL'

        if User.objects.filter(username=username).exists():
            self.stdout.write(f'Superusuario existente: {username}')
            return

        password = os.environ.get('DJANGO_SUPERUSER_PASSWORD')

        if not password:
            raise CommandError(
                'Falta la variable de entorno DJANGO_SUPERUSER_PASSWORD.'
            )

        User.objects.create_superuser(
            username=username,
            password=password,
        )

        self.stdout.write(
            self.style.SUCCESS(f'Superusuario creado: {username}')
        )
