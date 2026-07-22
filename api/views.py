import os
import json
import uuid
import datetime
from datetime import timedelta

import calendar
import time
import requests
import random
import re
import razorpay
from django.shortcuts import render, redirect, get_object_or_404

from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.conf import settings
from django.views.decorators.csrf import csrf_exempt
from django.http import HttpResponse, JsonResponse, Http404, HttpResponseForbidden
from django.db.models import Sum, Count, Q, Avg
from django.contrib.auth import login, logout, authenticate
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User

from .models import Tenant, CallEventLog, ServerConsoleLog, PaymentRecord


# Helper to add console logs
def add_system_log(level, message, phase=None, explanation=None):
    log = ServerConsoleLog.objects.create(
        level=level,
        message=message,
        phase=phase,
        explanation=explanation
    )
    # Prune logs to keep only latest 100
    count = ServerConsoleLog.objects.count()
    if count > 100:
        ids_to_keep = ServerConsoleLog.objects.order_by('-timestamp')[:100].values_list('id', flat=True)
        ServerConsoleLog.objects.exclude(id__in=ids_to_keep).delete()
    return log

def get_user_tenant(user, request=None):
    """Returns the Tenant record for non-superusers, or the impersonated Tenant if session exists, or None."""
    if request and user and user.is_superuser:
        impersonate_id = request.session.get('impersonate_tenant_id')
        if impersonate_id:
            return Tenant.objects.filter(id=impersonate_id).first()
            
    if user and user.is_authenticated and not user.is_superuser:
        return Tenant.objects.filter(email__iexact=user.email).first() or Tenant.objects.first()
    return None


# Precision month adder
def add_months(sourcedate, months):
    month = sourcedate.month - 1 + months
    year = sourcedate.year + month // 12
    month = month % 12 + 1
    day = min(sourcedate.day, calendar.monthrange(year, month)[1])
    return datetime.datetime(year, month, day, sourcedate.hour, sourcedate.minute, sourcedate.second, tzinfo=sourcedate.tzinfo)

# Synchronize database state to data.json file automatically
def sync_db_to_data_json():
    try:
        json_path = os.path.join(settings.BASE_DIR, 'data.json')
        
        tenants = []
        for t in Tenant.objects.all().order_by('-created_at'):
            tenants.append({
                "id": str(t.id),
                "name": t.name,
                "email": t.email,
                "licenseKey": t.license_key,
                "plan": t.plan,
                "createdAt": t.created_at.isoformat() if t.created_at else None,
                "expiresAt": t.expires_at.isoformat() if t.expires_at else None,
                "status": t.status,
                "wabaToken": t.waba_token,
                "phoneNumberId": t.phone_number_id,
                "templateName": t.template_name,
                "languageCode": t.language_code,
                "callsCount": t.calls_count,
                "messagesSent": t.messages_sent
            })
            
        call_history = []
        for h in CallEventLog.objects.all().order_by('-timestamp'):
            call_history.append({
                "id": h.id,
                "timestamp": h.timestamp.isoformat() if h.timestamp else None,
                "licenseKey": h.license_key,
                "tenantName": h.tenant_name,
                "callerPhone": h.caller_phone,
                "callerName": h.caller_name,
                "status": h.status,
                "durationMs": h.duration_ms,
                "details": h.details,
                "rawError": h.raw_error
            })
            
        data = {
            "tenants": tenants,
            "callHistory": call_history
        }
        
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
            
        add_system_log(
            'info',
            'Synchronized live database changes back to data.json backup storage.',
            'Phase 2: Core Server Engine'
        )
    except Exception as e:
        print(f"Failed to sync database to data.json: {str(e)}")

