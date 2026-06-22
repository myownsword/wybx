from django.urls import path
from . import views

urlpatterns = [
    path('', views.order_list, name='order_list'),
    path('dashboard/', views.dashboard, name='dashboard'),
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('order/create/', views.order_create, name='order_create'),
    path('order/<int:order_id>/', views.order_detail, name='order_detail'),
    path('order/<int:order_id>/assign/', views.order_assign, name='order_assign'),
    path('order/<int:order_id>/start/', views.order_start, name='order_start'),
    path('order/<int:order_id>/finish/', views.order_finish, name='order_finish'),
    path('order/<int:order_id>/confirm/', views.order_confirm, name='order_confirm'),
    path('order/<int:order_id>/rework/', views.order_rework, name='order_rework'),
    path('order/<int:order_id>/close/', views.order_close, name='order_close'),
    path('order/<int:order_id>/reschedule/', views.order_reschedule_request, name='order_reschedule_request'),
    path('order/<int:order_id>/reschedule/<int:req_id>/approve/', views.order_reschedule_approve, name='order_reschedule_approve'),
    path('order/<int:order_id>/reschedule/<int:req_id>/reject/', views.order_reschedule_reject, name='order_reschedule_reject'),
    path('order/<int:order_id>/technician-flag/', views.order_technician_flag, name='order_technician_flag'),
    path('export/materials/', views.export_materials_csv, name='export_materials'),
    path('export/timeout/', views.export_timeout_csv, name='export_timeout'),
]
