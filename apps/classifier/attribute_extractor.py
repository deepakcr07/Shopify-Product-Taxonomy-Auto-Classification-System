"""
Category Attribute and Value Extraction & Normalization Engine.
Extracts standardized Shopify category attributes and canonical attribute values from product metadata.
Stores both raw and canonical normalized representations.
"""
import re

CANONICAL_MATERIALS = {
    'bonded leather': 'Bonded Leather',
    'genuine leather': 'Genuine Leather',
    'faux leather': 'Faux Leather',
    'leather': 'Leather',
    'velvet': 'Velvet',
    'upholstered fabric': 'Fabric',
    'fabric': 'Fabric',
    'polyester': 'Polyester',
    'linen': 'Linen',
    'cotton': 'Cotton',
    'boucle': 'Boucle',
    'solid wood': 'Solid Wood',
    'engineered wood': 'Engineered Wood',
    'oak': 'Oak Wood',
    'walnut': 'Walnut Wood',
    'teak': 'Teak Wood',
    'mahogany': 'Mahogany Wood',
    'pine': 'Pine Wood',
    'rattan': 'Rattan',
    'wicker': 'Wicker',
    'stainless steel': 'Stainless Steel',
    'aluminum': 'Aluminum',
    'steel': 'Steel',
    'iron': 'Iron',
    'metal': 'Metal',
    'brass': 'Brass',
    'glass': 'Glass',
    'tempered glass': 'Tempered Glass',
    'marble': 'Marble',
    'ceramic': 'Ceramic',
    'plastic': 'Plastic',
    'acrylic': 'Acrylic',
}

CANONICAL_COLORS = {
    'white': 'White',
    'off-white': 'White',
    'black': 'Black',
    'gray': 'Gray',
    'grey': 'Gray',
    'charcoal': 'Charcoal',
    'brown': 'Brown',
    'beige': 'Beige',
    'cream': 'Cream',
    'ivory': 'Ivory',
    'navy': 'Navy',
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
}

CANONICAL_STYLES = {
    'mid-century modern': 'Mid-Century Modern',
    'mid-century': 'Mid-Century Modern',
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
    'coastal': 'Coastal',
    'art deco': 'Art Deco',
    'vintage': 'Vintage',
    'glam': 'Glam',
}

PATTERNS = ['Heathered', 'Tufted', 'Solid', 'Striped', 'Textured', 'Distressed', 'Channel Tufted']


def normalize_color(raw_color):
    """Extracts canonical color and pattern from composite raw strings like 'Heathered Weave Ivory'."""
    if not raw_color:
        return "", ""
    raw_lower = raw_color.lower().strip()
    detected_color = ""
    detected_pattern = ""

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
        detected_color = raw_color.title()

    return detected_color, detected_pattern


def normalize_material(raw_material):
    """Extracts canonical material from raw descriptions like '100% Polyester Heathered Weave Fabric'."""
    if not raw_material:
        return ""
    raw_lower = raw_material.lower().strip()
    for m_key, m_val in CANONICAL_MATERIALS.items():
        if re.search(r'\b' + re.escape(m_key) + r'\b', raw_lower):
            return m_val
    return raw_material.title()