# Seed/migrate data from original data.json
def migrate_existing_data_if_needed():
    if Tenant.objects.count() == 0:
        json_path = os.path.join(settings.BASE_DIR, 'data.json')
        if os.path.exists(json_path):
            try:
                with open(json_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                
                tenants_list = data.get('tenants', [])
                for t in tenants_list:
                    c_at = parse_datetime(t.get('createdAt')) or timezone.now()
                    e_at = parse_datetime(t.get('expiresAt')) or timezone.now()
                    
                    Tenant.objects.create(
                        id=uuid.UUID(t.get('id')),
                        name=t.get('name'),
                        email=t.get('email'),
                        license_key=t.get('licenseKey'),
                        plan=t.get('plan'),
                        created_at=c_at,
                        expires_at=e_at,
                        status=t.get('status', 'active'),
                        waba_token=t.get('wabaToken', ''),
                        phone_number_id=t.get('phoneNumberId', ''),
                        template_name=t.get('templateName', 'missed_call_recovery'),
                        language_code=t.get('languageCode', 'en_US'),
                        calls_count=t.get('callsCount', 0),
                        messages_sent=t.get('messagesSent', 0)
                    )
                
                history_list = data.get('callHistory', [])
                for h in history_list:
                    # Use the actual timestamp from the data (preserves the real call/missed call time)
                    ts = parse_datetime(h.get('timestamp')) or timezone.now()
                    CallEventLog.objects.create(
                        id=h.get('id', str(uuid.uuid4())),
                        timestamp=ts,
                        license_key=h.get('licenseKey'),
                        tenant_name=h.get('tenantName'),
                        caller_phone=h.get('callerPhone'),
                        caller_name=h.get('callerName', 'Customer'),
                        status=h.get('status'),
                        duration_ms=h.get('durationMs'),
                        details=h.get('details'),
                        raw_error=h.get('rawError')
                    )
                
                add_system_log(
                    'info',
                    f'Successfully migrated {len(tenants_list)} tenants and {len(history_list)} call logs from data.json.',
                    'Phase 1: Database Setup',
                    'Migration script parsed and loaded raw JSON data directly into local sqlite database.'
                )
                return
            except Exception as e:
                add_system_log(
                    'error',
                    f'Error migrating from data.json: {str(e)}. Falling back to default seeding.',
                    'Phase 1: Database Setup'
                )
        
        # Fallback to seed defaults
        seed_default_tenants()

# Default seed tenants helper
def seed_default_tenants():
    if Tenant.objects.count() == 0:
        add_system_log(
            'info',
            'No active tenants found. Seeding database with default accounts...',
            'Phase 1: Database Setup',
            'Local store is empty. Auto-populating default businesses for instant local testing.'
        )
        
        now = timezone.now()
        active_expiry = now + datetime.timedelta(days=30)
        expired_expiry = now - datetime.timedelta(days=5)
        
        Tenant.objects.create(
            id=uuid.UUID('1243a6d3-34b5-4744-baa9-7a85babf2e45'),
            name='Apex Car Rentals',
            email='billing@apexrentals.com',
            license_key='LIC-APEX-8821',
            plan='1-month',
            created_at=now,
            expires_at=active_expiry,
            status='active',
            waba_token='EAAG9zZA89ZA0IBACF3qZCZC7ZA9ZBqW4S0vZCvj',
            phone_number_id='109552145399882',
            template_name='lead_follow_up_instant',
            language_code='en_US',
            calls_count=14,
            messages_sent=12
        )
        
        Tenant.objects.create(
            id=uuid.UUID('527b50d9-d04f-4c40-b0d4-daace55d1934'),
            name='Prime Real Estate Group',
            email='leads@primerealty.com',
            license_key='LIC-PRIME-4402',
            plan='1-month',
            created_at=now - datetime.timedelta(days=35),
            expires_at=expired_expiry,
            status='expired',
            waba_token='EAAG9zZA89ZA0IBAK7K4Nf3eH9L9W1vCfZ8O0x9',
            phone_number_id='209332145300122',
            template_name='missed_call_recovery',
            language_code='en_US',
            calls_count=38,
            messages_sent=38
        )
        
        add_system_log(
            'info',
            'Database seeding completed successfully. Created "Apex Car Rentals" and "Prime Real Estate Group".',
            'Phase 1: Database Setup'
        )

# Extract a Meta Cloud API error message
def build_meta_failure_message(raw_details):
    fallback = f"Meta API transmission failure: {raw_details}"
    try:
        parsed = json.loads(raw_details)
    except:
        match = re.search(r'\{[\s\S]*\}', raw_details)
        if match:
            try:
                parsed = json.loads(match.group(0))
            except:
                parsed = None
        else:
            parsed = None

    inner = parsed.get('error') if parsed and isinstance(parsed, dict) else parsed
    if inner and isinstance(inner, dict) and isinstance(inner.get('message'), str):
        code_str = ""
        if inner.get('code') is not None:
            subcode = f" / subcode {inner.get('error_subcode')}" if inner.get('error_subcode') is not None else ""
            code_str = f" (code {inner.get('code')}{subcode})"
        
        msg = inner.get('message')
        short_msg = msg[:220] + '…' if len(msg) > 220 else msg
        return f"Meta API transmission failure{code_str}: {short_msg}  [{inner.get('type', 'GraphAPIError')}]"
    
    if isinstance(inner, str) and len(inner) > 0:
        return f"Meta API transmission failure: {inner[:220] + '…' if len(inner) > 220 else inner}"
    
    return fallback


def standardize_phone_number(phone_str):
    if not phone_str:
        return ""
    clean = re.sub(r'[^0-9]', '', phone_str)
    if len(clean) == 10:
        clean = '91' + clean
    elif len(clean) == 11 and clean.startswith('0'):
        clean = '91' + clean[1:]
    return clean


# --- Shared data computation for overview ---
def compute_overview_stats(user=None):
    """Compute all overview dashboard statistics.
    Used by both the page view and the JSON endpoint to avoid duplication."""
    migrate_existing_data_if_needed()
    
    is_tenant = False
    tenant = None
    if user and not user.is_superuser:
        tenant = Tenant.objects.filter(email__iexact=user.email).first()
        if tenant:
            is_tenant = True

    if is_tenant:
        total_tenants = 1
        active_tenants = 1 if tenant.status == 'active' and tenant.expires_at > timezone.now() else 0
        expired_or_blocked_count = 1 if tenant.status in ['blocked', 'expired'] or tenant.expires_at <= timezone.now() else 0
        total_calls = tenant.calls_count
        total_messages = tenant.messages_sent
        
        tenant_logs = CallEventLog.objects.filter(license_key=tenant.license_key)
        recent_history = list(tenant_logs.order_by('-timestamp')[:15])
    else:
        total_tenants = Tenant.objects.count()
        active_tenants = Tenant.objects.filter(status='active', expires_at__gt=timezone.now()).count()
        expired_or_blocked_count = Tenant.objects.filter(Q(status='blocked') | Q(status='expired') | Q(expires_at__lte=timezone.now())).distinct().count()
        total_calls = Tenant.objects.aggregate(total=Sum('calls_count'))['total'] or 0
        total_messages = Tenant.objects.aggregate(total=Sum('messages_sent'))['total'] or 0
        
        recent_history = list(CallEventLog.objects.all().order_by('-timestamp')[:15])

    # Calculate SLA & Avg response based on the rolling window of the last 15 webhooks shown on the chart
    total_logs_count = len(recent_history)
    sla_passed_count = sum(1 for item in recent_history if item.is_sla_pass)
    
    sla_percentage = int((sla_passed_count / total_logs_count * 100)) if total_logs_count > 0 else 100
    sla_dashoffset = float(2 * 3.1415926535 * 44 * (1 - sla_percentage / 100))
    avg_latency = int(sum(item.duration_ms for item in recent_history) / total_logs_count) if total_logs_count > 0 else 0

    # Fetch last 15 call events
    recent_history_list = []
    for item in reversed(recent_history):
        # Apply a square-root power scale with a 15% baseline boost so that low-latency webhooks (e.g. 100-300ms) look substantial and visually tall in the chart.
        ratio = min(item.duration_ms / 4000, 1.0)
        percent = 15 + (ratio ** 0.5) * 85
        recent_history_list.append({
            'id': str(item.id)[:8],
            'tenant_name': item.tenant_name,
            'duration_ms': item.duration_ms,
            'status': item.status,
            'percent': round(percent, 1),
            # Check SLA eligibility using encapsulated Model Property helper
            'is_under_sla': item.is_sla_pass,
        })

    # Ensure system boot log exists
    if ServerConsoleLog.objects.count() == 0:
        add_system_log(
            'info',
            'Super Admin Server Engine initialized successfully.',
            'Phase 2: Core Server Engine',
            'Django Server booted and listening on Localhost. Template engine and system modules fully active.'
        )

    if is_tenant:
        # Prevent data leaks by excluding system logs that reference other tenants
        other_tenants_names = Tenant.objects.exclude(id=tenant.id).values_list('name', flat=True)
        logs_query = ServerConsoleLog.objects.all()
        for name in other_tenants_names:
            logs_query = logs_query.exclude(message__icontains=name)
        logs = logs_query.order_by('-timestamp')[:50]
    else:
        logs = ServerConsoleLog.objects.all().order_by('-timestamp')[:50]
    formatted_logs = []
    for log in logs:
        local_ts = timezone.localtime(log.timestamp) if timezone.is_aware(log.timestamp) else log.timestamp
        formatted_logs.append({
            'timestamp_str': local_ts.strftime("%Y-%m-%d %H:%M:%S"),
            'timestamp_time': local_ts.strftime("%H:%M:%S.%f")[:-3],
            'level': log.level,
            'phase': log.phase or 'Core Engine',
            'message': log.message,
            'explanation': log.explanation or 'No explanation available for this log.'
        })

    return {
        'total_tenants': total_tenants,
        'active_tenants': active_tenants,
        'expired_or_blocked_count': expired_or_blocked_count,
        'total_calls': total_calls,
        'total_messages': total_messages,
        'sla_percentage': sla_percentage,
        'sla_dashoffset': sla_dashoffset,
        'avg_latency': avg_latency,
        'recent_history': recent_history_list,
        'logs': formatted_logs,
    }


# --- PAGE VIEWS ---

@login_required
def overview_view(request):
    if request.user.is_superuser and 'impersonate_tenant_id' in request.session:
        request.session.pop('impersonate_tenant_id', None)
        return redirect('overview')

    is_tenant = False
    tenant = None
    if not request.user.is_superuser:
        tenant = Tenant.objects.filter(email__iexact=request.user.email).first()
        if tenant:
            is_tenant = True

    stats = compute_overview_stats(request.user)
    
    if is_tenant:
        tenants = Tenant.objects.filter(id=tenant.id)
        history = CallEventLog.objects.filter(license_key=tenant.license_key).order_by('-timestamp')
    else:
        tenants = Tenant.objects.all().order_by('-created_at')
        history = CallEventLog.objects.all().order_by('-timestamp')

    context = {
        'active_page': 'overview',
        **stats,
        'tenants': tenants,
        'history': history,
    }
    return render(request, 'api/overview.html', context)


@login_required
def tenants_view(request):
    migrate_existing_data_if_needed()
    user_tenant = get_user_tenant(request.user, request)

    
    if request.method == 'POST':
        if not request.user.is_superuser:
            return HttpResponseForbidden("SuperAdmin access required to register new subscribers.")

            
        # Add new tenant
        name = request.POST.get('name')
        email = request.POST.get('email', '').strip()
        plan = request.POST.get('plan')
        waba_token = request.POST.get('waba_token', '').strip()
        phone_number_id = request.POST.get('phone_number_id', '').strip()
        template_name = request.POST.get('template_name', '').strip() or 'missed_call_recovery'
        language_code = request.POST.get('language_code', '').strip() or 'en_US'

        if name and email and plan and waba_token and phone_number_id and template_name:
            now = timezone.now()
            
            # Calculate expiry
            if plan == '1-month':
                expires_at = add_months(now, 1)
            elif plan == '3-month':
                expires_at = add_months(now, 3)
            elif plan == '6-month':
                expires_at = add_months(now, 6)
            elif plan == '12-month':
                expires_at = add_months(now, 12)
            else:
                expires_at = now + datetime.timedelta(days=30)

            # Generate unique license key
            clean_name = re.sub(r'[^a-zA-Z0-9]', '', name)[:5].upper()
            random_suffix = random.randint(1000, 9999)
            license_key = f"LIC-{clean_name}-{random_suffix}"

            Tenant.objects.create(
                name=name,
                email=email,
                plan=plan,
                license_key=license_key,
                expires_at=expires_at,
                waba_token=waba_token,
                phone_number_id=phone_number_id,
                template_name=template_name,
                language_code=language_code,
                status='active'
            )

            # Auto-create user account if not exists
            if not User.objects.filter(email__iexact=email).exists():
                clean_username = re.sub(r'[^a-zA-Z0-9_]', '', name.lower().replace(' ', '_'))[:20] or "user"
                if User.objects.filter(username=clean_username).exists():
                    clean_username = f"{clean_username}_{random.randint(100, 999)}"
                User.objects.create_user(username=clean_username, email=email, password='Password123!')
                add_system_log('info', f"Auto-created subscriber login account '{clean_username}' for {email}")

            add_system_log(
                'info',
                f'Registered Tenant "{name}" with License Key: {license_key}',
                'Phase 2: Core Server Engine',
                f'POST Form submitted. Django automatically calculated the expiry to {expires_at.strftime("%Y-%m-%d")} for the {plan} subscription and saved the record.'
            )
            sync_db_to_data_json()
            return redirect('tenants')

    if user_tenant:
        tenants = Tenant.objects.filter(id=user_tenant.id)
    else:
        tenants = Tenant.objects.all().order_by('-created_at')

    context = {
        'active_page': 'tenants',
        'tenants': tenants,
        'user_tenant': user_tenant,
    }
    return render(request, 'api/tenants.html', context)



@login_required
def tenant_edit_waba_view(request, pk):
    migrate_existing_data_if_needed()
    try:
        tenant = Tenant.objects.get(id=pk)
    except Tenant.DoesNotExist:
        raise Http404("Tenant not found")
        
    user_tenant = get_user_tenant(request.user)
    if user_tenant and user_tenant.id != tenant.id:
        return HttpResponseForbidden("You can only modify your own WABA settings.")

    if request.method == 'POST':
        tenant.waba_token = request.POST.get('waba_token', '').strip()
        tenant.phone_number_id = request.POST.get('phone_number_id', '').strip()
        tenant.template_name = request.POST.get('template_name', '').strip() or 'missed_call_recovery'
        tenant.language_code = request.POST.get('language_code', '').strip() or 'en_US'
        tenant.save()
        
        add_system_log(
            'info',
            f'Updated WABA Settings for Tenant: "{tenant.name}"',
            'Phase 2: Core Server Engine',
            f'Updated Meta WABA details for tenant record secure isolation.'
        )
        sync_db_to_data_json()
    return redirect('tenants')


@login_required
def tenant_toggle_block_view(request, pk):
    if not request.user.is_superuser:
        return HttpResponseForbidden("SuperAdmin privileges required.")
    migrate_existing_data_if_needed()
    try:
        tenant = Tenant.objects.get(id=pk)
    except Tenant.DoesNotExist:
        raise Http404("Tenant not found")
        
    tenant.status = 'active' if tenant.status == 'blocked' else 'blocked'
    tenant.save()
    
    add_system_log(
        'info',
        f'Toggled Status for Tenant "{tenant.name}" to {tenant.status.upper()}',
        'Phase 2: Core Server Engine'
    )
    sync_db_to_data_json()
    return redirect('tenants')


@login_required
def tenant_extend_view(request, pk):
    if not request.user.is_superuser:
        return HttpResponseForbidden("SuperAdmin privileges required.")
    migrate_existing_data_if_needed()
    try:
        tenant = Tenant.objects.get(id=pk)
    except Tenant.DoesNotExist:
        raise Http404("Tenant not found")
        
    current_expiry = tenant.expires_at
    base_date = current_expiry if current_expiry > timezone.now() else timezone.now()
    new_expiry = add_months(base_date, 1)
    
    tenant.expires_at = new_expiry
    tenant.status = 'active'
    
    import re
    plan_name = tenant.plan
    match = re.match(r'(\d+)-month', plan_name)
    if match:
        months = int(match.group(1))
        tenant.plan = f"{months + 1}-month"
    else:
        tenant.plan = "2-month"
        
    tenant.save()
    
    add_system_log(
        'info',
        f'Extended plan subscription (+1 month) for Tenant "{tenant.name}". New Expiry: {new_expiry.strftime("%Y-%m-%d")}',
        'Phase 2: Core Server Engine'
    )
    sync_db_to_data_json()
    return redirect('tenants')


@login_required
def tenant_delete_view(request, pk):
    if not request.user.is_superuser:
        return HttpResponseForbidden("SuperAdmin privileges required.")
    migrate_existing_data_if_needed()
    try:
        tenant = Tenant.objects.get(id=pk)
    except Tenant.DoesNotExist:
        return JsonResponse({'success': False, 'message': 'Tenant not found or already deleted.'}, status=404)
        
    if request.method == 'POST':
        confirm_password = request.POST.get('confirm_password', '')
        
        if request.user.check_password(confirm_password):
            name = tenant.name
            license_key = tenant.license_key
            tenant_email = tenant.email
            
            # Cascade delete all call logs associated with this license key
            CallEventLog.objects.filter(license_key=license_key).delete()
            
            # Delete matching User login account(s)
            deleted_users_count = User.objects.filter(email__iexact=tenant_email, is_superuser=False).delete()[0]
            
            tenant.delete()
            
            add_system_log(
                'info',
                f'Permanently deleted Tenant record "{name}" ({tenant_email}) and removed {deleted_users_count} associated user login account(s).',
                'Phase 2: Core Server Engine'
            )
            sync_db_to_data_json()
            return JsonResponse({'success': True})
        else:
            return JsonResponse({'success': False, 'message': 'Incorrect administrator password entered. Please try again.'}, status=400)
            
    return redirect('tenants')



@login_required
def history_view(request):
    migrate_existing_data_if_needed()
    user_tenant = get_user_tenant(request.user, request)
    if user_tenant:
        history = CallEventLog.objects.filter(license_key=user_tenant.license_key).order_by('-timestamp')
    else:
        history = CallEventLog.objects.all().order_by('-timestamp')
    context = {
        'active_page': 'history',
        'history': history,
        'user_tenant': user_tenant,
    }
    return render(request, 'api/history.html', context)


@login_required
def seed_history_view(request):
    migrate_existing_data_if_needed()
    user_tenant = get_user_tenant(request.user, request)

    if request.method == 'POST':
        suffix = ''.join(random.choices('abcdefghijklmnopqrstuvwxyz0123456789', k=5))
        now = timezone.now()
        
        target_license_key = user_tenant.license_key if (user_tenant and user_tenant.license_key) else 'LIC-DEMO-9900'
        target_name = user_tenant.name if user_tenant else 'Apex Car Rentals'

        dummy_logs = [
            {
                'id': f"tx_98s21a_{suffix}",
                'timestamp': now - datetime.timedelta(minutes=4),
                'license_key': target_license_key,
                'tenant_name': target_name,
                'caller_phone': '+14155552671',
                'caller_name': 'Sarah Jenkins',
                'status': 'simulated',
                'duration_ms': 242,
                'details': f'SIMULATED: Mock seed history entry for {target_name}.'
            },
            {
                'id': f"tx_12f90b_{suffix}",
                'timestamp': now - datetime.timedelta(minutes=15),
                'license_key': target_license_key,
                'tenant_name': target_name,
                'caller_phone': '+13125559082',
                'caller_name': 'Marcus Vance',
                'status': 'simulated',
                'duration_ms': 195,
                'details': f'SIMULATED: Mock seed history entry for {target_name}.'
            },
            {
                'id': f"tx_44f12z_{suffix}",
                'timestamp': now - datetime.timedelta(minutes=32),
                'license_key': target_license_key,
                'tenant_name': target_name,
                'caller_phone': '+16505557711',
                'caller_name': 'Elizabeth Holmes',
                'status': 'simulated',
                'duration_ms': 180,
                'details': f'SIMULATED: Mock seed history entry for {target_name}.'
            }
        ]

        created_count = 0
        for log_data in dummy_logs:
            if not CallEventLog.objects.filter(id=log_data['id']).exists():
                CallEventLog.objects.create(**log_data)
                created_count += 1
                
        add_system_log(
            'info',
            f'Seeded {created_count} call logs successfully for "{target_name}".',
            'Phase 5: Super Admin Control Console'
        )
        sync_db_to_data_json()
        return redirect('history')

    return redirect('history')



@login_required
def sandbox_view(request):
    migrate_existing_data_if_needed()
    user_tenant = get_user_tenant(request.user, request)
    if user_tenant:
        tenants = Tenant.objects.filter(id=user_tenant.id)
    else:
        tenants = Tenant.objects.all().order_by('name')
    context = {
        'active_page': 'sandbox',
        'tenants': tenants,
        'user_tenant': user_tenant,
    }
    return render(request, 'api/sandbox.html', context)



@csrf_exempt
@login_required
def run_simulation_view(request):
    if request.method == 'POST':
        user_tenant = get_user_tenant(request.user)
        if user_tenant:
            license_key = user_tenant.license_key
        else:
            license_key = request.POST.get('license_key')

        caller_phone = request.POST.get('caller_phone', '+1 (415) 555-2345')
        caller_name = request.POST.get('caller_name', 'David Miller')

        if not license_key:
            return JsonResponse({'error': 'Please select or enter a license key'}, status=400)


        # Trigger internal webhook handler locally / internally
        # We can construct the call request directly to handle_call_event endpoint functionality
        start_time = time.time()
        
        # Prepare parameters mock requests response structure
        # To hit handle_call_event locally:
        try:
            tenant = Tenant.objects.filter(license_key=license_key).first()
            if not tenant:
                duration_ms = int((time.time() - start_time) * 1000)
                CallEventLog.objects.create(
                    id=str(uuid.uuid4()),
                    timestamp=timezone.now(),
                    license_key=license_key,
                    tenant_name='Unknown Business',
                    caller_phone=caller_phone,
                    caller_name=caller_name,
                    status='failed',
                    duration_ms=duration_ms,
                    details='Invalid License Key provided.'
                )
                add_system_log('warn', f'Rejected simulation. Invalid License: {license_key}', 'Phase 3: Subscription & Expiry Logic')
                return JsonResponse({
                    'status': 401,
                    'ok': False,
                    'data': {
                        'success': False,
                        'status': 'unauthorized',
                        'message': 'Invalid license key. Please check your admin configuration.'
                    }
                })

            expiry_date = tenant.expires_at
            current_date = timezone.now()

            if tenant.status == 'blocked' or current_date > expiry_date:
                duration_ms = int((time.time() - start_time) * 1000)
                current_status = tenant.status
                if current_status != 'blocked' and current_date > expiry_date:
                    current_status = 'expired'
                    tenant.status = 'expired'
                    tenant.save()

                CallEventLog.objects.create(
                    id=str(uuid.uuid4()),
                    timestamp=timezone.now(),
                    license_key=license_key,
                    tenant_name=tenant.name,
                    caller_phone=caller_phone,
                    caller_name=caller_name,
                    status='blocked' if current_status == 'blocked' else 'expired',
                    duration_ms=duration_ms,
                    details='Account manually blocked by Super Admin.' if current_status == 'blocked' else 'License plan exceeded subscription duration.'
                )
                
                tenant.calls_count += 1
                tenant.save()
                
                add_system_log('warn', f'Simulation block. License Blocked/Expired: {license_key}', 'Phase 3: Subscription & Expiry Logic')
                return JsonResponse({
                    'status': 403,
                    'ok': False,
                    'data': {
                        'success': False,
                        'status': current_status,
                        'message': 'SaaS access blocked by administrator.' if current_status == 'blocked' else 'Subscription plan expired. Please extend license validity.'
                    }
                })

            # Active! Trigger WhatsApp logic
            waba_token = tenant.waba_token
            phone_number_id = tenant.phone_number_id.strip()
            template_name = tenant.template_name
            language_code = tenant.language_code or 'en_US'
            
            clean_phone = standardize_phone_number(caller_phone)
            # Real Meta WhatsApp tokens are 150+ chars; seed tokens are ~41 chars
            is_mock_token = not waba_token or len(waba_token) < 80 or 'EAAG9zZA89ZA0IBA...' in waba_token

            meta_success = False
            meta_response_details = ''

            if is_mock_token:
                time.sleep(0.15)
                meta_success = True
                meta_response_details = 'SIMULATED SUCCESS (Local test token used). Message payload was generated and verified perfectly.'
                add_system_log('info', f'[SIMULATED] Fired WhatsApp template "{template_name}" to {clean_phone}', 'Phase 4: Meta WABA API Integration')
            else:
                try:
                    meta_url = f"https://graph.facebook.com/v21.0/{phone_number_id}/messages"
                    payload = {
                        'messaging_product': 'whatsapp',
                        'to': clean_phone,
                        'type': 'template',
                        'template': {
                            'name': template_name,
                            'language': {
                                'code': language_code
                            },
                            'components': [
                                {
                                    'type': 'body',
                                    'parameters': [{'type': 'text', 'text': caller_name or 'Customer'}]
                                }
                            ]
                        }
                    }
                    headers = {
                        'Authorization': f'Bearer {waba_token}',
                        'Content-Type': 'application/json'
                    }
                    res = requests.post(meta_url, json=payload, headers=headers, timeout=2.5)
                    
                    err_data = None
                    try:
                        res_json = res.json()
                        err_data = res_json.get('error', {})
                    except:
                        pass

                    retried = False
                    if res.status_code != 200 and err_data and (err_data.get('code') == 132000 or '132000' in str(err_data.get('message', ''))):
                        # Retry without parameters due to static template
                        retry_payload = {
                            'messaging_product': 'whatsapp',
                            'to': clean_phone,
                            'type': 'template',
                            'template': {
                                'name': template_name,
                                'language': {'code': language_code}
                            }
                        }
                        res = requests.post(meta_url, json=retry_payload, headers=headers, timeout=2.5)
                        retried = True

                    if res.status_code in [200, 201]:
                        meta_success = True
                        meta_response_details = f"Meta API returned Status {res.status_code}: {res.text}"
                    else:
                        meta_success = False
                        meta_response_details = f"Meta API returned unexpected status {res.status_code}: {res.text}"
                except Exception as e:
                    meta_success = False
                    meta_response_details = str(e)

            duration_ms = int((time.time() - start_time) * 1000)
            is_under_sla = duration_ms < 3000

            tenant.calls_count += 1
            # Only count messages_sent for real API deliveries, not simulations
            if meta_success and not is_mock_token:
                tenant.messages_sent += 1
            tenant.save()

            # Build accurate details reflecting whether it was simulated or actually sent
            if meta_success:
                if is_mock_token:
                    log_details = 'SIMULATED: WhatsApp auto-recovery message simulated (local test token in use). No actual message sent.'
                else:
                    # Extract the actual message ID from Meta response
                    try:
                        res_json = res.json() if hasattr(res, 'json') else json.loads(res.text)
                        msg_id = res_json.get('messages', [{}])[0].get('id', '')
                        if msg_id:
                            if retried:
                                log_details = f"Meta accepted message (retried without params). ID: {msg_id}"
                            else:
                                log_details = f"Meta accepted message. ID: {msg_id}"
                        else:
                            log_details = f"Meta accepted request (status 200). Response: {res.text}"
                    except Exception:
                        if retried:
                            log_details = f"Meta accepted request (status 200, retried). Response: {res.text}"
                        else:
                            log_details = f"Meta accepted request (status 200). Response: {res.text}"
            else:
                log_details = build_meta_failure_message(meta_response_details)

            # Use 'simulated' status for mock tokens (message was never actually sent)
            event_status = 'simulated' if (meta_success and is_mock_token) else ('success' if meta_success else 'failed')
            CallEventLog.objects.create(
                id=str(uuid.uuid4()),
                timestamp=timezone.now(),
                license_key=license_key,
                tenant_name=tenant.name,
                caller_phone=caller_phone,
                caller_name=caller_name,
                status=event_status,
                duration_ms=duration_ms,
                details=log_details,
                raw_error=None if meta_success else meta_response_details
            )

            sim_status = '[SIMULATED] ' if is_mock_token else ''
            add_system_log('info', f'{sim_status}Processed call simulation in {duration_ms}ms. SLA: {"PASSED" if is_under_sla else "FAILED"}', 'Phase 4: Meta WABA API Integration')

            return JsonResponse({
                'status': 200 if meta_success else 502,
                'ok': meta_success,
                'data': {
                    'success': meta_success,
                    'licenseStatus': 'active',
                    'durationMs': duration_ms,
                    'slaStatus': 'PASSED' if is_under_sla else 'EXCEEDED',
                    'details': meta_response_details
                }
            })
        except Exception as e:
            return JsonResponse({'status': 500, 'ok': False, 'data': {'error': str(e)}}, status=500)
            
    return JsonResponse({'error': 'POST requests only'}, status=400)


@login_required
def settings_view(request):
    if request.user.is_superuser and 'impersonate_tenant_id' in request.session:
        request.session.pop('impersonate_tenant_id', None)
        return redirect('settings')

    migrate_existing_data_if_needed()
    tenants_count = Tenant.objects.count()
    calls_count = CallEventLog.objects.count()
    logs_count = ServerConsoleLog.objects.count()
    
    context = {
        'active_page': 'settings',
        'tenants_count': tenants_count,
        'calls_count': calls_count,
        'logs_count': logs_count,
    }
    return render(request, 'api/settings.html', context)


@login_required
def clear_system_logs_view(request):
    if not request.user.is_superuser:
        return HttpResponseForbidden("SuperAdmin privileges required.")
    if request.method == 'POST':
        confirm_password = request.POST.get('confirm_password', '')
        is_ajax = request.headers.get('x-requested-with') == 'XMLHttpRequest' or 'application/json' in request.headers.get('Accept', '')
        
        if request.user.check_password(confirm_password):
            ServerConsoleLog.objects.all().delete()
            add_system_log(
                'info',
                'Console logs manually cleared by admin.',
                'Phase 5: Super Admin Control Console'
            )
            if is_ajax:
                return JsonResponse({'success': True})
        else:
            if is_ajax:
                return JsonResponse({'success': False, 'message': 'Incorrect administrator password entered.'})
            from django.contrib import messages
            messages.error(request, "Clear logs aborted: Incorrect administrator password entered.")
    return redirect('settings')


@login_required
def seed_db_view(request):
    if not request.user.is_superuser:
        return HttpResponseForbidden("SuperAdmin privileges required.")
    if request.method == 'POST':
        confirm_password = request.POST.get('confirm_password', '')
        is_ajax = request.headers.get('x-requested-with') == 'XMLHttpRequest' or 'application/json' in request.headers.get('Accept', '')
        
        if request.user.check_password(confirm_password):
            # Flush DB first to seed fresh defaults
            Tenant.objects.all().delete()
            CallEventLog.objects.all().delete()
            ServerConsoleLog.objects.all().delete()
            
            migrate_existing_data_if_needed()
            if is_ajax:
                return JsonResponse({'success': True})
        else:
            if is_ajax:
                return JsonResponse({'success': False, 'message': 'Incorrect administrator password entered.'})
            from django.contrib import messages
            messages.error(request, "Database reset aborted: Incorrect administrator password entered.")
    return redirect('settings')



@login_required
def qa_view(request):
    context = {
        'active_page': 'qa',
    }
    return render(request, 'api/qa.html', context)


@login_required
def plans_view(request):
    current_tenant = None
    impersonate_id = request.session.get('impersonate_tenant_id')
    if impersonate_id and request.user.is_superuser:
        current_tenant = Tenant.objects.filter(id=impersonate_id).first()
    elif not request.user.is_superuser:
        current_tenant = Tenant.objects.filter(email__iexact=request.user.email).first()

    context = {
        'active_page': 'plans',
        'current_tenant': current_tenant,
    }
    return render(request, 'api/plans.html', context)


@login_required
def user_dashboard_view(request):
    migrate_existing_data_if_needed()
    tenant = None
    impersonate_id = request.session.get('impersonate_tenant_id')
    if impersonate_id and request.user.is_superuser:
        tenant = Tenant.objects.filter(id=impersonate_id).first()
    elif not request.user.is_superuser:
        tenant = Tenant.objects.filter(email__iexact=request.user.email).first()
    else:
        tenant = Tenant.objects.first()

    if request.method == 'POST' and tenant:
        plan = request.POST.get('plan', '').strip()
        waba_token = request.POST.get('waba_token', '').strip()
        phone_number_id = request.POST.get('phone_number_id', '').strip()
        template_name = request.POST.get('template_name', '').strip()
        language_code = request.POST.get('language_code', '').strip()


        # Only allow setting plan duration once during initial setup
        if plan and (not tenant.license_key or tenant.license_key == 'PENDING_SETUP'):
            tenant.plan = plan
            now = timezone.now()
            if plan == '1-month':
                tenant.expires_at = add_months(now, 1)
            elif plan == '3-month':
                tenant.expires_at = add_months(now, 3)
            elif plan == '6-month':
                tenant.expires_at = add_months(now, 6)
            elif plan == '12-month':
                tenant.expires_at = add_months(now, 12)


        tenant.waba_token = waba_token
        tenant.phone_number_id = phone_number_id
        tenant.template_name = template_name
        tenant.language_code = language_code


        # Generate unique license key when WABA info is submitted
        if not tenant.license_key or tenant.license_key == 'PENDING_SETUP':
            clean_name = re.sub(r'[^a-zA-Z0-9]', '', tenant.name)[:5].upper() or "SUB"
            random_suffix = random.randint(1000, 9999)
            tenant.license_key = f"LIC-{clean_name}-{random_suffix}"
            tenant.status = 'active'

        tenant.save()
        add_system_log('info', f"Updated WABA credentials & generated License Key '{tenant.license_key}' for '{tenant.name}'")
        sync_db_to_data_json()
        return redirect('user_dashboard')

    recent_history = []
    sla_percentage = 100
    if tenant and tenant.license_key and tenant.license_key != 'PENDING_SETUP':
        tenant_logs = CallEventLog.objects.filter(license_key=tenant.license_key).order_by('-timestamp')
        recent_history = list(tenant_logs[:10])
        total_count = len(recent_history)
        passed_count = sum(1 for item in recent_history if item.is_sla_pass)
        sla_percentage = int((passed_count / total_count * 100)) if total_count > 0 else 100

    selected_plan = request.GET.get('plan', '').strip()

    context = {
        'active_page': 'user_dashboard',
        'tenant': tenant,
        'recent_history': recent_history,
        'sla_percentage': sla_percentage,
        'selected_plan': selected_plan,
    }
    return render(request, 'api/user_dashboard.html', context)





# --- WEBHOOK / API CALL HANDLER (EXTERNAL API) ---

@csrf_exempt
def handle_call_event(request):
    migrate_existing_data_if_needed()
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed'}, status=405)

    start_time = time.time()
    try:
        data = json.loads(request.body)
    except:
        return JsonResponse({'error': 'Invalid JSON payload'}, status=400)

    license_key = data.get('licenseKey')
    caller_phone = data.get('callerPhone')
    caller_name = data.get('callerName')

    if not license_key or not caller_phone:
        return JsonResponse({
            'error': 'Invalid Payload',
            'required': {'licenseKey': 'string', 'callerPhone': 'string'}
        }, status=400)

    add_system_log(
        'info',
        f'Incoming mobile call event received! License: {license_key}, Caller: {caller_phone}',
        'Phase 3: Subscription & Expiry Logic',
        f'HTTP POST /api/call-event invoked. Starting verification flow for License Key: "{license_key}"'
    )

    try:
        tenant = Tenant.objects.filter(license_key=license_key).first()
        
        if not tenant:
            duration_ms = int((time.time() - start_time) * 1000)
            add_system_log(
                'warn',
                f'Rejected incoming call event. Invalid License Key: {license_key}',
                'Phase 3: Subscription & Expiry Logic'
            )

            CallEventLog.objects.create(
                id=str(uuid.uuid4()),
                timestamp=timezone.now(),
                license_key=license_key,
                tenant_name='Unknown Business',
                caller_phone=caller_phone,
                caller_name=caller_name or 'Customer',
                status='failed',
                duration_ms=duration_ms,
                details='Invalid License Key provided.'
            )

            return JsonResponse({
                'success': False,
                'status': 'unauthorized',
                'message': 'Invalid license key. Please check your admin configuration.'
            }, status=401)

        expiry_date = tenant.expires_at
        current_date = timezone.now()

        if tenant.status == 'blocked' or current_date > expiry_date:
            duration_ms = int((time.time() - start_time) * 1000)
            current_status = tenant.status
            if current_status != 'blocked' and current_date > expiry_date:
                current_status = 'expired'
                tenant.status = 'expired'
                tenant.save()

            add_system_log(
                'warn',
                f'License Key Blocked/Expired: {license_key}. Account Status: "{current_status}". Expired On: {expiry_date.strftime("%Y-%m-%d %H:%M:%S")}',
                'Phase 3: Subscription & Expiry Logic'
            )

            CallEventLog.objects.create(
                id=str(uuid.uuid4()),
                timestamp=timezone.now(),
                license_key=license_key,
                tenant_name=tenant.name,
                caller_phone=caller_phone,
                caller_name=caller_name or 'Customer',
                status='blocked' if current_status == 'blocked' else 'expired',
                duration_ms=duration_ms,
                details='Account manually blocked by Super Admin.' if current_status == 'blocked' else 'License plan exceeded subscription duration.'
            )

            tenant.calls_count += 1
            tenant.save()

            return JsonResponse({
                'success': False,
                'status': current_status,
                'message': 'SaaS access blocked by administrator.' if current_status == 'blocked' else 'Subscription plan expired. Please extend license validity.'
            }, status=403)

        # Active
        waba_token = tenant.waba_token
        phone_number_id = tenant.phone_number_id.strip()
        template_name = tenant.template_name
        language_code = tenant.language_code or 'en_US'
        
        clean_phone = standardize_phone_number(caller_phone)
        # Real Meta WhatsApp tokens are 150+ chars; seed tokens are ~41 chars
        is_mock_token = not waba_token or len(waba_token) < 80 or 'EAAG9zZA89ZA0IBA...' in waba_token

        meta_success = False
        meta_response_details = ''

        if is_mock_token:
            time.sleep(0.15)
            meta_success = True
            meta_response_details = 'SIMULATED SUCCESS (Local test token used). Message payload was generated and verified perfectly.'
        else:
            try:
                meta_url = f"https://graph.facebook.com/v21.0/{phone_number_id}/messages"
                payload = {
                    'messaging_product': 'whatsapp',
                    'to': clean_phone,
                    'type': 'template',
                    'template': {
                        'name': template_name,
                        'language': {'code': language_code},
                        'components': [
                            {
                                'type': 'body',
                                'parameters': [{'type': 'text', 'text': caller_name or 'Customer'}]
                            }
                        ]
                    }
                }
                headers = {
                    'Authorization': f'Bearer {waba_token}',
                    'Content-Type': 'application/json'
                }
                res = requests.post(meta_url, json=payload, headers=headers, timeout=2.5)
                
                err_data = None
                try:
                    res_json = res.json()
                    err_data = res_json.get('error', {})
                except:
                    pass

                retried = False
                if res.status_code != 200 and err_data and (err_data.get('code') == 132000 or '132000' in str(err_data.get('message', ''))):
                    retry_payload = {
                        'messaging_product': 'whatsapp',
                        'to': clean_phone,
                        'type': 'template',
                        'template': {
                            'name': template_name,
                            'language': {'code': language_code}
                        }
                    }
                    res = requests.post(meta_url, json=retry_payload, headers=headers, timeout=2.5)
                    retried = True

                if res.status_code in [200, 201]:
                    meta_success = True
                    meta_response_details = f"Meta API returned Status {res.status_code}: {res.text}"
                else:
                    meta_success = False
                    meta_response_details = f"Meta API returned unexpected status {res.status_code}: {res.text}"
            except Exception as e:
                meta_success = False
                meta_response_details = str(e)

        duration_ms = int((time.time() - start_time) * 1000)
        is_under_sla = duration_ms < 3000

        tenant.calls_count += 1
        # Only count messages_sent for real API deliveries, not simulations
        if meta_success and not is_mock_token:
            tenant.messages_sent += 1
        tenant.save()

        # Build accurate details reflecting whether it was simulated or actually sent
        if meta_success:
            if is_mock_token:
                log_details = 'SIMULATED: WhatsApp auto-recovery message simulated (local test token in use). No actual message sent.'
            else:
                # Extract the actual message ID from Meta response
                try:
                    res_json = res.json() if hasattr(res, 'json') else json.loads(res.text)
                    msg_id = res_json.get('messages', [{}])[0].get('id', '')
                    if msg_id:
                        if retried:
                            log_details = f"Meta accepted message (retried without params). ID: {msg_id}"
                        else:
                            log_details = f"Meta accepted message. ID: {msg_id}"
                    else:
                        log_details = f"Meta accepted request (status 200). Response: {res.text}"
                except Exception:
                    if retried:
                        log_details = f"Meta accepted request (status 200, retried). Response: {res.text}"
                    else:
                        log_details = f"Meta accepted request (status 200). Response: {res.text}"
        else:
            log_details = build_meta_failure_message(meta_response_details)

        # Use 'simulated' status for mock tokens (message was never actually sent)
        event_status = 'simulated' if (meta_success and is_mock_token) else ('success' if meta_success else 'failed')
        CallEventLog.objects.create(
            id=str(uuid.uuid4()),
            timestamp=timezone.now(),
            license_key=license_key,
            tenant_name=tenant.name,
            caller_phone=caller_phone,
            caller_name=caller_name or 'Customer',
            status=event_status,
            duration_ms=duration_ms,
            details=log_details,
            raw_error=None if meta_success else meta_response_details
        )

        delivery_status = 'SIMULATED' if is_mock_token else ('DELIVERED' if meta_success else 'FAILED')
        add_system_log(
            'info' if meta_success else 'warn',
            f'Processed incoming webhook call event in {duration_ms}ms. WhatsApp: {delivery_status}',
            'Phase 4: Meta WABA API Integration'
        )

        return JsonResponse({
            'success': meta_success,
            'licenseStatus': 'active',
            'durationMs': duration_ms,
            'slaStatus': 'PASSED' if is_under_sla else 'EXCEEDED',
            'details': meta_response_details
        }, status=200 if meta_success else 502)

    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@login_required
def latest_status_view(request):
    latest_event = CallEventLog.objects.all().order_by('-timestamp').first()
    latest_log = ServerConsoleLog.objects.all().order_by('-timestamp').first()
    
    event_timestamp = latest_event.timestamp.isoformat() if latest_event else ""
    log_timestamp = latest_log.timestamp.isoformat() if latest_log else ""
    
    total_calls = Tenant.objects.aggregate(total=Sum('calls_count'))['total'] or 0
    total_tenants = Tenant.objects.count()
    
    return JsonResponse({
        'event_timestamp': event_timestamp,
        'log_timestamp': log_timestamp,
        'total_calls': total_calls,
        'total_tenants': total_tenants,
    })


@login_required
def overview_data_view(request):
    """JSON endpoint for real-time dashboard updates without page reload."""
    is_tenant = False
    tenant = None
    if not request.user.is_superuser:
        tenant = Tenant.objects.filter(email__iexact=request.user.email).first()
        if tenant:
            is_tenant = True

    stats = compute_overview_stats(request.user)
    
    if is_tenant:
        latest_event = CallEventLog.objects.filter(license_key=tenant.license_key).order_by('-timestamp').first()
    else:
        latest_event = CallEventLog.objects.all().order_by('-timestamp').first()
        
    latest_log = ServerConsoleLog.objects.all().order_by('-timestamp').first()

    return JsonResponse({
        **stats,
        'event_timestamp': latest_event.timestamp.isoformat() if latest_event else "",
        'log_timestamp': latest_log.timestamp.isoformat() if latest_log else "",
    })


@csrf_exempt
def handle_meta_status_webhook(request):
    """Receives message delivery status updates from Meta Developer App Webhooks"""
    if request.method == 'GET':
        mode = request.GET.get('hub.mode')
        token = request.GET.get('hub.verify_token')
        challenge = request.GET.get('hub.challenge')
        # Standard token verification - can be customized by user
        if mode == 'subscribe' and token == 'walstar_verify_token':
            return HttpResponse(challenge)
        return HttpResponse('Forbidden', status=403)
        
    elif request.method == 'POST':
        try:
            data = json.loads(request.body)
            # Parse status changes
            for entry in data.get('entry', []):
                for change in entry.get('changes', []):
                    value = change.get('value', {})
                    for status_info in value.get('statuses', []):
                        wamid = status_info.get('id')
                        status = status_info.get('status')  # 'sent', 'delivered', 'read', 'failed'
                        
                        # Find the corresponding call log where detail contains the wamid
                        log = CallEventLog.objects.filter(details__contains=wamid).first()
                        if log:
                            if status == 'failed':
                                errors = status_info.get('errors', [])
                                error_msg = "Meta delivery failed"
                                if errors:
                                    err = errors[0]
                                    error_msg = f"Meta delivery failed (code {err.get('code')}): {err.get('message')}"
                                
                                log.status = 'failed'
                                log.details = error_msg
                                log.save()
                                
                                add_system_log(
                                    'error',
                                    f"WhatsApp delivery failed for caller {log.caller_phone}: {error_msg}",
                                    'Phase 4: Meta WABA API Integration'
                                )
                            elif status in ['delivered', 'read']:
                                if log.status != 'success':
                                    log.status = 'success'
                                    log.save()
                                    
                                    add_system_log(
                                        'info',
                                        f"WhatsApp message {wamid[:12]} successfully {status} to {log.caller_phone}",
                                        'Phase 4: Meta WABA API Integration'
                                    )
            return HttpResponse('OK')
        except Exception as e:
            add_system_log('error', f"Webhook processing error: {str(e)}", 'Phase 4: Meta WABA API Integration')
            return HttpResponse(str(e), status=500)


# --- AUTHENTICATION VIEWS ---

def login_view(request):
    if request.user.is_authenticated:
        if request.user.is_superuser:
            return redirect('overview')
        return redirect('user_dashboard')
        
    error_msg = None
    if request.method == 'POST':
        email_input = request.POST.get('email', '').strip()
        password = request.POST.get('password', '')
        
        user_by_email = User.objects.filter(Q(email__iexact=email_input) | Q(username__iexact=email_input)).first()

        if user_by_email:
            user = authenticate(request, username=user_by_email.username, password=password)
            if user is not None:
                if not user.is_superuser:
                    tenant_exists = Tenant.objects.filter(email__iexact=user.email).exists()
                    if not tenant_exists:
                        error_msg = "Your subscriber account has been removed by system administration."
                        return render(request, 'api/login.html', {'error_msg': error_msg})
                
                login(request, user)
                if user.is_superuser:
                    return redirect('overview')
                return redirect('user_dashboard')
            else:
                error_msg = "Incorrect password. Please verify your password and try again."
        else:
            error_msg = "No registered account found with this email address. Please check your credentials or create an account."

            
    return render(request, 'api/login.html', {'error_msg': error_msg})




def register_view(request):
    if request.user.is_authenticated:
        if request.user.is_superuser:
            return redirect('overview')
        return redirect('user_dashboard')
        
    error_msg = None
    if request.method == 'POST':
        business_name = request.POST.get('business_name', '').strip() or request.POST.get('username', '').strip()
        email = request.POST.get('email', '').strip()
        password = request.POST.get('password', '')
        password_confirm = request.POST.get('password_confirm', '')

        clean_username = re.sub(r'[^a-zA-Z0-9_]', '', business_name.lower().replace(' ', '_'))[:20] or "user"
        if User.objects.filter(username=clean_username).exists():
            clean_username = f"{clean_username}_{random.randint(100, 999)}"

        if not business_name or not email or not password:
            error_msg = "Please fill in all required registration fields."
        elif password != password_confirm:
            error_msg = "Passwords do not match. Please verify both password entries."
        elif User.objects.filter(email__iexact=email).exists():
            error_msg = f"An account with email '{email}' already exists. Please sign in instead."
        else:
            try:
                # 1. Create Django user account
                user = User.objects.create_user(username=clean_username, email=email, password=password)
                
                Tenant.objects.create(
                    name=business_name,
                    email=email,
                    plan='',
                    license_key=f"PENDING_{uuid.uuid4().hex[:6].upper()}",
                    expires_at=timezone.now(),
                    waba_token='',
                    phone_number_id='',
                    template_name='',
                    language_code='',
                    status='pending'
                )



                # 3. Authenticate and log in user
                login(request, user)
                
                add_system_log(
                    'info',
                    f"Self-registered new subscriber '{business_name}' ({email}). Awaiting WABA credential setup.",
                    'Phase 2: Core Server Engine',
                    f"User '{clean_username}' ({email}) registered. Pending WABA setup to generate license key."
                )
                sync_db_to_data_json()
                return redirect('user_dashboard')
            except Exception as e:
                error_msg = f"Error creating subscriber account: {str(e)}"
                
    return render(request, 'api/register.html', {'error_msg': error_msg})





def logout_view(request):
    logout(request)
    return redirect('login')


def forgot_password_view(request):
    if request.user.is_authenticated:
        return redirect('overview')
        
    error_msg = None
    success_msg = None
    
    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        email = request.POST.get('email', '').strip()
        new_password = request.POST.get('new_password', '')
        new_password_confirm = request.POST.get('new_password_confirm', '')
        
        if new_password != new_password_confirm:
            error_msg = "New passwords do not match. Please verify both password entries."
        else:
            user = User.objects.filter(username=username, email__iexact=email).first()
            if user:
                user.set_password(new_password)
                user.save()
                
                add_system_log(
                    'info',
                    f"Password reset completed for user: {username}",
                    'Phase 2: Core Server Engine',
                    f"User '{username}' updated their account password via administrative recovery form."
                )
                success_msg = "Password reset successfully! You can now sign in with your new password."
            else:
                error_msg = "Verification failed. The username and email address combination does not match any registered account."
                
    return render(request, 'api/forgot_password.html', {'error_msg': error_msg, 'success_msg': success_msg})
@csrf_exempt
def create_razorpay_order_view(request):
    if not request.user.is_authenticated:
        return JsonResponse({'status': 'failed', 'message': 'Session expired. Please log in again.'}, status=200)

    if request.method != 'POST':
        return JsonResponse({'status': 'failed', 'message': 'Invalid request method.'}, status=200)

    tenant = get_user_tenant(request.user, request)
    if not tenant:
        tenant = Tenant.objects.filter(email__iexact=request.user.email).first()
        if not tenant and request.user.username:
            tenant = Tenant.objects.filter(name__iexact=request.user.username).first()

    if not tenant:
        tenant = Tenant.objects.create(
            name=request.user.username.title() if request.user.username else 'Subscriber',
            email=request.user.email or f"{request.user.username}@example.com",
            license_key=f"PENDING_{uuid.uuid4().hex[:6].upper()}",
            status='pending',
            expires_at=timezone.now()
        )

    plan_duration = request.POST.get('plan_duration')
    if not plan_duration and request.body:
        try:
            import json
            body_data = json.loads(request.body)
            plan_duration = body_data.get('plan_duration')
        except Exception:
            pass
    if not plan_duration:
        plan_duration = '1-month'

    prices_paise = {
        '1-month': 99900,   # ₹999
        '3-month': 249900,  # ₹2,499
        '6-month': 499900,  # ₹4,999
        '12-month': 899900, # ₹8,999
    }
    amount_paise = prices_paise.get(plan_duration, 99900)
    amount_rupees = amount_paise / 100.0

    key_id = (settings.RAZERPAY_KEY_ID or '').strip()
    key_secret = (settings.RAZERPAY_KEY_SECRET or '').strip()

    if not key_id or not key_secret:
        return JsonResponse({'status': 'failed', 'message': 'Razorpay API keys are missing or invalid in backend .env file.'}, status=200)

    client = razorpay.Client(auth=(key_id, key_secret))

    try:
        receipt_id = f"rcpt_{str(tenant.id).replace('-', '')[:10]}_{int(time.time())}"
        order_data = {
            'amount': int(amount_paise),
            'currency': 'INR',
            'receipt': receipt_id,
            'payment_capture': 1,
            'notes': {
                'tenant_id': str(tenant.id),
                'tenant_name': str(tenant.name),
                'plan_duration': str(plan_duration)
            }
        }
        order = client.order.create(data=order_data)

        PaymentRecord.objects.create(
            tenant=tenant,
            razorpay_order_id=order['id'],
            plan=plan_duration,
            amount=amount_rupees,
            status='created'
        )

        return JsonResponse({
            'status': 'success',
            'order_id': order['id'],
            'amount': order['amount'],
            'currency': order.get('currency', 'INR'),
            'key_id': key_id,
            'business_name': tenant.name,
            'business_email': tenant.email or request.user.email or 'subscriber@example.com',
            'business_phone': tenant.phone_number_id or '',
        })

    except Exception as e:
        import traceback
        print("--- RAZORPAY ORDER ERROR ---")
        traceback.print_exc()
        return JsonResponse({'status': 'failed', 'message': f"Razorpay API Error: {str(e)}"}, status=200)


@csrf_exempt
def verify_razorpay_payment_view(request):
    if not request.user.is_authenticated:
        return JsonResponse({'status': 'failed', 'message': 'Session expired. Please log in again.'}, status=200)

    if request.method != 'POST':
        return JsonResponse({'status': 'failed', 'message': 'Invalid request method.'}, status=200)

    try:

        import json
        try:
            data = json.loads(request.body) if request.body else request.POST
        except Exception:
            data = request.POST

        order_id = data.get('razorpay_order_id')
        payment_id = data.get('razorpay_payment_id')
        signature = data.get('razorpay_signature')
        phone_number_id = data.get('phone_number_id', '').strip()
        waba_token = data.get('waba_token', '').strip()
        template_name = data.get('template_name', '').strip() or 'missed_call_recovery'
        language_code = data.get('language_code', '').strip() or 'en_US'

        client = razorpay.Client(auth=(settings.RAZERPAY_KEY_ID, settings.RAZERPAY_KEY_SECRET))

        params_dict = {
            'razorpay_order_id': order_id,
            'razorpay_payment_id': payment_id,
            'razorpay_signature': signature
        }

        # Verify cryptographic signature
        client.utility.verify_payment_signature(params_dict)

        payment_rec = PaymentRecord.objects.filter(razorpay_order_id=order_id).first()
        if not payment_rec:
            return JsonResponse({'status': 'failed', 'message': 'Payment record not found.'}, status=404)

        tenant = payment_rec.tenant
        payment_rec.razorpay_payment_id = payment_id
        payment_rec.razorpay_signature = signature
        payment_rec.status = 'paid'
        payment_rec.save()

        # Update Tenant WABA Credentials and Expiry
        if phone_number_id:
            tenant.phone_number_id = phone_number_id
        if waba_token:
            tenant.waba_token = waba_token
        tenant.template_name = template_name
        tenant.language_code = language_code
        tenant.plan = payment_rec.plan
        tenant.status = 'active'

        now = timezone.now()
        days_map = {'1-month': 30, '3-month': 90, '6-month': 180, '12-month': 365}
        added_days = days_map.get(payment_rec.plan, 30)
        tenant.expires_at = now + timedelta(days=added_days)

        # Generate License Key if pending
        if tenant.is_pending:
            random_code = uuid.uuid4().hex[:8].upper()
            tenant.license_key = f"WABA_KEY-{random_code}"




        tenant.save()
        sync_db_to_data_json()

        add_system_log(
            'info',
            f"Payment successful & Plan activated for tenant '{tenant.name}' ({tenant.plan})",
            'Razorpay Gateway',
            f"Payment ID: {payment_id}, Order ID: {order_id}, Amount: ₹{payment_rec.amount}"
        )

        return JsonResponse({
            'status': 'success',
            'license_key': tenant.license_key,
            'plan': tenant.plan,
            'expires_at': tenant.expires_at.strftime('%b %d, %Y')
        })
    except razorpay.errors.SignatureVerificationError:
        return JsonResponse({'status': 'failed', 'message': 'Payment signature verification failed!'}, status=400)
    except Exception as e:
        return JsonResponse({'status': 'failed', 'message': str(e)}, status=500)


@login_required
def impersonate_user_view(request, tenant_id):
    if not request.user.is_superuser:
        from django.http import HttpResponseForbidden
        return HttpResponseForbidden("SuperAdmin access required to impersonate accounts.")
    request.session['impersonate_tenant_id'] = str(tenant_id)
    return redirect('user_dashboard')


@login_required
def exit_impersonate_view(request):
    request.session.pop('impersonate_tenant_id', None)
    return redirect('tenants')





