"""
Category Attribute and Value Extraction & Normalization Engine.
Extracts standardized Shopify category attributes and canonical attribute values from product metadata.
Provides category-aware attribute detection based on Shopify Product Taxonomy relationships,
canonical attribute value matching against TaxonomyAttributeValue, and separate product specifications.
"""
import re
import logging
from django.db.models import Q
from apps.taxonomy.models import TaxonomyCategory, TaxonomyAttribute, TaxonomyAttributeValue

logger = logging.getLogger(__name__)

# Canonical material mappings for normalization
CANONICAL_MATERIALS = {
    'bonded leather': 'Bonded Leather',
    'genuine leather': 'Genuine Leather',
    'faux leather': 'Faux Leather',
    'leatherette': 'Faux Leather',
    'leather': 'Leather',
    'velvet': 'Velvet',
    'upholstered fabric': 'Fabric',
    'upholstered polyester fabric': 'Polyester',
    'polyester fabric': 'Polyester',
    'polyester': 'Polyester',
    'fabric': 'Fabric',
    'linen': 'Linen',
    'cotton': 'Cotton',
    'boucle': 'Boucle',
    'solid wood': 'Wood',
    'engineered wood': 'Wood',
    'rubberwood': 'Wood',
    'oak': 'Wood',
    'walnut': 'Wood',
    'teak': 'Wood',
    'mahogany': 'Wood',
    'pine': 'Wood',
    'wood': 'Wood',
    'rattan': 'Rattan',
    'wicker': 'Wicker',
    'stainless steel': 'Stainless Steel',
    'aluminum': 'Aluminum',
    'steel': 'Steel',
    'iron': 'Iron',
    'metal': 'Metal',
    'brass': 'Brass',
    'glass': 'Glass',
    'tempered glass': 'Glass',
    'marble': 'Marble',
    'ceramic': 'Ceramic',
    'plastic': 'Plastic',
    'acrylic': 'Acrylic',
    'microfiber': 'Microfiber',
    'nylon': 'Nylon',
    'suede': 'Suede',
    'polyurethane': 'Polyurethane (PU)',
}

# Canonical color mappings for normalization
CANONICAL_COLORS = {
    'white': 'White',
    'off-white': 'White',
    'black': 'Black',
    'gray': 'Gray',
    'grey': 'Gray',
    'charcoal': 'Charcoal',
    'granite': 'Granite',
    'brown': 'Brown',
    'beige': 'Beige',
    'cream': 'Cream',
    'ivory': 'Ivory',
    'navy': 'Navy',
    'navy blue': 'Navy',
    'blue': 'Blue',
    'teal': 'Teal',
    'green': 'Green',
    'emerald': 'Emerald',
    'olive': 'Olive',
    'gold': 'Gold',
    'silver': 'Silver',
    'bronze': 'Bronze',
    'brass': 'Brass',
    'walnut': 'Walnut',
    'natural': 'Natural',
    'espresso': 'Espresso',
    'tan': 'Tan',
    'pink': 'Pink',
    'rose gold': 'Rose Gold',
    'orange': 'Orange',
    'yellow': 'Yellow',
    'red': 'Red',
    'burgundy': 'Burgundy',
    'mustard': 'Mustard',
    'purple': 'Purple',
    'multicolor': 'Multicolor',
}

# Base color fallback for Shopify canonical taxonomy matching
COLOR_TO_BASE_SHOPIFY_COLOR = {
    'ivory': 'White',
    'off-white': 'White',
    'cream': 'Beige',
    'tan': 'Beige',
    'natural': 'Beige',
    'charcoal': 'Gray',
    'granite': 'Gray',
    'grey': 'Gray',
    'espresso': 'Brown',
    'walnut': 'Brown',
    'teal': 'Blue',
    'navy blue': 'Navy',
    'emerald': 'Green',
    'olive': 'Green',
    'burgundy': 'Red',
    'mustard': 'Yellow',
}

