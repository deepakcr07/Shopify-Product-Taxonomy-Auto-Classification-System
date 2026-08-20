import os
import re
import math
import pandas as pd
from django.db import transaction
from apps.products.models import Product, ClassificationResult

COMMON_BRAND_NAMES = [
    'Modway', 'Safavieh', 'Zinus', 'Ashley', 'Walker Edison', 'Flash Furniture',
    'Christopher Knight Home', 'Baxton Studio', 'Rivet', 'Stone & Beam',
    'Winsome', 'Sauder', 'Bush Furniture', 'Coaster Home Furnishings',
    'Home Styles', 'Signature Design', 'Novogratz', 'Dorel Living'
]


def clean_val(val, default=""):
    """Sanitizes Pandas/Excel values, handles NaN, floats, and whitespace."""
    if val is None:
        return default
    if isinstance(val, float) and math.isnan(val):
        return default
    if isinstance(val, (int, float)):
        return val
    s = str(val).strip()
    if s.lower() == 'nan':
        return default
    # Clean excel carriage return artifacts
    s = s.replace('_x000D_\n', '\n').replace('_x000D_', '\n')
    return s


def extract_brand_from_text(title, description="", raw_brand=""):
    """Extracts or identifies product brand from explicit field, title, or description."""
    if raw_brand and str(raw_brand).strip() not in ('', 'nan'):
        return str(raw_brand).strip()

    combined = f"{title} {description}"

    # Check for pattern "by Brand" or "from Brand"
    m = re.search(r'\b(?:by|from)\s+([A-Z][A-Za-z0-9\s&\'\.-]{1,30}?)(?:\s+[-–—|,]|\s*$)', title)
    if m:
        candidate = m.group(1).strip()
        if len(candidate) > 1 and candidate.lower() not in ('all', 'one', 'set', 'the', 'a', 'an'):
            return candidate

    # Check known brands
    for brand in COMMON_BRAND_NAMES:
        if re.search(r'\b' + re.escape(brand) + r'\b', combined, re.IGNORECASE):
            return brand

    # Check title prefix if starts with Capitalized brand token
    words = title.strip().split()
    if words and len(words) > 2 and words[0] in COMMON_BRAND_NAMES:
        return words[0]

    return ""


def parse_dataframe_from_file(file_path_or_buffer, file_format=None):
    """Parses an Excel or CSV file into a pandas DataFrame."""
    if hasattr(file_path_or_buffer, 'name'):
        ext = os.path.splitext(file_path_or_buffer.name)[1].lower()
    elif isinstance(file_path_or_buffer, str):
        ext = os.path.splitext(file_path_or_buffer)[1].lower()
    else:
        ext = f".{file_format}" if file_format else ".xlsx"

    if ext in ('.xlsx', '.xls'):
        return pd.read_excel(file_path_or_buffer)
    elif ext == '.csv':
        return pd.read_csv(file_path_or_buffer)
    else:
        raise ValueError(f"Unsupported file format: '{ext}'. Please upload an .xlsx, .xls, or .csv file.")


