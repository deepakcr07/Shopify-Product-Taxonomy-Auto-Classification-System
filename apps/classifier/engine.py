"""
Core Product Classification Engine.
Implements fast candidate retrieval, taxonomy-aware contextual rules (e.g., Indoor vs Outdoor),
multi-feature ranking, confidence scoring, alternative category suggestions, and robust fallback mechanisms.
"""
import re
import logging
from collections import defaultdict
from rapidfuzz import fuzz
from django.conf import settings
from apps.taxonomy.models import TaxonomyCategory
from apps.classifier.confidence import calculate_confidence
from apps.classifier.attribute_extractor import extract_attributes
from apps.classifier.image_handler import verify_image_url

logger = logging.getLogger(__name__)

INDOOR_KEYWORDS = {
    'living room', 'bedroom', 'dining room', 'indoor', 'office', 'den',
    'kitchen', 'hallway', 'entryway', 'bathroom', 'apartment', 'home office'
}

OUTDOOR_KEYWORDS = {
    'outdoor', 'patio', 'deck', 'garden', 'backyard', 'all-weather',
    'weather resistant', 'water resistant', 'sunbrella', 'poolside',
    'balcony', 'porch', 'terrace', 'gazebo', 'lanai'
}


class TaxonomyIndex:
    """
    In-memory high-performance index for Shopify Product Taxonomy categories.
    Enables sub-millisecond candidate generation across 10,000+ categories.
    """
    _instance = None

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        self.categories = []
        self.id_to_cat = {}
        self.category_names = []
        self.category_paths = []
        self.token_to_cat_ids = defaultdict(set)
        self.is_loaded = False
        self.reload()

    def reload(self):
        """Loads categories from database into memory index."""
        cats = list(TaxonomyCategory.objects.all())
        if not cats:
            return

        self.categories = cats
        self.id_to_cat = {c.id: c for c in cats}
        self.category_names = [c.name for c in cats]
        self.category_paths = [c.full_name for c in cats]

        self.token_to_cat_ids.clear()
        for idx, cat in enumerate(cats):
            tokens = set(re.findall(r'\b[a-zA-Z0-9]{3,}\b', cat.full_name.lower()))
            for token in tokens:
                self.token_to_cat_ids[token].add(idx)

        self.is_loaded = True
        logger.info(f"TaxonomyIndex loaded {len(cats)} categories.")

    def get_candidates(self, query_text, top_k=30):
        """
        Retrieves top candidate matches for a given query text.
        Combines token intersection with fuzzy matching.
        """
        if not self.is_loaded:
            self.reload()
            if not self.is_loaded:
                return []

        tokens = set(re.findall(r'\b[a-zA-Z0-9]{3,}\b', query_text.lower()))
        matched_indices = set()
        for t in tokens:
            if t in self.token_to_cat_ids:
                matched_indices.update(self.token_to_cat_ids[t])

        # If token match is too sparse, search across full list
        candidate_indices = list(matched_indices) if len(matched_indices) >= 5 else list(range(len(self.categories)))

        # Score candidate paths against query
        scored = []
        for idx in candidate_indices:
            cat = self.categories[idx]
            name_score = fuzz.token_set_ratio(query_text, cat.name)
            path_score = fuzz.partial_ratio(query_text, cat.full_name)
            combined_match = (name_score * 0.6) + (path_score * 0.4)
            scored.append((combined_match, cat))

        scored.sort(key=lambda x: x[0], reverse=True)
        return scored[:top_k]


