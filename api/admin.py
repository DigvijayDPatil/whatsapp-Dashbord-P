from django.contrib import admin
from .models import SubscriptionPlan, Tenant, CallEventLog, ServerConsoleLog, PaymentRecord

@admin.register(SubscriptionPlan)
class SubscriptionPlanAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug', 'price_rupees', 'duration_days', 'monthly_equivalent', 'discount_badge', 'is_popular', 'is_active', 'order')
    list_editable = ('price_rupees', 'discount_badge', 'is_popular', 'is_active', 'order')
    search_fields = ('name', 'slug', 'subtitle')
    prepopulated_fields = {'slug': ('name',)}
    ordering = ('order', 'duration_days')


@admin.register(Tenant)
class TenantAdmin(admin.ModelAdmin):
    list_display = ('name', 'email', 'license_key', 'plan', 'status', 'created_at', 'expires_at', 'calls_count', 'messages_sent')
    list_filter = ('status', 'plan', 'created_at')
    search_fields = ('name', 'email', 'license_key', 'phone_number_id')
    ordering = ('-created_at',)

@admin.register(PaymentRecord)
class PaymentRecordAdmin(admin.ModelAdmin):
    list_display = ('tenant', 'plan', 'amount', 'status', 'razorpay_order_id', 'razorpay_payment_id', 'created_at')
    list_filter = ('status', 'plan', 'created_at')
    search_fields = ('tenant__name', 'tenant__email', 'razorpay_order_id', 'razorpay_payment_id')
    ordering = ('-created_at',)

@admin.register(CallEventLog)
class CallEventLogAdmin(admin.ModelAdmin):
    list_display = ('tenant_name', 'caller_phone', 'caller_name', 'status', 'duration_ms', 'timestamp')
    list_filter = ('status', 'timestamp')
    search_fields = ('tenant_name', 'caller_phone', 'caller_name', 'license_key')
    ordering = ('-timestamp',)

@admin.register(ServerConsoleLog)
class ServerConsoleLogAdmin(admin.ModelAdmin):
    list_display = ('timestamp', 'level', 'phase', 'message')
    list_filter = ('level', 'phase', 'timestamp')
    search_fields = ('message', 'phase', 'explanation')
    ordering = ('-timestamp',)