# Canonical style mappings
CANONICAL_STYLES = {
    'mid-century modern': 'Modern',
    'mid-century': 'Modern',
    'modern': 'Modern',
    'contemporary': 'Contemporary',
    'transitional': 'Transitional',
    'boho': 'Bohemian',
    'bohemian': 'Bohemian',
    'rustic': 'Rustic',
    'industrial': 'Industrial',
    'scandinavian': 'Scandinavian',
    'minimalist': 'Minimalist',
    'traditional': 'Traditional',
    'farmhouse': 'Farmhouse',
    'classic': 'Classic',
    'retro': 'Retro/Vintage',
    'vintage': 'Retro/Vintage',
    'victorian': 'Victorian',
    'colonial': 'Colonial',
}

PATTERNS = [
    'Heathered Weave', 'Heathered', 'Tufted', 'Solid', 'Striped',
    'Textured', 'Distressed', 'Channel Tufted', 'Checkered', 'Chevron',
    'Floral', 'Geometric', 'Plaid', 'Animal', 'Abstract', 'Damask'
]

ROOMS_AND_DOMAINS = [
    'baby', 'toddler', 'bathroom', 'bedroom', 'kitchen', 'dining',
    'office', 'outdoor', 'patio', 'vehicle', 'automotive', 'boat',
    'dollhouse', 'boombox', 'ant farm', 'aquarium', 'emergency',
    'fishing', 'motorcycle', 'bicycle', 'musical'
]

# Fast in-memory caches for high-throughput batch classification
_CATEGORY_ATTRIBUTES_CACHE = {}
_ATTRIBUTE_VALUES_CACHE = {}
_UNIVERSAL_ATTRIBUTES_CACHE = None


def clear_attribute_cache():
    """Clears in-memory attribute caches (useful during tests or taxonomy reload)."""
    global _CATEGORY_ATTRIBUTES_CACHE, _ATTRIBUTE_VALUES_CACHE, _UNIVERSAL_ATTRIBUTES_CACHE
    _CATEGORY_ATTRIBUTES_CACHE.clear()
    _ATTRIBUTE_VALUES_CACHE.clear()
    _UNIVERSAL_ATTRIBUTES_CACHE = None


def get_universal_attributes():
    """Returns core physical Shopify taxonomy attributes applicable across general consumer categories."""
    global _UNIVERSAL_ATTRIBUTES_CACHE
    if _UNIVERSAL_ATTRIBUTES_CACHE is None:
        names = ['Material', 'Color', 'Pattern']
        attrs = list(TaxonomyAttribute.objects.filter(name__in=names).prefetch_related('values'))
        order_map = {n: i for i, n in enumerate(names)}
        attrs.sort(key=lambda a: order_map.get(a.name, 99))
        _UNIVERSAL_ATTRIBUTES_CACHE = attrs
    return _UNIVERSAL_ATTRIBUTES_CACHE


def get_canonical_values_for_attribute(attribute):
    """
    Returns a dictionary of {lowercase_name: {'name': canonical_name, 'id': value_id}} for a given TaxonomyAttribute.
    Results are cached in memory for sub-millisecond retrieval.
    """
    if attribute.id in _ATTRIBUTE_VALUES_CACHE:
        return _ATTRIBUTE_VALUES_CACHE[attribute.id]

    val_map = {}
    for val in attribute.values.all():
        val_map[val.name.lower()] = {'name': val.name, 'id': val.id}
    _ATTRIBUTE_VALUES_CACHE[attribute.id] = val_map
    return val_map


