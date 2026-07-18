from django.db import models
import uuid

class Tenant(models.Model):
    PLAN_CHOICES = [
        ('1-month', '1-month'),
        ('3-month', '3-month'),
        ('6-month', '6-month'),
        ('12-month', '12-month'),
    ]
    STATUS_CHOICES = [
        ('active', 'active'),
        ('expired', 'expired'),
        ('blocked', 'blocked'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    email = models.EmailField()
    license_key = models.CharField(max_length=100, unique=True)
    plan = models.CharField(max_length=20, choices=PLAN_CHOICES, default='1-month')
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='active')
    waba_token = models.TextField(blank=True, default='')
    phone_number_id = models.CharField(max_length=100, blank=True, default='')
    template_name = models.CharField(max_length=100, default='missed_call_recovery')
    language_code = models.CharField(max_length=20, default='en_US')
    calls_count = models.IntegerField(default=0)
    messages_sent = models.IntegerField(default=0)

    def __str__(self):
        return f"{self.name} ({self.license_key})"

class CallEventLogQuerySet(models.QuerySet):
    def sla_passes(self):
        return self.filter(
            status__in=CallEventLog.SUCCESS_STATUSES
        )

class CallEventLog(models.Model):
    # SLA Constants
    SLA_THRESHOLD_MS = 3000
    SUCCESS_STATUSES = ['success', 'simulated']
    FAILED_STATUSES = ['failed']
    TRIGGERED_STATUSES = ['success', 'simulated', 'failed']

    STATUS_CHOICES = [
        ('success', 'success'),
        ('failed', 'failed'),
        ('expired', 'expired'),
        ('blocked', 'blocked'),
        ('simulated', 'simulated'),
    ]

    id = models.CharField(primary_key=True, max_length=100, editable=False)
    timestamp = models.DateTimeField()
    license_key = models.CharField(max_length=100)
    tenant_name = models.CharField(max_length=255)
    caller_phone = models.CharField(max_length=50)
    caller_name = models.CharField(max_length=255, default='Customer')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES)
    duration_ms = models.IntegerField()
    details = models.TextField()
    raw_error = models.TextField(blank=True, null=True)

    # Custom Manager
    objects = CallEventLogQuerySet.as_manager()

    @property
    def is_sla_pass(self):
        return self.status in self.SUCCESS_STATUSES

    def __str__(self):
        return f"{self.tenant_name} - {self.caller_phone} ({self.status})"

class ServerConsoleLog(models.Model):
    LEVEL_CHOICES = [
        ('info', 'info'),
        ('warn', 'warn'),
        ('error', 'error'),
    ]

    timestamp = models.DateTimeField(auto_now_add=True)
    level = models.CharField(max_length=10, choices=LEVEL_CHOICES, default='info')
    message = models.TextField()
    phase = models.CharField(max_length=255, blank=True, null=True)
    explanation = models.TextField(blank=True, null=True)

    def __str__(self):
        return f"[{self.level.upper()}] {self.message[:50]}"
