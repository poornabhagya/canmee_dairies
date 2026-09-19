from django.urls import path

from . import views

app_name = "hrm"

urlpatterns = [
    path("employees/", views.employee_list, name="employee_list"),
    path("employees/add/", views.employee_create, name="employee_create"),
    path("employees/<int:pk>/", views.employee_detail, name="employee_detail"),
    path("employees/<int:pk>/json/", views.employee_detail_json, name="employee_detail_json"),
    path("employees/<int:pk>/edit/", views.employee_edit, name="employee_edit"),
    path("employees/<int:pk>/resign/", views.employee_resign, name="employee_resign"),
    path("employees/<int:pk>/terminate/", views.employee_terminate, name="employee_terminate"),
    path("employees/<int:pk>/status/", views.employee_status_update, name="employee_status_update"),
    path("employees/<int:pk>/delete/", views.employee_delete, name="employee_delete"),
    path("departments/manage/", views.department_manage, name="department_manage"),
]