CATEGORY_DOMAIN_ATTRIBUTES = [
    # 1. Sectional Sofas
    (
        lambda path, leaf: 'sectional' in path or 'sectional' in leaf,
        ['Sectional configuration', 'Sectional shape', 'Chaise or sectional orientation', 'Upholstery material', 'Chair/Sofa features', 'Furniture finish']
    ),
    # 2. Sofas / Couches / Loveseats / Daybeds / Futons / Settees / Chaise Lounges
    (
        lambda path, leaf: any(k in path or k in leaf for k in ['sofa', 'couch', 'loveseat', 'daybed', 'futon', 'settee', 'chaise lounge']),
        ['Upholstery material', 'Chair/Sofa features', 'Furniture finish']
    ),
    # 3. Chairs / Armchairs / Recliners / Benches / Stools / Ottomans / Seating
    (
        lambda path, leaf: any(k in path or k in leaf for k in ['chair', 'armchair', 'recliner', 'bench', 'stool', 'ottoman', 'seating']),
        ['Upholstery material', 'Chair/Sofa features', 'Furniture finish']
    ),
    # 4. Tables / Desks / Nightstands / Consoles / Side Tables
    (
        lambda path, leaf: any(k in path or k in leaf for k in ['table', 'desk', 'nightstand', 'side table', 'end table', 'console']),
        ['Table base material', 'Table leg design', 'Table base type', 'Table size', 'Furniture finish']
    ),
    # 5. Beds / Bed Frames / Headboards / Mattresses
    (
        lambda path, leaf: any(k in path or k in leaf for k in ['bed', 'headboard', 'mattress']),
        ['Bed/Frame features', 'Bed base type', 'Bed storage type', 'Bedding size', 'Furniture finish', 'Upholstery material']
    ),
    # 6. Rugs & Mats
    (
        lambda path, leaf: any(k in path or k in leaf for k in ['rug', 'mat ']),
        ['Mat/Rug shape', 'Rug/Mat features', 'Rug/Mat material']
    ),
    # 7. Apparel / Tops / Shirts / Dresses / Clothing
    (
        lambda path, leaf: any(k in path or k in leaf for k in ['clothing', 'apparel', 'shirt', 'top', 'dress', 'jacket', 'coat', 'sweater']),
        ['Sleeve length type', 'Sleeve style', 'Neckline']
    ),
    # 8. General Furniture / Storage / Cabinets / Shelving
    (
        lambda path, leaf: 'furniture' in path or any(k in leaf for k in ['cabinet', 'dresser', 'bookcase', 'shelf', 'storage', 'stand', 'cart', 'buffet', 'credenza', 'wardrobe']),
        ['Furniture finish']
    ),
]


def get_category_relevant_attributes(category):
    """
    Resolves Shopify taxonomy attributes relevant to a specific TaxonomyCategory.
    Returns a list of TaxonomyAttribute model instances strictly from Shopify Product Taxonomy.
    """
    if not category:
        return get_universal_attributes()

    cat_id = getattr(category, 'id', str(category))
    if cat_id in _CATEGORY_ATTRIBUTES_CACHE:
        return _CATEGORY_ATTRIBUTES_CACHE[cat_id]

    # 1. Check if category has explicit M2M linked attributes in the database
    if hasattr(category, 'attributes'):
        direct = list(category.attributes.prefetch_related('values').all())
        if direct and len(direct) > 2:
            _CATEGORY_ATTRIBUTES_CACHE[cat_id] = direct
            return direct

    # 2. Derive relevant attributes from Shopify Taxonomy data based on category domain
    full_path_lower = (category.full_name or category.name or "").lower()
    leaf_lower = (category.name or "").lower()

    universal = get_universal_attributes()
    seen_ids = {u.id for u in universal}
    cat_specific_names = []

    for matcher, attr_names in CATEGORY_DOMAIN_ATTRIBUTES:
        if matcher(full_path_lower, leaf_lower):
            for name in attr_names:
                if name not in cat_specific_names:
                    cat_specific_names.append(name)
            break  # Matched the most specific category domain rule

    specific_attrs = []
    if cat_specific_names:
        fetched = list(TaxonomyAttribute.objects.filter(name__in=cat_specific_names).prefetch_related('values'))
        # Retain order as defined in domain list
        order_map = {name: idx for idx, name in enumerate(cat_specific_names)}
        fetched.sort(key=lambda a: order_map.get(a.name, 99))
        for attr in fetched:
            if attr.id not in seen_ids:
                seen_ids.add(attr.id)
                specific_attrs.append(attr)

    # Combine: Universal attributes (Material, Color, Pattern, Style), followed by Category-Specific Attributes
    result = list(universal) + specific_attrs
    _CATEGORY_ATTRIBUTES_CACHE[cat_id] = result
    return result


