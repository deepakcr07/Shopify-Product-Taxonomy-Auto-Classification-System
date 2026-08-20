import os
from pathlib import Path
from django.core.management.base import BaseCommand
from apps.products.services.importer import import_product_file
from django.conf import settings


class Command(BaseCommand):
    help = 'Imports products from an Excel or CSV file into the database'

    def add_arguments(self, parser):
        parser.add_argument(
            '--file',
            type=str,
            default='Product List.xlsx',
            help='Path to the product catalogue file (default: Product List.xlsx)'
        )

    def handle(self, *args, **options):
        file_path = options['file']
        if not os.path.isabs(file_path):
            file_path = os.path.join(settings.BASE_DIR, file_path)

        if not os.path.exists(file_path):
            self.stderr.write(f"File not found: {file_path}")
            return

        self.stdout.write(f"Starting catalogue import from {file_path}...")
        res = import_product_file(file_path)
        
        self.stdout.write(self.style.SUCCESS(
            f"[OK] Import finished: Read {res['total_read']} rows, successfully imported {res['imported']} products."
        ))
        if res['errors']:
            self.stdout.write(self.style.WARNING(f"Warnings ({len(res['errors'])}): {res['errors'][:5]}"))
