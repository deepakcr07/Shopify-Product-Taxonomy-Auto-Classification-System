import csv
import io
import json
import pandas as pd
from apps.products.models import Product, ClassificationResult


def export_classification_data(export_format='csv', status_filter=None, min_confidence=None):
    """
    Exports classified products with predicted Shopify categories, approved categories,
    review decisions, confidence metrics, normalized attributes, and version metadata.
    """
    qs = ClassificationResult.objects.select_related(
        'product', 'predicted_category', 'approved_category'
    ).all()

    if status_filter and status_filter != 'all':
        qs = qs.filter(status=status_filter)
    if min_confidence is not None and min_confidence != '':
        try:
            qs = qs.filter(confidence_score__gte=float(min_confidence) / 100.0 if float(min_confidence) > 1 else float(min_confidence))
        except ValueError:
            pass

    records = []
    for res in qs:
        prod = res.product
        pred_cat = res.predicted_category
        appr_cat = res.approved_category
        eff_cat = res.effective_category

        # Format category attributes & product specifications
        cat_attrs_formatted = []
        specs_formatted = []

        extracted_dict = res.extracted_attributes or {}
        if 'shopify_category_attributes' in extracted_dict:
            for attr in extracted_dict['shopify_category_attributes']:
                if attr.get('status') == 'detected' or attr.get('normalized_value') or attr.get('raw_value'):
                    aname = attr.get('attribute_name')
                    norm = attr.get('normalized_value') or attr.get('raw_value')
                    raw = attr.get('raw_value')
                    matched = attr.get('matched_shopify_value')
                    if norm and raw and norm.lower() != str(raw).lower():
                        cat_attrs_formatted.append(f"{aname}: {norm} [raw: {raw}]")
                    elif norm:
                        cat_attrs_formatted.append(f"{aname}: {norm}")
        if 'product_specifications' in extracted_dict:
            for spec in extracted_dict['product_specifications']:
                sname = spec.get('name')
                val = spec.get('value')
                if val:
                    specs_formatted.append(f"{sname}: {val}")

        # Fallback to legacy flat formatting if empty
        if not cat_attrs_formatted:
            for k, v in extracted_dict.items():
                if k in ('shopify_category_attributes', 'product_specifications'):
                    continue
                if isinstance(v, dict):
                    norm = v.get('normalized', '')
                    raw = v.get('raw', '')
                    if norm and raw and norm != raw:
                        cat_attrs_formatted.append(f"{k}: {norm} [raw: {raw}]")
                    elif norm:
                        cat_attrs_formatted.append(f"{k}: {norm}")
                elif isinstance(v, str) and v:
                    cat_attrs_formatted.append(f"{k}: {v}")

        attrs_str = "; ".join(cat_attrs_formatted)
        specs_str = "; ".join(specs_formatted)

        records.append({
            'Product Number': prod.product_number,
            'Model Number': prod.model_number,
            'Product Name': prod.name,
            'Brand': prod.brand,
            'Original Category': prod.product_category,
            'Original Sub Category': prod.product_sub_category,
            'Predicted Category GID': pred_cat.id if pred_cat else '',
            'Predicted Category Code': pred_cat.code if pred_cat else '',
            'Predicted Category Path': pred_cat.full_name if pred_cat else 'Unclassified',
            'Approved Category GID': appr_cat.id if appr_cat else '',
            'Approved Category Path': appr_cat.full_name if appr_cat else '',
            'Final Effective Category': eff_cat.full_name if eff_cat else 'Unclassified',
            'Confidence Score': f"{round(res.confidence_score * 100, 1)}%",
            'Classification Status': res.get_status_display(),
            'Review Decision': res.get_review_decision_display() if hasattr(res, 'get_review_decision_display') else res.review_decision,
            'Shopify Category Attributes': attrs_str,
            'Product Specifications': specs_str,
            'Normalized Attributes': attrs_str,
            'Image Status': res.get_image_status_display(),
            'Image URL': prod.image_url,
            'Classifier Version': res.classifier_version or 'v1.2.0',
            'Taxonomy Version': res.taxonomy_version or '2026-02',
            'Reviewed By': res.reviewed_by or '',
            'Review Notes': res.review_notes or '',
            'Processed At': res.processed_at.isoformat() if res.processed_at else '',
        })

    if export_format == 'json':
        return json.dumps(records, indent=2), 'application/json', 'products_export.json'

    df = pd.DataFrame(records)

    if export_format in ('xlsx', 'excel'):
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='Classified Products')
        output.seek(0)
        return output.getvalue(), 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', 'products_export.xlsx'

    # Default to CSV
    output = io.StringIO()
    df.to_csv(output, index=False)
    return output.getvalue(), 'text/csv', 'products_export.csv'
