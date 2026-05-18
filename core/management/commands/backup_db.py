from shutil import copy2

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone


class Command(BaseCommand):
    help = 'Crea una copia de seguridad local de db.sqlite3.'

    def handle(self, *args, **options):
        database_path = settings.BASE_DIR / 'db.sqlite3'
        backups_dir = settings.BASE_DIR / 'backups'

        if not database_path.exists():
            raise CommandError(
                f'No se encontró la base de datos: {database_path}'
            )

        backups_dir.mkdir(exist_ok=True)

        timestamp = timezone.localtime().strftime('%Y-%m-%d_%H-%M-%S')
        backup_path = backups_dir / f'db_{timestamp}.sqlite3'

        if backup_path.exists():
            raise CommandError(
                f'Ya existe un backup con ese nombre: {backup_path}'
            )

        copy2(database_path, backup_path)

        self.stdout.write(
            self.style.SUCCESS(
                f'Backup creado correctamente: {backup_path}'
            )
        )
