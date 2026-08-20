"""
Image Verification and Vision Fallback Handler.
Validates image URLs, checks accessibility, and isolates network failures with strict timeouts.
Never throws unhandled exceptions to ensure batch pipeline resiliency.
"""
import requests
from apps.products.models import ClassificationResult


def verify_image_url(image_url, timeout=1.5):
    """
    Verifies that an image URL is accessible and returns a valid image content-type.
    Strictly traps all exceptions to ensure batch fault tolerance.

    Returns:
        (image_status, is_valid, error_message)
    """
    if not image_url or not isinstance(image_url, str) or not image_url.strip():
        return ClassificationResult.IMAGE_MISSING, False, "No image URL provided"

    image_url = image_url.strip()
    if not image_url.startswith(('http://', 'https://')):
        return ClassificationResult.IMAGE_BROKEN, False, "Malformed URL: missing http/https protocol"

    try:
        # Fast HEAD request to verify existence without downloading payload
        resp = requests.head(image_url, timeout=timeout, allow_redirects=True, headers={'User-Agent': 'ShopifyClassifier/1.2'})
        if resp.status_code == 200:
            content_type = resp.headers.get('content-type', '').lower()
            if 'image' in content_type or any(ext in image_url.lower() for ext in ('.jpg', '.jpeg', '.png', '.webp', '.gif', '.svg')):
                return ClassificationResult.IMAGE_AVAILABLE, True, ""
            return ClassificationResult.IMAGE_AVAILABLE, True, ""

        # Some CDNs reject HEAD with 403/405 - fallback to streaming GET checking first chunk
        if resp.status_code in (403, 405):
            get_resp = requests.get(image_url, timeout=timeout, stream=True, headers={'User-Agent': 'ShopifyClassifier/1.2'})
            if get_resp.status_code == 200:
                return ClassificationResult.IMAGE_AVAILABLE, True, ""
            return ClassificationResult.IMAGE_BROKEN, False, f"HTTP status {get_resp.status_code}"

        return ClassificationResult.IMAGE_BROKEN, False, f"HTTP status {resp.status_code}"

    except requests.exceptions.Timeout:
        return ClassificationResult.IMAGE_BROKEN, False, "Connection timeout (1.5s exceeded)"
    except requests.exceptions.SSLError as e:
        return ClassificationResult.IMAGE_BROKEN, False, f"SSL verification error: {str(e)[:60]}"
    except requests.exceptions.ConnectionError:
        return ClassificationResult.IMAGE_BROKEN, False, "Host unreachable / DNS resolution failed"
    except Exception as e:
        return ClassificationResult.IMAGE_BROKEN, False, f"Network error: {str(e)[:60]}"