def normalize_color(raw_color):
    """
    Extracts canonical color and pattern from composite raw strings like 'Heathered Weave Ivory'
    or 'Navy Fabric / Blue'. Returns (detected_color, detected_pattern, raw_cleaned).
    """
    if not raw_color:
        return "", "", ""
    raw_str = str(raw_color).strip()
    raw_lower = raw_str.lower()
    detected_color = ""
    detected_pattern = ""

    # Check for known patterns in color string
    for pat in PATTERNS:
        if pat.lower() in raw_lower:
            detected_pattern = pat
            break

    # Look for known color tokens
    for c_key, c_val in CANONICAL_COLORS.items():
        if re.search(r'\b' + re.escape(c_key) + r'\b', raw_lower):
            detected_color = c_val
            break

    if not detected_color:
        # Fallback to cleaning up words like 'Fabric'
        cleaned = re.sub(r'\b(fabric|weave|color|finish)\b', '', raw_str, flags=re.IGNORECASE).strip()
        detected_color = cleaned.title() if cleaned else raw_str.title()

    return detected_color, detected_pattern, raw_str


def normalize_material(raw_material):
    """Extracts canonical material from raw descriptions like '100% Polyester Heathered Weave Fabric'."""
    if not raw_material:
        return "", ""
    raw_str = str(raw_material).strip()
    raw_lower = raw_str.lower()

    for m_key, m_val in CANONICAL_MATERIALS.items():
        if re.search(r'\b' + re.escape(m_key) + r'\b', raw_lower):
            return m_val, raw_str

    # Fallback to title-cased clean material
    cleaned = re.sub(r'[\d%]+', '', raw_str).strip()
    return cleaned.title() if cleaned else raw_str.title(), raw_str


def normalize_style(raw_style):
    """Extracts canonical style from raw descriptions."""
    if not raw_style:
        return "", ""
    raw_str = str(raw_style).strip()
    raw_lower = raw_str.lower()

    for s_key, s_val in CANONICAL_STYLES.items():
        if re.search(r'\b' + re.escape(s_key) + r'\b', raw_lower):
            return s_val, raw_str

    return raw_str.title(), raw_str


def match_canonical_shopify_value(attribute, raw_val, norm_val):
    """
    Matches an extracted/normalized value against the valid predefined canonical
    TaxonomyAttributeValue choices for a given TaxonomyAttribute.
    Returns (matched_shopify_value, matched_shopify_value_id) or (None, None).
    """
    if not raw_val and not norm_val:
        return None, None

    canon_map = get_canonical_values_for_attribute(attribute)
    if not canon_map:
        return norm_val or raw_val, None

    # 1. Exact case-insensitive match on normalized value
    if norm_val and norm_val.lower() in canon_map:
        entry = canon_map[norm_val.lower()]
        return entry['name'], entry['id']

    # 2. Exact case-insensitive match on raw value
    if raw_val and raw_val.lower() in canon_map:
        entry = canon_map[raw_val.lower()]
        return entry['name'], entry['id']

    # 3. Substring/token match in canonical values
    test_str = f"{norm_val or ''} {raw_val or ''}".lower()
    for cv_lower, entry in canon_map.items():
        if len(cv_lower) >= 3 and re.search(r'\b' + re.escape(cv_lower) + r'\b', test_str):
            return entry['name'], entry['id']

    # 4. Check if any word in raw value matches canonical value
    words = re.findall(r'[a-zA-Z]{3,}', test_str)
    for w in words:
        if w in canon_map:
            entry = canon_map[w]
            return entry['name'], entry['id']

    # 5. Base color fallback (e.g. Ivory -> White / Cream -> Beige) if specific shade not in canonical taxonomy list
    if norm_val and norm_val.lower() in COLOR_TO_BASE_SHOPIFY_COLOR:
        base_c = COLOR_TO_BASE_SHOPIFY_COLOR[norm_val.lower()]
        if base_c.lower() in canon_map:
            entry = canon_map[base_c.lower()]
            return entry['name'], entry['id']

    return None, None