class ProductClassifier:
    """
    Multi-modal classifier that analyzes product metadata, title, clues,
    images, and attributes to identify the optimal Shopify Category.
    """

    def __init__(self, confidence_threshold=None):
        self.index = TaxonomyIndex.get_instance()
        self.confidence_threshold = confidence_threshold or getattr(
            settings, 'CLASSIFIER_SETTINGS', {}
        ).get('CONFIDENCE_THRESHOLD', 0.70)

    def classify_product(self, product, check_image=False):
        """
        Classifies a single product instance.

        Returns a dict:
        {
            'category_id': str,
            'category': TaxonomyCategory,
            'confidence_score': float,
            'status': str,
            'alternatives': list,
            'extracted_attributes': dict,
            'confidence_breakdown': dict,
            'image_status': str,
            'error_log': str,
            'last_error': str
        }
        """
        try:
            # 1. Clean and prepare signals
            title = (product.name or "").strip()
            brand = (getattr(product, 'brand', '') or "").strip()
            cat_clue = (product.product_category or "").strip()
            subcat_clue = (product.product_sub_category or "").strip()
            clues = f"{cat_clue} {subcat_clue}".strip()
            desc = (product.description or "").strip()
            bullets = (product.bullets or "").strip()
            materials = (product.materials or "").strip()

            has_desc = bool(desc or bullets)
            has_image = bool(product.image_url and str(product.image_url).startswith('http'))

            # Context detection: Indoor vs Outdoor
            full_text_lower = f"{title} {clues} {desc} {bullets} {materials}".lower()
            is_explicit_outdoor = any(kw in full_text_lower for kw in OUTDOOR_KEYWORDS)
            is_explicit_indoor = any(kw in full_text_lower for kw in INDOOR_KEYWORDS)

            # Query composite for candidate generation
            query_composite = f"{title} {brand} {clues} {materials}".strip()
            if not query_composite:
                query_composite = f"Item {product.product_number}"

            # 2. Retrieve candidates
            candidate_tuples = self.index.get_candidates(query_composite, top_k=35)
            if not candidate_tuples:
                return self._fallback_unclassified(product, "No matching taxonomy categories found.")

            # 3. Score candidates with multi-signal feature evaluator and contextual rules
            scored_candidates = []
            for initial_score, cat in candidate_tuples:
                cat_full_lower = cat.full_name.lower()
                cat_leaf_lower = cat.name.lower()
                is_cat_outdoor = 'outdoor' in cat_full_lower or 'patio' in cat_full_lower

                # Context Penalty / Boost (Fix for Issue #7: Indoor Sofa getting Outdoor alternatives)
                context_multiplier = 1.0
                if is_explicit_indoor and not is_explicit_outdoor and is_cat_outdoor:
                    context_multiplier = 0.20  # Heavily penalize outdoor categories for indoor items
                elif is_explicit_outdoor and not is_cat_outdoor:
                    context_multiplier = 0.35  # Heavily penalize indoor categories for outdoor items
                elif is_explicit_outdoor and is_cat_outdoor:
                    context_multiplier = 1.25  # Boost outdoor match

                # Title match signal (0.0 to 1.0)
                t_token = fuzz.token_set_ratio(title.lower(), cat_leaf_lower) / 100.0
                t_partial = fuzz.partial_ratio(cat_leaf_lower, title.lower()) / 100.0
                t_path_ratio = fuzz.partial_ratio(title.lower(), cat_full_lower) / 100.0
                title_score = min(1.0, max(t_token, t_partial, ((t_token * 0.7) + (t_path_ratio * 0.3))))

                # Clue match signal (Source Excel Category / Sub-Category)
                clue_score = 0.5  # Neutral default
                if clues:
                    c_leaf = fuzz.token_set_ratio(clues.lower(), cat_leaf_lower) / 100.0
                    c_path = fuzz.partial_ratio(clues.lower(), cat_full_lower) / 100.0
                    c_token_full = fuzz.token_set_ratio(clues.lower(), cat_full_lower) / 100.0
                    clue_score = min(1.0, max(c_leaf, c_path, c_token_full))

                # Description & Bullets keyword match
                desc_score = 0.5
                if has_desc:
                    d_text = f"{desc} {bullets}".lower()
                    d_leaf = fuzz.partial_ratio(cat_leaf_lower, d_text) / 100.0
                    d_token = fuzz.token_set_ratio(d_text, cat_leaf_lower) / 100.0
                    desc_score = min(1.0, max(d_leaf * 1.1, d_token))

                # Attribute consistency
                attr_score = 0.85 if materials else 0.5

                # Image signal
                image_score = 0.85 if has_image else 0.5

                # Calculate composite candidate score with context multiplier
                c_score = (
                    (title_score * 0.40) +
                    (clue_score * 0.30) +
                    (desc_score * 0.15) +
                    (attr_score * 0.15)
                ) * context_multiplier

                scored_candidates.append({
                    'category': cat,
                    'composite_score': c_score,
                    'title_score': title_score,
                    'clue_score': clue_score,
                    'desc_score': desc_score,
                    'attr_score': attr_score,
                    'image_score': image_score,
                    'is_outdoor': is_cat_outdoor
                })

            # Sort candidates by composite score descending
            scored_candidates.sort(key=lambda x: x['composite_score'], reverse=True)
            top_cand = scored_candidates[0]
            second_cand = scored_candidates[1] if len(scored_candidates) > 1 else None

            # Calculate margin between #1 and #2
            margin = (top_cand['composite_score'] - second_cand['composite_score']) if second_cand else 0.5
            margin_norm = max(0.0, min(1.0, margin * 2.0))

            # 4. Optional Image Verification
            image_status = product.classification.image_status if hasattr(product, 'classification') else 'unchecked'
            image_err = ""
            if check_image and product.image_url:
                image_status, is_valid_img, image_err = verify_image_url(product.image_url)
                if not is_valid_img:
                    top_cand['image_score'] = 0.35
            elif not has_image:
                image_status = 'missing'
            else:
                image_status = 'available'

            # 5. Final Calibrated Confidence Score
            final_conf, breakdown = calculate_confidence(
                title_score=top_cand['title_score'],
                clue_score=top_cand['clue_score'],
                desc_score=top_cand['desc_score'],
                attr_score=top_cand['attr_score'],
                image_score=top_cand['image_score'],
                margin_score=margin_norm,
                has_desc=has_desc,
                has_image=has_image,
                title_text=title,
                matched_category_name=top_cand['category'].name
            )

            # 6. Build Top 3-4 High Quality Alternatives
            alternatives = []
            seen_cat_ids = {top_cand['category'].id}

            # Filter candidates to avoid penalized outdoor noise for indoor items
            valid_alternatives = [
                c for c in scored_candidates[1:]
                if c['category'].id not in seen_cat_ids and c['composite_score'] > 0.15
            ]

            for cand in valid_alternatives[:4]:
                cat_obj = cand['category']
                seen_cat_ids.add(cat_obj.id)
                ratio = cand['composite_score'] / max(0.01, top_cand['composite_score'])
                alt_conf = round(max(0.05, min(0.95, final_conf * ratio * 0.88)), 3)
                alternatives.append({
                    'id': cat_obj.id,
                    'name': cat_obj.name,
                    'full_name': cat_obj.full_name,
                    'code': cat_obj.code,
                    'confidence': alt_conf,
                    'confidence_percent': f"{int(alt_conf * 100)}%"
                })

            # 7. Extract & Normalize Category Attributes
            extracted_attrs = extract_attributes(product, top_cand['category'])

            # 8. Determine Status
            status = 'auto_classified' if final_conf >= self.confidence_threshold else 'needs_review'

            return {
                'category_id': top_cand['category'].id,
                'category': top_cand['category'],
                'confidence_score': final_conf,
                'status': status,
                'alternatives': alternatives,
                'extracted_attributes': extracted_attrs,
                'confidence_breakdown': breakdown,
                'image_status': image_status,
                'error_log': image_err,
                'last_error': ''
            }

        except Exception as e:
            logger.exception(f"Error classifying product {product.id}: {e}")
            return self._fallback_unclassified(product, str(e))

    def _fallback_unclassified(self, product, error_msg):
        """Fallback handler for edge cases and errors without crashing batch execution."""
        return {
            'category_id': None,
            'category': None,
            'confidence_score': 0.0,
            'status': 'failed' if error_msg else 'needs_review',
            'alternatives': [],
            'extracted_attributes': extract_attributes(product),
            'confidence_breakdown': {
                'error': error_msg,
                'evidence': [f"Classification error: {error_msg}"]
            },
            'image_status': 'broken' if product.image_url else 'missing',
            'error_log': error_msg,
            'last_error': error_msg
        }
