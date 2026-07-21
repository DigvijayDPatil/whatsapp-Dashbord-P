from django.urls import path
from django.views.generic.base import RedirectView
from . import views

urlpatterns = [
    # Auth
    path('login/', views.login_view, name='login'),
    path('register/', views.register_view, name='register'),
    path('register', views.register_view),
    path('logout/', views.logout_view, name='logout'),
    path('forgot-password/', views.forgot_password_view, name='forgot_password'),




    # Pages
    path('', views.overview_view, name='overview'),
    path('tenants/', views.tenants_view, name='tenants'),
    path('tenants/edit-waba/<uuid:pk>/', views.tenant_edit_waba_view, name='tenant_edit_waba'),
    path('tenants/toggle-block/<uuid:pk>/', views.tenant_toggle_block_view, name='tenant_toggle_block'),
    path('tenants/extend/<uuid:pk>/', views.tenant_extend_view, name='tenant_extend'),
    path('tenants/delete/<uuid:pk>/', views.tenant_delete_view, name='tenant_delete'),
    path('history/', views.history_view, name='history'),
    path('history/seed/', views.seed_history_view, name='seed_history'),
    path('sandbox/', views.sandbox_view, name='sandbox'),
    path('settings/', views.settings_view, name='settings'),
    path('settings/clear-logs/', views.clear_system_logs_view, name='clear_system_logs'),
    path('settings/seed-db/', views.seed_db_view, name='seed_db'),
    path('qa/', views.qa_view, name='qa'),
    path('plans/', views.plans_view, name='plans'),
    path('user-dashboard/', views.user_dashboard_view, name='user_dashboard'),
    path('academy/', RedirectView.as_view(url='/qa/', permanent=True)),

    
    # APIs & Webhooks
    path('sandbox/run/', views.run_simulation_view, name='run_simulation'),
    path('api/call-event/', views.handle_call_event, name='api_call_event'),
    # External webhook: register without trailing slash (mobile apps POST without it)
    path('api/webhook/call', views.handle_call_event, name='api_webhook_call'),
    path('api/webhook/call/', views.handle_call_event, name='api_webhook_call_slash'),
    path('api/webhook/status', views.handle_meta_status_webhook, name='api_webhook_status'),
    path('api/webhook/status/', views.handle_meta_status_webhook, name='api_webhook_status_slash'),
    path('api/latest-status/', views.latest_status_view, name='latest_status'),
    path('api/overview-data/', views.overview_data_view, name='overview_data'),

    # Razorpay Payment Endpoints
    path('api/create-razorpay-order/', views.create_razorpay_order_view, name='create_razorpay_order'),
    path('api/verify-razorpay-payment/', views.verify_razorpay_payment_view, name='verify_razorpay_payment'),
]

