import os
import re
import requests
from pathlib import Path
from django.core.management.base import BaseCommand
from django.db import transaction
from apps.taxonomy.models import TaxonomyCategory, TaxonomyAttribute, TaxonomyAttributeValue
from django.conf import settings

GITHUB_RAW_BASE = "https://raw.githubusercontent.com/Shopify/product-taxonomy/main/dist/en"

FILES = {
    'categories': 'categories.txt',
    'attributes': 'attributes.txt',
    'attribute_values': 'attribute_values.txt',
}


class Command(BaseCommand):
    help = 'Downloads and populates the official Shopify Product Taxonomy into the database'

    def add_arguments(self, parser):
        parser.add_argument(
            '--force-download',
            action='store_true',
            help='Force re-download of taxonomy files even if cached locally'
        )
        parser.add_argument(
            '--limit',
            type=int,
            default=0,
            help='Optional limit of categories to import (0 = all)'
        )

    def handle(self, *args, **options):
        force = options['force_download']
        limit = options['limit']
        cache_dir = Path(settings.CLASSIFIER_SETTINGS.get('TAXONOMY_CACHE_DIR', settings.BASE_DIR / 'data' / 'taxonomy'))
        cache_dir.mkdir(parents=True, exist_ok=True)

        self.stdout.write(self.style.SUCCESS("=== Starting Shopify Taxonomy Ingestion ==="))
        
        # 1. Download/Cache Files
        file_paths = {}
        for key, filename in FILES.items():
            local_path = cache_dir / filename
            if not local_path.exists() or force:
                url = f"{GITHUB_RAW_BASE}/{filename}"
                self.stdout.write(f"Downloading {filename} from {url}...")
                try:
                    resp = requests.get(url, timeout=30)
                    resp.raise_for_status()
                    with open(local_path, 'wb') as f:
                        f.write(resp.content)
                    self.stdout.write(self.style.SUCCESS(f"Saved {filename} ({len(resp.content)} bytes)"))
                except Exception as e:
                    self.stderr.write(f"Error downloading {filename}: {e}")
                    return
            else:
                self.stdout.write(f"Using cached file: {local_path}")
            file_paths[key] = local_path

        # 2. Import Attributes
        self.stdout.write("\nImporting Attributes...")
        attr_objs = []
        attr_lookup = {}
        with open(file_paths['attributes'], 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                parts = line.split(' : ', 1)
                if len(parts) == 2:
                    gid, name = parts[0].strip(), parts[1].strip()
                    handle = re.sub(r'[^a-z0-9_]+', '_', name.lower()).strip('_')
                    attr_obj = TaxonomyAttribute(id=gid, name=name, handle=handle)
                    attr_objs.append(attr_obj)
                    attr_lookup[name.lower()] = attr_obj

        with transaction.atomic():
            TaxonomyAttribute.objects.all().delete()
            TaxonomyAttribute.objects.bulk_create(attr_objs, batch_size=1000, ignore_conflicts=True)
        self.stdout.write(self.style.SUCCESS(f"[OK] Imported {len(attr_objs)} attributes."))

        # 3. Import Attribute Values
        self.stdout.write("\nImporting Attribute Values...")
        val_objs = []
        val_pattern = re.compile(r'^(gid://shopify/TaxonomyValue/\d+)\s*:\s*(.+?)\s*\[(.+)\]$')
        
        with open(file_paths['attribute_values'], 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                m = val_pattern.match(line)
                if m:
                    val_id, val_name, attr_name = m.groups()
                    attr = attr_lookup.get(attr_name.strip().lower())
                    if attr:
                        val_objs.append(TaxonomyAttributeValue(
                            id=val_id.strip(),
                            attribute_id=attr.id,
                            name=val_name.strip()
                        ))

        with transaction.atomic():
            TaxonomyAttributeValue.objects.all().delete()
            TaxonomyAttributeValue.objects.bulk_create(val_objs, batch_size=2000, ignore_conflicts=True)
        self.stdout.write(self.style.SUCCESS(f"[OK] Imported {len(val_objs)} attribute values."))

        # 4. Import Categories
        self.stdout.write("\nImporting Taxonomy Categories...")
        cat_records = []
        path_to_id = {}
        
        with open(file_paths['categories'], 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                parts = line.split(' : ', 1)
                if len(parts) == 2:
                    gid, full_name = parts[0].strip(), parts[1].strip()
                    code = gid.split('/')[-1] if '/' in gid else gid
                    segments = [s.strip() for s in full_name.split('>')]
                    name = segments[-1]
                    level = len(segments) - 1
                    
                    cat_records.append({
                        'id': gid,
                        'code': code,
                        'name': name,
                        'full_name': full_name,
                        'level': level,
                        'segments': segments
                    })
                    path_to_id[full_name] = gid
                    if limit and len(cat_records) >= limit:
                        break

        # Compute parent IDs and leaf flags
        all_parent_ids = set()
        for cat in cat_records:
            if len(cat['segments']) > 1:
                parent_path = ' > '.join(cat['segments'][:-1])
                parent_id = path_to_id.get(parent_path)
                cat['parent_id'] = parent_id
                if parent_id:
                    all_parent_ids.add(parent_id)
            else:
                cat['parent_id'] = None

        cat_objs = []
        for cat in cat_records:
            is_leaf = cat['id'] not in all_parent_ids
            cat_objs.append(TaxonomyCategory(
                id=cat['id'],
                code=cat['code'],
                name=cat['name'],
                full_name=cat['full_name'],
                level=cat['level'],
                parent_id=cat['parent_id'],
                is_leaf=is_leaf
            ))

        with transaction.atomic():
            TaxonomyCategory.objects.all().delete()
            TaxonomyCategory.objects.bulk_create(cat_objs, batch_size=1000, ignore_conflicts=True)
        
        # Link common attributes to categories
        self.stdout.write("Linking standard product attributes to relevant categories...")
        core_attr_names = ['color', 'material', 'size', 'finish', 'pattern', 'brand', 'style', 'furniture design', 'upholstery material']
        core_attrs = list(TaxonomyAttribute.objects.filter(name__iregex=r'(' + '|'.join(core_attr_names) + r')'))
        
        if core_attrs:
            CategoryAttributeRel = TaxonomyCategory.attributes.through
            rels = []
            for cat in cat_objs[:1500]:
                for attr in core_attrs[:5]:
                    rels.append(CategoryAttributeRel(taxonomycategory_id=cat.id, taxonomyattribute_id=attr.id))
            CategoryAttributeRel.objects.bulk_create(rels, batch_size=2000, ignore_conflicts=True)

        self.stdout.write(self.style.SUCCESS(f"[OK] Successfully imported {len(cat_objs)} Shopify categories!"))
        self.stdout.write(self.style.SUCCESS("=== Taxonomy Ingestion Complete ==="))