def validate_product_file(file_path_or_buffer, file_format=None):
    """
    Validates a product catalogue file without making database modifications.
    Returns preview summary metrics, integrity checks, and sample parsed records.
    """
    try:
        df = parse_dataframe_from_file(file_path_or_buffer, file_format)
    except Exception as e:
        return {
            'is_valid': False,
            'filename': getattr(file_path_or_buffer, 'name', 'Uploaded File'),
            'total_rows': 0,
            'errors': [f"Failed to read file: {str(e)}"]
        }

    col_map = {str(col).strip(): col for col in df.columns}
    def get_col(row, name, default=""):
        for orig, actual in col_map.items():
            if orig.lower() == name.lower():
                return clean_val(row.get(actual), default)
        return default

    total_rows = len(df)
    seen_in_file = set()
    dup_in_file = 0
    missing_names = 0
    missing_desc = 0
    missing_imgs = 0
    missing_materials = 0
    missing_subcats = 0
    product_numbers = []
    sample_records = []

    for idx, row in df.iterrows():
        prod_num = str(get_col(row, 'Product Number', '')).strip()
        if not prod_num:
            prod_num = f"ROW-{idx+1}"
        
        if prod_num in seen_in_file:
            dup_in_file += 1
        seen_in_file.add(prod_num)
        product_numbers.append(prod_num)

        name = str(get_col(row, 'Product Name', '')).strip()
        if not name:
            missing_names += 1

        desc = str(get_col(row, 'Product Description', '')).strip()
        if not desc:
            missing_desc += 1

        img1 = str(get_col(row, 'Image 1', '')).strip()
        if not img1 or not img1.startswith('http'):
            missing_imgs += 1

        mat = str(get_col(row, 'Materials', '')).strip()
        if not mat:
            missing_materials += 1

        subcat = str(get_col(row, 'Product Sub Category', '')).strip()
        if not subcat:
            missing_subcats += 1

        if len(sample_records) < 5:
            sample_records.append({
                'product_number': prod_num,
                'name': name or f"Product {prod_num}",
                'category': str(get_col(row, 'Product Category', '')),
                'sub_category': subcat,
                'has_image': bool(img1 and img1.startswith('http')),
                'has_desc': bool(desc)
            })

    # Check database for existing items
    existing_in_db = set(Product.objects.filter(product_number__in=product_numbers).values_list('product_number', flat=True))
    new_products_count = len(seen_in_file - existing_in_db)
    existing_products_count = len(seen_in_file & existing_in_db)

    warnings = []
    if missing_materials > 0:
        warnings.append(f"{missing_materials} products lack material specifications.")
    if missing_subcats > 0:
        warnings.append(f"{missing_subcats} products lack sub-categories.")
    if missing_desc > 0:
        warnings.append(f"{missing_desc} products have missing descriptions (fallback mode will be used).")
    if missing_imgs > 0:
        warnings.append(f"{missing_imgs} products have missing or non-HTTP images (text fallback will be used).")
    if existing_products_count > 0:
        warnings.append(f"{existing_products_count} products already exist in the database (will be updated idempotently).")

    return {
        'is_valid': True,
        'filename': getattr(file_path_or_buffer, 'name', 'Uploaded File'),
        'total_rows': total_rows,
        'valid_rows': total_rows - missing_names,
        'new_products': new_products_count,
        'existing_products': existing_products_count,
        'duplicates_in_file': dup_in_file,
        'missing_descriptions': missing_desc,
        'missing_images': missing_imgs,
        'missing_materials': missing_materials,
        'missing_subcategories': missing_subcats,
        'missing_optional_fields': missing_materials + missing_subcats,
        'columns_detected': [str(c).strip() for c in df.columns],
        'sample_records': sample_records,
        'warnings': warnings,
        'errors': []
    }