def find_matching_raw_snippet(keyword, product):
    """Finds the most specific bullet, material, color, or text line containing the keyword."""
    if not keyword:
        return ""
    # Check fields in order of specificity
    candidates = []
    if product.materials:
        candidates.append(product.materials.strip())
    if product.product_color:
        candidates.append(product.product_color.strip())
    if product.color_collection:
        candidates.append(product.color_collection.strip())
    if product.bullets:
        candidates.extend([b.strip() for b in product.bullets.split('\n') if b.strip()])
    if product.name:
        candidates.append(product.name.strip())
    if product.description:
        candidates.extend([s.strip() for s in re.split(r'[.;\n]', product.description) if s.strip()])

    kw_lower = keyword.lower()
    for c in candidates:
        if re.search(r'\b' + re.escape(kw_lower) + r'\b', c.lower()):
            return c[:80]
    return keyword.title()


def extract_category_attributes(product, category=None):
    """
    Extracts category-relevant Shopify taxonomy attributes and matches canonical values.
    Returns a list of attribute dictionaries:
    [
        {
            'attribute_id': 'gid://shopify/TaxonomyAttribute/4',
            'attribute_name': 'Material',
            'raw_value': '100% Polyester fabric',
            'normalized_value': 'Polyester',
            'matched_shopify_value': 'Polyester',
            'matched_shopify_value_id': 'gid://shopify/TaxonomyValue/4-2',
            'status': 'detected'
        },
        ...
    ]
    """
    relevant_attrs = get_category_relevant_attributes(category)
    combined_text = f"{product.name or ''} {product.materials or ''} {product.product_color or ''} {product.color_collection or ''} {product.bullets or ''} {product.description or ''}"

    shopify_attrs = []
    pattern_from_color = ""

    for attr in relevant_attrs:
        aname = attr.name
        raw_val = None
        norm_val = None
        matched_val = None
        matched_id = None

        canon_map = get_canonical_values_for_attribute(attr)

        # 1. Material
        if aname == 'Material':
            if product.materials and product.materials.strip():
                norm_val, raw_val = normalize_material(product.materials)
            else:
                for m_key, m_val in CANONICAL_MATERIALS.items():
                    if re.search(r'\b' + re.escape(m_key) + r'\b', combined_text, re.IGNORECASE):
                        raw_val = find_matching_raw_snippet(m_key, product)
                        norm_val = m_val
                        break
            if raw_val or norm_val:
                matched_val, matched_id = match_canonical_shopify_value(attr, raw_val, norm_val)

        # 2. Color
        elif aname == 'Color':
            if product.product_color and product.product_color.strip():
                norm_val, pattern_from_color, raw_val = normalize_color(product.product_color)
            elif product.color_collection and product.color_collection.strip():
                norm_val, pattern_from_color, raw_val = normalize_color(product.color_collection)
            else:
                for c_key, c_val in CANONICAL_COLORS.items():
                    if re.search(r'\b' + re.escape(c_key) + r'\b', combined_text, re.IGNORECASE):
                        raw_val = find_matching_raw_snippet(c_key, product)
                        norm_val = c_val
                        break
            if raw_val or norm_val:
                matched_val, matched_id = match_canonical_shopify_value(attr, raw_val, norm_val)

        # 3. Pattern
        elif aname == 'Pattern':
            if pattern_from_color:
                raw_val = product.product_color or pattern_from_color
                norm_val = pattern_from_color
            else:
                for pat in PATTERNS:
                    if re.search(r'\b' + re.escape(pat) + r'\b', combined_text, re.IGNORECASE):
                        raw_val = find_matching_raw_snippet(pat, product)
                        norm_val = pat
                        break
            if raw_val or norm_val:
                matched_val, matched_id = match_canonical_shopify_value(attr, raw_val, norm_val)

        # 4. Upholstery Material
        elif aname == 'Upholstery material':
            if product.materials and product.materials.strip():
                norm_val, raw_val = normalize_material(product.materials)
            else:
                for m_key in ['bonded leather', 'genuine leather', 'faux leather', 'leatherette', 'leather', 'polyester', 'velvet', 'linen', 'cotton', 'microfiber', 'suede', 'vinyl', 'acrylic', 'canvas', 'fabric']:
                    if re.search(r'\b' + re.escape(m_key) + r'\b', combined_text, re.IGNORECASE):
                        raw_val = find_matching_raw_snippet(m_key, product)
                        norm_val, _ = normalize_material(m_key)
                        break
            if raw_val or norm_val:
                matched_val, matched_id = match_canonical_shopify_value(attr, raw_val, norm_val)

        # 5. Sectional Shape
        elif aname == 'Sectional shape':
            shape_patterns = [
                (r'\b(l[\s-]?shaped?|l[\s-]shape)\b', 'L-shaped'),
                (r'\b(u[\s-]?shaped?|u[\s-]shape)\b', 'U-shaped'),
                (r'\bcurved\b', 'Curved'),
                (r'\b(modular|custom)\b', 'Modular/custom'),
                (r'\bbumper\b', 'Bumper sectional'),
                (r'\bchaise sectional\b', 'Chaise sectional'),
                (r'\breversible sectional\b', 'Reversible sectional'),
                (r'\bsymmetrical\b', 'Symmetrical'),
            ]
            for pat, canonical in shape_patterns:
                m = re.search(pat, combined_text, re.IGNORECASE)
                if m:
                    raw_val = find_matching_raw_snippet(m.group(1), product)
                    norm_val = canonical
                    matched_val, matched_id = match_canonical_shopify_value(attr, raw_val, norm_val)
                    if not matched_val:
                        matched_val = canonical
                    break

        # 6. Chaise or Sectional Orientation
        elif aname == 'Chaise or sectional orientation':
            orient_patterns = [
                (r'\b(left[\s-]?facing|left[\s-]?hand[\s-]?facing|lhf)\b', 'Left-facing'),
                (r'\b(right[\s-]?facing|right[\s-]?hand[\s-]?facing|rhf)\b', 'Right-facing'),
                (r'\breversible\b', 'Reversible'),
                (r'\bsymmetrical\b', 'Symmetrical'),
            ]
            for pat, canonical in orient_patterns:
                m = re.search(pat, combined_text, re.IGNORECASE)
                if m:
                    raw_val = find_matching_raw_snippet(m.group(1), product)
                    norm_val = canonical
                    matched_val, matched_id = match_canonical_shopify_value(attr, raw_val, norm_val)
                    if not matched_val:
                        matched_val = canonical
                    break

        # 7. Furniture Finish
        elif aname == 'Furniture finish':
            finish_patterns = [
                (r'\b(matte|flat)\b', 'Matte'),
                (r'\b(gloss|glossy|high[\s-]?gloss)\b', 'Gloss'),
                (r'\b(natural|unfinished)\b', 'Natural'),
                (r'\b(stained|wood[\s-]?stain)\b', 'Stained'),
                (r'\b(powder[\s-]?coated)\b', 'Powder-coated'),
                (r'\b(painted)\b', 'Painted'),
                (r'\b(laminated|veneer)\b', 'Laminated'),
                (r'\b(varnished|lacquered)\b', 'Varnished'),
            ]
            for pat, canonical in finish_patterns:
                m = re.search(pat, combined_text, re.IGNORECASE)
                if m:
                    raw_val = find_matching_raw_snippet(m.group(1), product)
                    norm_val = canonical
                    matched_val, matched_id = match_canonical_shopify_value(attr, raw_val, norm_val)
                    if not matched_val:
                        matched_val = canonical
                    break

        # 8. Other Category-Specific Attributes: search for canonical values directly
        else:
            if canon_map:
                for cv_lower, entry in canon_map.items():
                    if len(cv_lower) >= 3 and re.search(r'\b' + re.escape(cv_lower) + r'\b', combined_text, re.IGNORECASE):
                        raw_val = find_matching_raw_snippet(cv_lower, product)
                        norm_val = entry['name']
                        matched_val = entry['name']
                        matched_id = entry['id']
                        break

        status = 'detected' if (raw_val or norm_val or matched_val) else 'not_detected'
        shopify_attrs.append({
            'attribute_id': attr.id,
            'attribute_name': attr.name,
            'raw_value': raw_val,
            'normalized_value': norm_val,
            'matched_shopify_value': matched_val,
            'matched_shopify_value_id': matched_id,
            'status': status
        })

    return shopify_attrs


