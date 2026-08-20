"""
Asynchronous Batch Job Worker and Queue Service.
Processes product catalogues in chunks with high throughput, pause/resume, and failure recovery.
Guarantees strict item-level fault isolation and idempotent resumption.
"""
import math
import logging
import threading
from django.utils import timezone
from django.db import transaction, connection
from apps.jobs.models import BatchJob
from apps.products.models import ClassificationResult, Product
from apps.classifier.engine import ProductClassifier

logger = logging.getLogger(__name__)

# Registry for controlling running worker threads
_ACTIVE_WORKERS = {}
_PAUSE_FLAGS = {}


def start_batch_classification(job_id=None, chunk_size=100, check_images=False, retry_failed=False):
    """
    Spawns or resumes a background classification worker thread for a given BatchJob.
    """
    if job_id:
        job = BatchJob.objects.get(id=job_id)
    else:
        # Determine items to process
        target_statuses = [ClassificationResult.STATUS_PENDING]
        if retry_failed:
            target_statuses.append(ClassificationResult.STATUS_FAILED)

        total_target = ClassificationResult.objects.filter(status__in=target_statuses).count()
        total_chunks = math.ceil(total_target / chunk_size) if chunk_size > 0 else 1

        job = BatchJob.objects.create(
            name=f"Classification Run #{BatchJob.objects.count() + 1}",
            status=BatchJob.STATUS_PENDING,
            batch_type='retry' if retry_failed else 'full',
            total_items=total_target,
            chunk_size=chunk_size,
            total_chunks=total_chunks,
            check_images=check_images,
        )

    _PAUSE_FLAGS[job.id] = False

    # Launch worker in daemon thread
    worker_thread = threading.Thread(
        target=_process_job_worker,
        args=(job.id, retry_failed),
        name=f"Worker-Job-{job.id}",
        daemon=True
    )
    _ACTIVE_WORKERS[job.id] = worker_thread
    worker_thread.start()

    return job


def pause_batch_job(job_id):
    """Signals a running worker thread to pause gracefully after completing the current chunk."""
    _PAUSE_FLAGS[job_id] = True
    job = BatchJob.objects.filter(id=job_id).first()
    if job and job.status == BatchJob.STATUS_RUNNING:
        job.status = BatchJob.STATUS_PAUSED
        job.save(update_fields=['status'])
    return True


def resume_batch_job(job_id):
    """Resumes a paused or interrupted batch job from where it left off."""
    job = BatchJob.objects.get(id=job_id)
    # Recalculate remaining pending items
    remaining = ClassificationResult.objects.filter(status=ClassificationResult.STATUS_PENDING).count()
    job.total_items = job.processed_items + remaining
    job.total_chunks = job.current_chunk + math.ceil(remaining / max(1, job.chunk_size))
    job.status = BatchJob.STATUS_RUNNING
    job.resumed_count += 1
    job.save()

    _PAUSE_FLAGS[job.id] = False
    worker_thread = threading.Thread(
        target=_process_job_worker,
        args=(job.id, False),
        name=f"Worker-Job-{job.id}",
        daemon=True
    )
    _ACTIVE_WORKERS[job.id] = worker_thread
    worker_thread.start()
    return job


def retry_failed_jobs(job_id=None, chunk_size=100, check_images=False):
    """
    Resets all failed items to pending status and triggers a retry worker job.
    """
    failed_qs = ClassificationResult.objects.filter(status=ClassificationResult.STATUS_FAILED)
    failed_count = failed_qs.count()
    if failed_count == 0:
        return None

    # Increment attempt count and mark as pending for retry
    failed_qs.update(status=ClassificationResult.STATUS_PENDING)

    if job_id:
        job = BatchJob.objects.get(id=job_id)
        job.status = BatchJob.STATUS_RUNNING
        job.retry_count += 1
        job.total_items += failed_count
        job.total_chunks += math.ceil(failed_count / max(1, job.chunk_size))
        job.save()
    else:
        job = BatchJob.objects.create(
            name=f"Retry Failed Run #{BatchJob.objects.count() + 1}",
            status=BatchJob.STATUS_PENDING,
            batch_type='retry',
            total_items=failed_count,
            chunk_size=chunk_size,
            total_chunks=math.ceil(failed_count / chunk_size),
            check_images=check_images,
            retry_count=1
        )

    _PAUSE_FLAGS[job.id] = False
    worker_thread = threading.Thread(
        target=_process_job_worker,
        args=(job.id, True),
        name=f"Worker-Retry-Job-{job.id}",
        daemon=True
    )
    _ACTIVE_WORKERS[job.id] = worker_thread
    worker_thread.start()
    return job


