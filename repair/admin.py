from django.contrib import admin
from .models import (
    Room, Resident, Technician, Dispatcher,
    Material, WorkOrder, OrderMaterial, Timeline,
)


class OrderMaterialInline(admin.TabularInline):
    model = OrderMaterial
    extra = 0
    raw_id_fields = ('material',)


class TimelineInline(admin.TabularInline):
    model = Timeline
    extra = 0
    readonly_fields = ('created_at',)


@admin.register(Room)
class RoomAdmin(admin.ModelAdmin):
    list_display = ('__str__', 'is_active')
    list_filter = ('is_active', 'building')
    search_fields = ('building', 'room_number')


@admin.register(Resident)
class ResidentAdmin(admin.ModelAdmin):
    list_display = ('name', 'room', 'phone', 'user')
    search_fields = ('name', 'phone')
    raw_id_fields = ('room', 'user')


@admin.register(Technician)
class TechnicianAdmin(admin.ModelAdmin):
    list_display = ('name', 'phone', 'specialty', 'is_active')
    list_filter = ('is_active',)
    search_fields = ('name', 'phone')


@admin.register(Dispatcher)
class DispatcherAdmin(admin.ModelAdmin):
    list_display = ('name', 'user')


@admin.register(Material)
class MaterialAdmin(admin.ModelAdmin):
    list_display = ('name', 'unit', 'price')
    search_fields = ('name',)


@admin.register(WorkOrder)
class WorkOrderAdmin(admin.ModelAdmin):
    list_display = ('order_no', 'room', 'resident', 'technician', 'status', 'urgency', 'created_at')
    list_filter = ('status', 'urgency', 'problem_type')
    search_fields = ('order_no', 'description', 'room__building', 'room__room_number')
    readonly_fields = ('order_no',)
    inlines = [OrderMaterialInline, TimelineInline]
    raw_id_fields = ('room', 'resident', 'technician', 'dispatcher', 'confirmed_by', 'parent_order')


@admin.register(Timeline)
class TimelineAdmin(admin.ModelAdmin):
    list_display = ('order', 'event_type', 'operator_name', 'created_at')
    list_filter = ('event_type',)
    search_fields = ('order__order_no', 'content')
    readonly_fields = ('created_at',)
    raw_id_fields = ('order', 'operator')
