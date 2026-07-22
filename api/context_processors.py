from .models import Tenant

def impersonation_context(request):
    if request.user.is_authenticated and request.user.is_superuser:
        impersonate_id = request.session.get('impersonate_tenant_id')
        if impersonate_id:
            tenant = Tenant.objects.filter(id=impersonate_id).first()
            if tenant:
                return {
                    'is_impersonating': True,
                    'impersonated_tenant': tenant,
                }
    return {
        'is_impersonating': False,
        'impersonated_tenant': None,
    }