def retry_single_product(product_id, check_image=True):
    """Retries classification for a single product immediately."""
    product = Product.objects.get(id=product_id)
    classifier = ProductClassifier()
    res = product.classification
    res.attempt_count += 1

    try:
        out = classifier.classify_product(product, check_image=check_image)
        res.predicted_category_id = out['category_id']
        res.confidence_score = out['confidence_score']
        res.status = out['status']
        res.alternatives = out['alternatives']
        res.extracted_attributes = out['extracted_attributes']
        res.confidence_breakdown = out['confidence_breakdown']
        res.image_status = out['image_status']
        res.error_log = out['error_log']
        res.last_error = ''
        res.processed_at = timezone.now()
    except Exception as e:
        res.status = ClassificationResult.STATUS_FAILED
        res.last_error = str(e)
        res.error_log = str(e)
        res.processed_at = timezone.now()

    res.save()
    return res


def _process_job_worker(job_id, is_retry=False):
    """Internal worker loop executing chunked classifications with strict fault isolation."""
    connection.close()

    try:
        job = BatchJob.objects.get(id=job_id)
        job.status = BatchJob.STATUS_RUNNING
        if not job.started_at:
            job.started_at = timezone.now()
        job.save(update_fields=['status', 'started_at'])

        classifier = ProductClassifier()
        chunk_size = job.chunk_size or 100

        while True:
            # Check pause flag
            if _PAUSE_FLAGS.get(job_id, False):
                job.status = BatchJob.STATUS_PAUSED
                job.save(update_fields=['status'])
                logger.info(f"Job #{job_id} successfully paused.")
                break

            # Fetch next chunk of pending classifications
            target_statuses = [ClassificationResult.STATUS_PENDING]
            pending_batch = list(
                ClassificationResult.objects.filter(
                    status__in=target_statuses
                ).select_related('product')[:chunk_size]
            )

            if not pending_batch:
                # All items finished
                job.status = BatchJob.STATUS_COMPLETED
                job.completed_at = timezone.now()
                job.save(update_fields=['status', 'completed_at'])
                logger.info(f"Job #{job_id} successfully completed all items.")
                break

            # Process chunk items with isolated exception handling
            chunk_auto = 0
            chunk_review = 0
            chunk_failed = 0

            for res in pending_batch:
                try:
                    product = res.product
                    out = classifier.classify_product(product, check_image=job.check_images)

                    res.predicted_category_id = out['category_id']
                    res.confidence_score = out['confidence_score']
                    res.status = out['status']
                    res.alternatives = out['alternatives']
                    res.extracted_attributes = out['extracted_attributes']
                    res.confidence_breakdown = out['confidence_breakdown']
                    res.image_status = out['image_status']
                    res.error_log = out['error_log']
                    res.last_error = ''
                    res.processed_at = timezone.now()

                    if res.status == ClassificationResult.STATUS_AUTO_CLASSIFIED:
                        chunk_auto += 1
                    elif res.status == ClassificationResult.STATUS_NEEDS_REVIEW:
                        chunk_review += 1
                    elif res.status == ClassificationResult.STATUS_FAILED:
                        chunk_failed += 1

                except Exception as e:
                    # Isolate item failure: do NOT crash worker or batch
                    logger.exception(f"Item error during classification of product {res.product_id}: {e}")
                    res.status = ClassificationResult.STATUS_FAILED
                    res.last_error = str(e)
                    res.error_log = str(e)
                    res.attempt_count += 1
                    res.processed_at = timezone.now()
                    chunk_failed += 1

            # Bulk update chunk results
            with transaction.atomic():
                ClassificationResult.objects.bulk_update(
                    pending_batch,
                    fields=[
                        'predicted_category', 'confidence_score', 'status',
                        'alternatives', 'extracted_attributes', 'confidence_breakdown',
                        'image_status', 'error_log', 'last_error', 'attempt_count',
                        'processed_at'
                    ],
                    batch_size=len(pending_batch)
                )

                # Update job progress metrics
                job.processed_items += len(pending_batch)
                job.auto_classified_items += chunk_auto
                job.needs_review_items += chunk_review
                job.failed_items += chunk_failed
                job.current_chunk += 1
                job.save()

    except Exception as e:
        logger.exception(f"Fatal worker exception for job #{job_id}: {e}")
        try:
            job = BatchJob.objects.get(id=job_id)
            job.status = BatchJob.STATUS_FAILED
            job.error_message = str(e)
            job.save(update_fields=['status', 'error_message'])
        except Exception:
            pass
    finally:
        _ACTIVE_WORKERS.pop(job_id, None)
        _PAUSE_FLAGS.pop(job_id, None)
        connection.close()