def extract_product_specifications(product):
    """
    Extracts general supplier specifications from the product metadata.
    Keeps product specifications strictly separated from Shopify category attributes.
    """
    specs = []
    combined_text = f"{product.name or ''} {product.materials or ''} {product.product_color or ''} {product.bullets or ''} {product.description or ''}"

    # 1. Assembly Required
    if product.assembly_required:
        val = str(product.assembly_required).strip().upper()
        if val in ('Y', 'YES', 'TRUE', '1'):
            specs.append({'name': 'Assembly Required', 'value': 'Yes', 'raw_value': str(product.assembly_required)})
        elif val in ('N', 'NO', 'FALSE', '0'):
            specs.append({'name': 'Assembly Required', 'value': 'No', 'raw_value': str(product.assembly_required)})
    elif re.search(r'\bassembly required\b', combined_text, re.IGNORECASE):
        specs.append({'name': 'Assembly Required', 'value': 'Yes', 'raw_value': 'Assembly Required'})

    # 2. Weight Capacity
    weight_match = re.search(r'weight\s+capacity\s*:?\s*(\d+\s*(?:lbs|lb|kg))', combined_text, re.IGNORECASE)
    if weight_match:
        cap = weight_match.group(1).strip()
        norm_cap = cap.lower().replace('lb', 'lbs').replace('lbss', 'lbs')
        specs.append({'name': 'Weight Capacity', 'value': norm_cap, 'raw_value': cap})

    # 3. Product Dimensions
    if product.product_dimensions and str(product.product_dimensions).strip() not in ('', 'nan'):
        dim = str(product.product_dimensions).strip().split('\n')[0][:100]
        specs.append({'name': 'Dimensions', 'value': dim, 'raw_value': str(product.product_dimensions).strip()})

    # 4. Product Weight
    if product.product_weight and str(product.product_weight).strip() not in ('', 'nan'):
        wt = str(product.product_weight).strip()
        val_str = f"{wt} lbs" if not wt.lower().endswith(('lbs', 'lb', 'kg')) else wt
        specs.append({'name': 'Product Weight', 'value': val_str, 'raw_value': wt})

    # 5. Country of Origin
    if product.country_of_origin and str(product.country_of_origin).strip() not in ('', 'nan'):
        co = str(product.country_of_origin).strip().title()
        specs.append({'name': 'Country of Origin', 'value': co, 'raw_value': str(product.country_of_origin).strip()})

    # 6. Is a Set
    if product.is_set:
        val = str(product.is_set).strip().upper()
        if val in ('Y', 'YES', 'TRUE', '1'):
            specs.append({'name': 'Is a Set', 'value': 'Yes', 'raw_value': str(product.is_set)})
        elif val in ('N', 'NO', 'FALSE', '0'):
            specs.append({'name': 'Is a Set', 'value': 'No', 'raw_value': str(product.is_set)})

    # 7. Set Includes
    if product.set_includes and str(product.set_includes).strip() not in ('', 'nan'):
        si = str(product.set_includes).strip()
        specs.append({'name': 'Set Includes', 'value': si, 'raw_value': si})

    # 8. Brand
    if getattr(product, 'brand', None) and str(product.brand).strip():
        specs.append({'name': 'Brand', 'value': str(product.brand).strip(), 'raw_value': str(product.brand).strip()})

    # 9. Model Number
    if getattr(product, 'model_number', None) and str(product.model_number).strip():
        specs.append({'name': 'Model Number', 'value': str(product.model_number).strip(), 'raw_value': str(product.model_number).strip()})

    # 10. Collection Name
    if getattr(product, 'collection_name', None) and str(product.collection_name).strip():
        specs.append({'name': 'Collection Name', 'value': str(product.collection_name).strip(), 'raw_value': str(product.collection_name).strip()})

    return specs


def extract_attributes(product, category=None):
    """
    Unified extraction entrypoint for a Product instance and optional TaxonomyCategory.
    Returns a dictionary containing strictly category-specific Shopify taxonomy attributes and values.
    """
    shopify_cat_attrs = extract_category_attributes(product, category)
    return {
        'shopify_category_attributes': shopify_cat_attrs,
    }


