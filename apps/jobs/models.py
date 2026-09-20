import time
from django.db import models
from django.utils import timezone


class BatchJob(models.Model):
    """Tracks batch classification job status, chunked progress, and performance metrics."""
    STATUS_PENDING = 'pending'
    STATUS_RUNNING = 'running'
    STATUS_PAUSED = 'paused'
    STATUS_COMPLETED = 'completed'
    STATUS_FAILED = 'failed'

    STATUS_CHOICES = [
        (STATUS_PENDING, 'Pending'),
        (STATUS_RUNNING, 'Running'),
        (STATUS_PAUSED, 'Paused'),
        (STATUS_COMPLETED, 'Completed'),
        (STATUS_FAILED, 'Failed'),
    ]

    name = models.CharField(max_length=255, default='Batch Classification Job')
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default=STATUS_PENDING, db_index=True)
    total_items = models.PositiveIntegerField(default=0)
    processed_items = models.PositiveIntegerField(default=0)
    auto_classified_items = models.PositiveIntegerField(default=0)
    needs_review_items = models.PositiveIntegerField(default=0)
    failed_items = models.PositiveIntegerField(default=0)
    chunk_size = models.PositiveIntegerField(default=100)
    current_chunk = models.PositiveIntegerField(default=0)
    total_chunks = models.PositiveIntegerField(default=0)
    check_images = models.BooleanField(default=False)
    batch_type = models.CharField(max_length=32, default='full', blank=True)
    resumed_count = models.PositiveIntegerField(default=0)
    retry_count = models.PositiveIntegerField(default=0)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    error_message = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Job #{self.id}: {self.name} ({self.status}) - {self.progress_percentage}%"

    @property
    def progress_percentage(self):
        if self.total_items == 0:
            return 0.0
        if self.status == self.STATUS_COMPLETED:
            return 100.0
        return min(100.0, round((self.processed_items / self.total_items) * 100, 1))

    @property
    def is_active(self):
        return self.status in (self.STATUS_RUNNING, self.STATUS_PENDING)

    @property
    def duration_seconds(self):
        if not self.started_at:
            return 0.0
        end_time = self.completed_at or timezone.now()
        secs = (end_time - self.started_at).total_seconds()
        return max(1.0, round(secs, 1)) if self.status == self.STATUS_COMPLETED and self.processed_items > 0 else max(0.0, round(secs, 1))

    @property
    def throughput_items_per_sec(self):
        dur = self.duration_seconds
        if dur <= 0:
            dur = 1.0
        if self.processed_items == 0:
            return 0.0
        return round(self.processed_items / dur, 1)

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'status': self.status,
            'batch_type': self.batch_type,
            'total_items': self.total_items,
            'processed_items': self.processed_items,
            'auto_classified_items': self.auto_classified_items,
            'needs_review_items': self.needs_review_items,
            'failed_items': self.failed_items,
            'resumed_count': self.resumed_count,
            'retry_count': self.retry_count,
            'progress_percentage': self.progress_percentage,
            'chunk_size': self.chunk_size,
            'current_chunk': self.current_chunk,
            'total_chunks': self.total_chunks,
            'duration_seconds': self.duration_seconds,
            'throughput_ips': self.throughput_items_per_sec,
            'started_at': self.started_at.isoformat() if self.started_at else None,
            'completed_at': self.completed_at.isoformat() if self.completed_at else None,
            'error_message': self.error_message,
        }
