from django.db import models
import uuid

class SubscriptionPlan(models.Model):
    slug = models.SlugField(max_length=50, unique=True, help_text="Unique identifier (e.g. 1-month, 3-month, 6-month, 12-month)")
    name = models.CharField(max_length=100)
    subtitle = models.CharField(max_length=255, blank=True, default='')
    duration_days = models.IntegerField(default=30)
    price_rupees = models.DecimalField(max_digits=10, decimal_places=2)
    monthly_equivalent = models.CharField(max_length=50, blank=True, default='')
    discount_badge = models.CharField(max_length=100, blank=True, default='')
    is_popular = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    features_list = models.TextField(blank=True, default='', help_text="Enter one feature per line")
    order = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['duration_days', 'order']

    def __str__(self):
        return f"{self.name} (₹{self.price_rupees})"

    def get_features(self):
        if not self.features_list:
            return []
        return [f.strip() for f in self.features_list.strip().split('\n') if f.strip()]


class Tenant(models.Model):
    PLAN_CHOICES = [
        ('1-month', '1-month'),
        ('3-month', '3-month'),
        ('6-month', '6-month'),
        ('12-month', '12-month'),
    ]
    STATUS_CHOICES = [
        ('active', 'active'),
        ('pending', 'pending'),
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

    @property
    def is_pending(self):
        return not self.license_key or self.license_key == 'PENDING_SETUP' or self.license_key.startswith('PENDING_')

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

class PaymentRecord(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name='payments')
    razorpay_order_id = models.CharField(max_length=100, unique=True)
    razorpay_payment_id = models.CharField(max_length=100, blank=True, default='')
    razorpay_signature = models.CharField(max_length=255, blank=True, default='')
    plan = models.CharField(max_length=20)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    status = models.CharField(max_length=20, default='created')
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.tenant.name} - {self.plan} (₹{self.amount}) [{self.status}]"