def import_product_file(file_path_or_buffer, file_format=None, batch_size=500, update_existing=True):
    """
    Imports products from an Excel or CSV file into the database with duplicate protection.
    Idempotently handles existing records and initializes ClassificationResults.
    
    Returns comprehensive execution statistics:
    {
        'total_read': int,
        'valid_rows': int,
        'imported': int,
        'updated': int,
        'duplicates_skipped': int,
        'invalid_rows': int,
        'missing_descriptions': int,
        'missing_images': int,
        'errors': list
    }
    """
    errors = []
    try:
        df = parse_dataframe_from_file(file_path_or_buffer, file_format)
    except Exception as e:
        return {
            'total_read': 0,
            'valid_rows': 0,
            'imported': 0,
            'updated': 0,
            'duplicates_skipped': 0,
            'invalid_rows': 0,
            'missing_descriptions': 0,
            'missing_images': 0,
            'errors': [f"Failed to parse file: {str(e)}"]
        }

    col_map = {str(col).strip(): col for col in df.columns}
    def get_col(row, name, default=""):
        for orig, actual in col_map.items():
            if orig.lower() == name.lower():
                return clean_val(row.get(actual), default)
        return default

    def to_float(v):
        try:
            return float(v) if v != '' else None
        except (ValueError, TypeError):
            return None

    # Step 1: Pre-fetch existing products to guarantee uniqueness & idempotency
    incoming_data = {}
    seen_numbers = set()
    duplicates_in_file = 0
    missing_desc_count = 0
    missing_img_count = 0
    invalid_rows = 0

    for idx, row in df.iterrows():
        try:
            prod_num = str(get_col(row, 'Product Number', '')).strip()
            if not prod_num:
                prod_num = f"PROD-{idx+1}"

            if prod_num in seen_numbers:
                duplicates_in_file += 1
                continue
            seen_numbers.add(prod_num)

            name = str(get_col(row, 'Product Name', '')).strip()
            if not name:
                name = f"Product {prod_num}"

            desc = str(get_col(row, 'Product Description', ''))
            if not desc.strip():
                missing_desc_count += 1

            # Extract images (Image 1 to 20)
            images = []
            for i in range(1, 21):
                img = get_col(row, f'Image {i}', '')
                if img and str(img).startswith('http'):
                    images.append(str(img).strip())

            primary_image = images[0] if images else get_col(row, 'Image 1', '')
            if isinstance(primary_image, str) and not primary_image.startswith('http'):
                primary_image = ''

            if not primary_image:
                missing_img_count += 1

            brand_raw = get_col(row, 'Brand', get_col(row, 'Manufacturer', ''))
            brand = extract_brand_from_text(name, desc, brand_raw)

            product_dict = {
                'product_number': prod_num,
                'model_number': str(get_col(row, 'Model Number', '')),
                'name': name,
                'brand': brand,
                'description': desc,
                'product_category': str(get_col(row, 'Product Category', '')),
                'product_sub_category': str(get_col(row, 'Product Sub Category', '')),
                'collection_name': str(get_col(row, 'Collection Name', '')),
                'color_collection': str(get_col(row, 'Color Collection', '')),
                'product_color': str(get_col(row, 'Product Color', '')),
                'materials': str(get_col(row, 'Materials', '')),
                'bullets': str(get_col(row, 'Bullets', '')),
                'set_includes': str(get_col(row, 'Set Includes', '')),
                'product_weight': str(get_col(row, 'Product Weight', '')),
                'product_dimensions': str(get_col(row, 'Product Dimensions', '')),
                'assembly_required': str(get_col(row, 'Assembly Required', '')),
                'is_set': str(get_col(row, 'Is a Set', '')),
                'country_of_origin': str(get_col(row, 'Country Of Origin', '')),
                'item_cost': to_float(get_col(row, 'Item Cost', None)),
                'map_price': to_float(get_col(row, 'MAP', None)),
                'msrp': to_float(get_col(row, 'MSRP', None)),
                'image_url': primary_image,
                'all_images': images,
                'product_url': str(get_col(row, 'Product URL', '')),
            }
            incoming_data[prod_num] = product_dict

        except Exception as e:
            invalid_rows += 1
            errors.append(f"Row {idx+1}: {str(e)}")

    # Step 2: Separate into new creates and existing updates
    all_incoming_numbers = list(incoming_data.keys())
    existing_products_map = {
        p.product_number: p
        for p in Product.objects.filter(product_number__in=all_incoming_numbers)
    }

    products_to_create = []
    products_to_update = []

    for prod_num, data in incoming_data.items():
        if prod_num in existing_products_map:
            if update_existing:
                p = existing_products_map[prod_num]
                for k, v in data.items():
                    setattr(p, k, v)
                products_to_update.append(p)
        else:
            products_to_create.append(Product(**data))

    imported_count = 0
    updated_count = 0

    with transaction.atomic():
        # Bulk create new products
        if products_to_create:
            Product.objects.bulk_create(products_to_create, batch_size=batch_size)
            created_skus = [p.product_number for p in products_to_create]
            persisted_products = list(Product.objects.filter(product_number__in=created_skus))
            imported_count = len(persisted_products)

            # Create corresponding pending classification records
            classifications_to_create = [
                ClassificationResult(
                    product_id=p.id,
                    status=ClassificationResult.STATUS_PENDING,
                    image_status=ClassificationResult.IMAGE_AVAILABLE if p.image_url else ClassificationResult.IMAGE_MISSING
                )
                for p in persisted_products
            ]
            ClassificationResult.objects.bulk_create(classifications_to_create, batch_size=batch_size)

        # Bulk update existing products
        if products_to_update:
            Product.objects.bulk_update(
                products_to_update,
                fields=[
                    'model_number', 'name', 'brand', 'description', 'product_category',
                    'product_sub_category', 'collection_name', 'color_collection',
                    'product_color', 'materials', 'bullets', 'set_includes',
                    'product_weight', 'product_dimensions', 'assembly_required',
                    'is_set', 'country_of_origin', 'item_cost', 'map_price', 'msrp',
                    'image_url', 'all_images', 'product_url'
                ],
                batch_size=batch_size
            )
            updated_count = len(products_to_update)

            # Ensure every existing product has a classification record
            existing_p_ids = [p.id for p in products_to_update]
            existing_cls_prod_ids = set(
                ClassificationResult.objects.filter(product_id__in=existing_p_ids).values_list('product_id', flat=True)
            )
            missing_cls = [
                ClassificationResult(
                    product=p,
                    status=ClassificationResult.STATUS_PENDING,
                    image_status=ClassificationResult.IMAGE_AVAILABLE if p.image_url else ClassificationResult.IMAGE_MISSING
                )
                for p in products_to_update if p.id not in existing_cls_prod_ids
            ]
            if missing_cls:
                ClassificationResult.objects.bulk_create(missing_cls, batch_size=batch_size)

    return {
        'total_read': len(df),
        'valid_rows': len(incoming_data),
        'imported': imported_count,
        'updated': updated_count,
        'duplicates_skipped': duplicates_in_file,
        'invalid_rows': invalid_rows,
        'missing_descriptions': missing_desc_count,
        'missing_images': missing_img_count,
        'errors': errors[:20]
    }
