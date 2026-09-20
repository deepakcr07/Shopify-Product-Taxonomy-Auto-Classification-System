"""
Confidence Scoring and Calibration Engine.
Combines multiple multi-modal signals into a calibrated, explainable confidence score (0.0 to 1.0)
with dynamic weight re-normalization for missing data and detailed human-readable evidence.
"""

def calculate_confidence(
    title_score,
    clue_score,
    desc_score,
    attr_score,
    image_score,
    margin_score,
    has_desc,
    has_image,
    title_text="",
    matched_category_name=""
):
    """
    Computes an explainable, dynamically re-weighted confidence score.

    Baseline Weights:
    - Title Match: 35%
    - Source Category / Sub-Category Clue Match: 25%
    - Description & Bullets Match: 20% (if description present, else 0%)
    - Attribute Consistency: 10%
    - Image Signal: 10% (if image present, else 0%)

    If description or image is absent, remaining weights are re-normalized to 100%.
    """
    # 1. Determine dynamic weights based on available data
    w_title = 0.35
    w_clue = 0.25
    w_desc = 0.20 if has_desc else 0.0
    w_attr = 0.10
    w_img = 0.10 if has_image else 0.0

    total_weight = w_title + w_clue + w_desc + w_attr + w_img
    if total_weight <= 0:
        return 0.0, {'error': 'No available signals'}

    # Normalize weights to sum to 1.0
    norm_w_title = w_title / total_weight
    norm_w_clue = w_clue / total_weight
    norm_w_desc = w_desc / total_weight
    norm_w_attr = w_attr / total_weight
    norm_w_img = w_img / total_weight

    # 2. Weighted component contributions
    c_title = title_score * norm_w_title
    c_clue = clue_score * norm_w_clue
    c_desc = desc_score * norm_w_desc
    c_attr = attr_score * norm_w_attr
    c_img = image_score * norm_w_img

    raw_weighted_score = c_title + c_clue + c_desc + c_attr + c_img

    # 3. Margin boost (separation between #1 candidate and #2 runner-up)
    margin_adjustment = min(0.08, margin_score * 0.12)

    # 4. Completeness factor (slight penalty if both description and image are missing)
    completeness_factor = 1.0
    if not has_desc and not has_image:
        completeness_factor = 0.88
    elif not has_desc or not has_image:
        completeness_factor = 0.96

    final_confidence = min(0.99, max(0.05, (raw_weighted_score + margin_adjustment) * completeness_factor))

    # 5. Build human-readable evidence list
    evidence = []
    if title_score >= 0.70:
        evidence.append(f"✓ Strong title match ({int(title_score*100)}%) for '{matched_category_name}'")
    elif title_score >= 0.40:
        evidence.append(f"✓ Moderate title alignment ({int(title_score*100)}%) with product name")
    else:
        evidence.append(f"⚠ Low title similarity ({int(title_score*100)}%)")

    if clue_score >= 0.70:
        evidence.append(f"✓ Source category clues ({int(clue_score*100)}%) strongly support taxonomy branch")
    elif clue_score >= 0.40:
        evidence.append(f"✓ Source category clues aligned ({int(clue_score*100)}%)")

    if has_desc:
        if desc_score >= 0.60:
            evidence.append(f"✓ Description & bullet keywords match ({int(desc_score*100)}%)")
        else:
            evidence.append(f"• Description provided ({int(desc_score*100)}% match)")
    else:
        evidence.append("ℹ No description provided; weights re-allocated to title and category clues")

    if attr_score >= 0.70:
        evidence.append("✓ Materials and attributes are consistent with category standards")

    if has_image:
        if image_score >= 0.70:
            evidence.append("✓ Primary image available and verified")
        else:
            evidence.append("⚠ Image verification failed or unreachable; fell back to text signals")
    else:
        evidence.append("ℹ No image URL provided; evaluated using full text evidence")

    if margin_score >= 0.50:
        evidence.append("✓ High candidate separation: top prediction clearly distinct from alternatives")

    breakdown = {
        'formula': 'Final = (Σ Normalized_Weight_i * Signal_Score_i + Margin_Bonus) * Completeness_Factor',
        'signals': {
            'title': {
                'score': round(title_score, 3),
                'normalized_weight': round(norm_w_title, 3),
                'contribution': round(c_title, 3)
            },
            'category_clues': {
                'score': round(clue_score, 3),
                'normalized_weight': round(norm_w_clue, 3),
                'contribution': round(c_clue, 3)
            },
            'description': {
                'score': round(desc_score, 3) if has_desc else 0.0,
                'normalized_weight': round(norm_w_desc, 3),
                'contribution': round(c_desc, 3),
                'present': has_desc
            },
            'attributes': {
                'score': round(attr_score, 3),
                'normalized_weight': round(norm_w_attr, 3),
                'contribution': round(c_attr, 3)
            },
            'image': {
                'score': round(image_score, 3) if has_image else 0.0,
                'normalized_weight': round(norm_w_img, 3),
                'contribution': round(c_img, 3),
                'present': has_image
            },
        },
        'margin_bonus': round(margin_adjustment, 3),
        'completeness_factor': round(completeness_factor, 3),
        'raw_weighted_score': round(raw_weighted_score, 3),
        'final_confidence': round(final_confidence, 2),
        'final_percentage': f"{int(round(final_confidence * 100))}%",
        'evidence': evidence
    }

    return round(final_confidence, 2), breakdown