def extract_attributes(product, category=None):
    """
    Extracts category attributes and their values from a Product instance.
    Returns a dictionary of standardized attributes containing both 'raw' and 'normalized' values.
    
    Structure:
    {
        'Material': { 'raw': 'Bonded Leather', 'normalized': 'Bonded Leather' },
        'Color': { 'raw': 'Heathered Weave Ivory', 'normalized': 'Ivory' },
        ...
    }
    """
    extracted = {}
    combined_text = f"{product.name} {product.materials} {product.product_color} {product.color_collection} {product.bullets} {product.description}"

    # 1. Extract Material
    raw_mat = None
    if product.materials and product.materials.strip():
        raw_mat = product.materials.strip()
    else:
        for m_key, m_val in CANONICAL_MATERIALS.items():
            if re.search(r'\b' + re.escape(m_key) + r'\b', combined_text, re.IGNORECASE):
                raw_mat = m_val
                break
    if raw_mat:
        norm_mat = normalize_material(raw_mat)
        extracted['Material'] = {
            'raw': raw_mat,
            'normalized': norm_mat
        }

    # 2. Extract Color
    raw_color = None
    if product.product_color and product.product_color.strip():
        raw_color = product.product_color.strip()
    elif product.color_collection and product.color_collection.strip():
        raw_color = product.color_collection.strip()
    else:
        for c_key, c_val in CANONICAL_COLORS.items():
            if re.search(r'\b' + re.escape(c_key) + r'\b', combined_text, re.IGNORECASE):
                raw_color = c_val
                break
    if raw_color:
        norm_color, detected_pat = normalize_color(raw_color)
        extracted['Color'] = {
            'raw': raw_color,
            'normalized': norm_color
        }
        if detected_pat:
            extracted['Pattern'] = {
                'raw': raw_color,
                'normalized': detected_pat
            }

    # 3. Extract Style
    raw_style = None
    for st_key, st_val in CANONICAL_STYLES.items():
        if re.search(r'\b' + re.escape(st_key) + r'\b', combined_text, re.IGNORECASE):
            raw_style = st_val
            break
    if raw_style:
        extracted['Style'] = {
            'raw': raw_style,
            'normalized': raw_style
        }

    # 4. Extract Assembly Required
    if product.assembly_required:
        val = str(product.assembly_required).strip().upper()
        if val in ('Y', 'YES', 'TRUE', '1'):
            extracted['Assembly Required'] = {'raw': str(product.assembly_required), 'normalized': 'Yes'}
        elif val in ('N', 'NO', 'FALSE', '0'):
            extracted['Assembly Required'] = {'raw': str(product.assembly_required), 'normalized': 'No'}

    # 5. Extract Is a Set & Set Includes
    if product.is_set:
        val = str(product.is_set).strip().upper()
        if val in ('Y', 'YES', 'TRUE', '1'):
            extracted['Is a Set'] = {'raw': str(product.is_set), 'normalized': 'Yes'}
        elif val in ('N', 'NO', 'FALSE', '0'):
            extracted['Is a Set'] = {'raw': str(product.is_set), 'normalized': 'No'}

    if product.set_includes and str(product.set_includes).strip() not in ('', 'nan'):
        extracted['Set Includes'] = {
            'raw': str(product.set_includes).strip(),
            'normalized': str(product.set_includes).strip()
        }

    # 6. Extract Weight Capacity
    weight_match = re.search(r'weight\s+capacity\s*:?\s*(\d+\s*(?:lbs|lb|kg))', combined_text, re.IGNORECASE)
    if weight_match:
        cap = weight_match.group(1).strip()
        extracted['Weight Capacity'] = {
            'raw': cap,
            'normalized': cap.lower().replace('lb', 'lbs').replace('lbss', 'lbs')
        }

    # 7. Extract Dimensions & Weight
    if product.product_dimensions and str(product.product_dimensions).strip() not in ('', 'nan'):
        dim = str(product.product_dimensions).strip().split('\n')[0][:100]
        extracted['Dimensions'] = {'raw': dim, 'normalized': dim}

    if product.product_weight and str(product.product_weight).strip() not in ('', 'nan'):
        wt = str(product.product_weight).strip()
        extracted['Product Weight'] = {'raw': wt, 'normalized': f"{wt} lbs"}

    # 8. Extract Country of Origin & Brand
    if product.country_of_origin and str(product.country_of_origin).strip() not in ('', 'nan'):
        extracted['Country of Origin'] = {
            'raw': str(product.country_of_origin).strip(),
            'normalized': str(product.country_of_origin).strip().title()
        }

    if getattr(product, 'brand', None) and str(product.brand).strip():
        extracted['Brand'] = {
            'raw': str(product.brand).strip(),
            'normalized': str(product.brand).strip()
        }

    return extracted
