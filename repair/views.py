import csv
from io import StringIO
from django.db import transaction
from django.shortcuts import render, redirect, get_object_or_404, reverse
from django.contrib.auth import login, logout, authenticate
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import HttpResponse, HttpResponseForbidden, JsonResponse
from django.utils import timezone
from django.db.models import Q, Sum, Count, F, DecimalField, Avg
from django.db.models.functions import Coalesce
from datetime import datetime, timedelta
from decimal import Decimal

from .models import (
    WorkOrder, Room, Resident, Technician, Dispatcher,
    Material, OrderMaterial, Timeline, generate_order_no, add_timeline,
    TIMELINE_CREATED, TIMELINE_ASSIGNED, TIMELINE_ARRIVED, TIMELINE_FINISHED,
    TIMELINE_CONFIRMED, TIMELINE_REWORK_REQUEST, TIMELINE_REWORK_CREATED,
    TIMELINE_CLOSED, TIMELINE_UNASSIGNED, TIMELINE_NOTE,
    TIMELINE_RESCHEDULE_REQUEST, TIMELINE_RESCHEDULE_APPROVED,
    TIMELINE_RESCHEDULE_REJECTED, TIMELINE_RESCHEDULE_REASSIGNED,
    TIMELINE_TECHNICIAN_FLAG, TIMELINE_TIMEOUT,
    RescheduleRequest, TechnicianFlag, TimeoutConfig,
)
from .forms import (
    RepairSubmitForm, AssignForm, StartWorkForm, FinishWorkForm,
    ConfirmForm, ReworkForm, MaterialFormSet,
    RescheduleRequestForm, RescheduleReviewForm, TechnicianFlagForm,
)


def get_user_role(user):
    if not user or not user.is_authenticated:
        return None
    if hasattr(user, 'resident_profile'):
        return 'resident'
    if hasattr(user, 'technician_profile'):
        return 'technician'
    if hasattr(user, 'dispatcher_profile'):
        return 'dispatcher'
    if user.is_superuser:
        return 'admin'
    return 'unknown'


def login_view(request):
    if request.user.is_authenticated:
        return redirect('order_list')
    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')
        user = authenticate(request, username=username, password=password)
        if user:
            login(request, user)
            messages.success(request, f'欢迎回来，{user.get_full_name() or user.username}！')
            return redirect('order_list')
        else:
            messages.error(request, '用户名或密码错误')
    return render(request, 'login.html')


def logout_view(request):
    logout(request)
    return redirect('login')


@login_required
def order_list(request):
    role = get_user_role(request.user)
    status_filter = request.GET.get('status', '')
    urgency_filter = request.GET.get('urgency', '')
    timeout_filter = request.GET.get('timeout', '')
    keyword = request.GET.get('keyword', '').strip()

    qs = WorkOrder.objects.all()

    if role == 'resident':
        qs = qs.filter(resident=request.user.resident_profile)
    elif role == 'technician':
        qs = qs.filter(technician=request.user.technician_profile)

    if status_filter:
        qs = qs.filter(status=status_filter)
    if urgency_filter:
        qs = qs.filter(urgency=urgency_filter)
    if timeout_filter == '1':
        qs = qs.filter(is_timeout=True)
    if keyword:
        qs = qs.filter(
            Q(order_no__icontains=keyword) |
            Q(description__icontains=keyword) |
            Q(room__building__icontains=keyword) |
            Q(room__room_number__icontains=keyword)
        )

    orders = sorted(qs, key=lambda o: (o.urgency_rank, o.created_at.timestamp() * -1))

    status_counts = {
        'total': qs.count(),
        'urgent': qs.filter(urgency__in=[WorkOrder.URGENCY_HIGH, WorkOrder.URGENCY_URGENT]).count(),
        'pending': qs.filter(status=WorkOrder.STATUS_PENDING).count(),
        'assigned': qs.filter(status=WorkOrder.STATUS_ASSIGNED).count(),
        'in_progress': qs.filter(status=WorkOrder.STATUS_IN_PROGRESS).count(),
        'done': qs.filter(status=WorkOrder.STATUS_DONE).count(),
        'rework': qs.filter(status=WorkOrder.STATUS_REWORK).count(),
        'confirmed': qs.filter(status__in=[WorkOrder.STATUS_CONFIRMED, WorkOrder.STATUS_CLOSED]).count(),
        'timeout': qs.filter(is_timeout=True).count(),
    }

    context = {
        'orders': orders,
        'status_filter': status_filter,
        'urgency_filter': urgency_filter,
        'timeout_filter': timeout_filter,
        'keyword': keyword,
        'status_counts': status_counts,
        'role': role,
        'STATUS_CHOICES': WorkOrder.STATUS_CHOICES,
        'URGENCY_CHOICES': WorkOrder.URGENCY_CHOICES,
    }
    return render(request, 'order_list.html', context)


@login_required
@transaction.atomic
def order_create(request):
    role = get_user_role(request.user)
    if role not in ['resident', 'dispatcher', 'admin']:
        return HttpResponseForbidden('您没有报修权限')

    if request.method == 'POST':
        form = RepairSubmitForm(request.POST, user=request.user)
        if form.is_valid():
            cd = form.cleaned_data
            room = cd['room_obj']

            resident = None
            if role == 'resident':
                resident = request.user.resident_profile
                if resident.room_id != room.id:
                    messages.error(request, '您只能为自己的房屋提交报修！')
                    return redirect('order_create')
            else:
                resident_qs = Resident.objects.filter(room=room)
                if resident_qs.exists():
                    resident = resident_qs.first()
                else:
                    user = request.user
                    if hasattr(user, 'dispatcher_profile'):
                        messages.error(request, f'该房号暂无注册住户，请先登记住户信息！')
                        return redirect('order_create')

            order = WorkOrder.objects.create(
                order_no=generate_order_no(),
                room=room,
                resident=resident,
                problem_type=cd['problem_type'],
                urgency=cd['urgency'],
                description=cd['description'],
                available_start=cd['available_start'],
                available_end=cd['available_end'],
                contact_phone=cd['contact_phone'],
            )
            add_timeline(order, TIMELINE_CREATED, request.user,
                        f'住户{resident.name if resident else ""}提交报修：{cd["description"][:50]}...')

            messages.success(request, f'报修提交成功！工单号：{order.order_no}')
            return redirect('order_detail', order_id=order.id)
    else:
        form = RepairSubmitForm(user=request.user)

    return render(request, 'order_create.html', {'form': form, 'role': role})


def _build_detail_context(request, order):
    role = get_user_role(request.user)
    can_assign = (role in ['dispatcher', 'admin']) and order.can_assign()
    can_start = order.can_start(request.user)
    can_finish = order.can_finish(request.user)
    can_confirm = order.can_confirm(request.user)
    can_rework = order.can_rework(request.user)
    can_close = (role in ['dispatcher', 'admin']) and order.can_close()
    can_reschedule = order.can_reschedule(request.user)
    can_review_reschedule = (role in ['dispatcher', 'admin'])
    can_flag = (role == 'technician') and order.status in [WorkOrder.STATUS_ASSIGNED] and order.technician and order.technician.user_id == request.user.id

    materials = order.materials.all()
    material_total = sum(m.subtotal for m in materials)
    timelines = order.timelines.all()

    assign_form = AssignForm(order=order) if can_assign else None
    start_form = StartWorkForm() if can_start else None
    finish_form = FinishWorkForm() if can_finish else None
    confirm_form = ConfirmForm() if can_confirm else None
    rework_form = ReworkForm() if can_rework else None
    material_formset = MaterialFormSet(instance=order) if can_finish else None
    reschedule_form = RescheduleRequestForm(order=order) if can_reschedule else None
    flag_form = TechnicianFlagForm() if can_flag else None

    pending_reschedules = order.reschedule_requests.filter(status=RescheduleRequest.STATUS_PENDING) if can_review_reschedule else RescheduleRequest.objects.none()
    review_forms = {req.id: RescheduleReviewForm(reschedule_request=req) for req in pending_reschedules}
    active_technicians = Technician.objects.filter(is_active=True) if can_review_reschedule else Technician.objects.none()

    return {
        'order': order,
        'role': role,
        'can_assign': can_assign,
        'can_start': can_start,
        'can_finish': can_finish,
        'can_confirm': can_confirm,
        'can_rework': can_rework,
        'can_close': can_close,
        'can_reschedule': can_reschedule,
        'can_review_reschedule': can_review_reschedule,
        'can_flag': can_flag,
        'materials': materials,
        'material_total': material_total,
        'timelines': timelines,
        'assign_form': assign_form,
        'start_form': start_form,
        'finish_form': finish_form,
        'confirm_form': confirm_form,
        'rework_form': rework_form,
        'material_formset': material_formset,
        'reschedule_form': reschedule_form,
        'flag_form': flag_form,
        'pending_reschedules': pending_reschedules,
        'review_forms': review_forms,
        'active_technicians': active_technicians,
    }


@login_required
def order_detail(request, order_id):
    order = get_object_or_404(WorkOrder, id=order_id)
    role = get_user_role(request.user)

    can_view = False
    if role in ['dispatcher', 'admin']:
        can_view = True
    elif role == 'resident':
        can_view = (order.resident_id == request.user.resident_profile.id)
    elif role == 'technician':
        can_view = (order.technician_id == request.user.technician_profile.id)

    if not can_view:
        return HttpResponseForbidden('您无权查看此工单')

    context = _build_detail_context(request, order)
    return render(request, 'order_detail.html', context)


@login_required
@transaction.atomic
def order_assign(request, order_id):
    order = get_object_or_404(WorkOrder, id=order_id)
    role = get_user_role(request.user)
    if role not in ['dispatcher', 'admin']:
        return HttpResponseForbidden('您没有派工权限')
    if not order.can_assign():
        messages.error(request, '当前工单状态不允许派工')
        return redirect('order_detail', order_id=order.id)

    if request.method == 'POST':
        form = AssignForm(request.POST, order=order)
        if form.is_valid():
            cd = form.cleaned_data
            old_tech = order.technician

            if old_tech and old_tech.id != cd['technician'].id:
                add_timeline(order, TIMELINE_UNASSIGNED, request.user,
                            f'原派工师傅：{old_tech.name}，已取消')

            dispatcher = None
            if hasattr(request.user, 'dispatcher_profile'):
                dispatcher = request.user.dispatcher_profile

            order.technician = cd['technician']
            order.dispatcher = dispatcher
            order.scheduled_start = cd['scheduled_start']
            order.scheduled_end = cd['scheduled_end']
            order.status = WorkOrder.STATUS_ASSIGNED
            order.save()

            add_timeline(order, TIMELINE_ASSIGNED, request.user,
                        f'分派师傅：{cd["technician"].name}，预约时间：{cd["scheduled_start"].strftime("%Y-%m-%d %H:%M")} ~ {cd["scheduled_end"].strftime("%Y-%m-%d %H:%M")}')

            if request.headers.get('HX-Request'):
                context = _build_detail_context(request, order)
                return render(request, '_order_detail_panel.html', context)
            messages.success(request, '派工成功！')
            return redirect('order_detail', order_id=order.id)
        else:
            errors = '\n'.join([f'{k}: {v[0]}' for k, v in form.errors.items()])
            if request.headers.get('HX-Request'):
                return HttpResponse(f'<div class="alert alert-danger">{errors}</div>', status=422)
            messages.error(request, f'派工失败：{errors}')
            return redirect('order_detail', order_id=order.id)
    return redirect('order_detail', order_id=order.id)


@login_required
@transaction.atomic
def order_start(request, order_id):
    order = get_object_or_404(WorkOrder, id=order_id)
    if not order.can_start(request.user):
        messages.error(request, '您不能启动此工单')
        return redirect('order_detail', order_id=order.id)

    if request.method == 'POST':
        form = StartWorkForm(request.POST)
        note = ''
        if form.is_valid():
            note = form.cleaned_data.get('arrival_note', '')

        order.arrived_at = timezone.now()
        order.status = WorkOrder.STATUS_IN_PROGRESS
        order.save()

        content = f'师傅到场，开始处理'
        if note:
            content += f'。备注：{note}'
        add_timeline(order, TIMELINE_ARRIVED, request.user, content)

        if request.headers.get('HX-Request'):
            context = _build_detail_context(request, order)
            return render(request, '_order_detail_panel.html', context)
        messages.success(request, '已到场，开始处理！')
        return redirect('order_detail', order_id=order.id)
    return redirect('order_detail', order_id=order.id)


@login_required
@transaction.atomic
def order_finish(request, order_id):
    order = get_object_or_404(WorkOrder, id=order_id)
    if not order.can_finish(request.user):
        messages.error(request, '您不能完成此工单')
        return redirect('order_detail', order_id=order.id)

    if request.method == 'POST':
        form = FinishWorkForm(request.POST)
        formset = MaterialFormSet(request.POST, instance=order)

        if form.is_valid() and formset.is_valid():
            cd = form.cleaned_data
            order.result = cd['result']
            order.photo_placeholder = cd['photo_placeholder']
            order.finished_at = timezone.now()
            order.status = WorkOrder.STATUS_DONE
            order.save()

            materials = formset.save(commit=False)
            for obj in formset.deleted_objects:
                obj.delete()
            for m in materials:
                if not m.unit_price and m.material_id:
                    m.unit_price = m.material.price
                m.order = order
                m.save()

            material_info = []
            for m in order.materials.all():
                material_info.append(f'{m.material.name}x{m.quantity}{m.material.unit}')
            content = f'处理完成：{cd["result"][:80]}'
            if material_info:
                content += f'。耗材：{", ".join(material_info)}'
            add_timeline(order, TIMELINE_FINISHED, request.user, content)

            if request.headers.get('HX-Request'):
                context = _build_detail_context(request, order)
                return render(request, '_order_detail_panel.html', context)
            messages.success(request, '工单处理完成，等待住户确认！')
            return redirect('order_detail', order_id=order.id)
        else:
            errors = []
            if form.errors:
                errors.extend([f'{k}: {v[0]}' for k, v in form.errors.items()])
            for i, f in enumerate(formset):
                if f.errors:
                    for k, v in f.errors.items():
                        errors.append(f'第{i+1}行-{k}: {v[0]}')
            err_msg = '\n'.join(errors) if errors else '表单校验失败'
            if request.headers.get('HX-Request'):
                return HttpResponse(f'<div class="alert alert-danger">{err_msg}</div>', status=422)
            messages.error(request, f'提交失败：{err_msg}')
            return redirect('order_detail', order_id=order.id)
    return redirect('order_detail', order_id=order.id)


@login_required
@transaction.atomic
def order_confirm(request, order_id):
    order = get_object_or_404(WorkOrder, id=order_id)
    if not order.can_confirm(request.user):
        if order.status != WorkOrder.STATUS_DONE:
            messages.error(request, '该工单当前不可确认')
        elif order.resident.user_id != request.user.id:
            messages.error(request, '您只能确认自己提交的报修工单！')
        else:
            messages.error(request, '您没有确认权限')
        return redirect('order_detail', order_id=order.id)

    if request.method == 'POST':
        form = ConfirmForm(request.POST)
        if form.is_valid():
            cd = form.cleaned_data
            order.confirmed_at = timezone.now()
            order.confirmed_by = request.user
            order.satisfaction = int(cd['satisfaction'])
            order.confirm_remark = cd['confirm_remark']
            order.status = WorkOrder.STATUS_CONFIRMED
            order.save()

            content = f'住户确认满意，满意度：{order.satisfaction}星'
            if cd['confirm_remark']:
                content += f'。评价：{cd["confirm_remark"]}'
            add_timeline(order, TIMELINE_CONFIRMED, request.user, content)

            if request.headers.get('HX-Request'):
                context = _build_detail_context(request, order)
                return render(request, '_order_detail_panel.html', context)
            messages.success(request, '确认成功！')
            return redirect('order_detail', order_id=order.id)
        else:
            errors = '\n'.join([f'{k}: {v[0]}' for k, v in form.errors.items()])
            if request.headers.get('HX-Request'):
                return HttpResponse(f'<div class="alert alert-danger">{errors}</div>', status=422)
            messages.error(request, f'确认失败：{errors}')
            return redirect('order_detail', order_id=order.id)
    return redirect('order_detail', order_id=order.id)


@login_required
@transaction.atomic
def order_rework(request, order_id):
    order = get_object_or_404(WorkOrder, id=order_id)
    if not order.can_rework(request.user):
        if order.resident.user_id != request.user.id:
            messages.error(request, '您只能申请自己工单的返工！')
        elif order.status == WorkOrder.STATUS_CLOSED and order.rework_count >= 3:
            messages.error(request, '该工单已达到最大返工次数(3次)，请联系调度员处理')
        else:
            messages.error(request, '该工单当前不可申请返工')
        return redirect('order_detail', order_id=order.id)

    if request.method == 'POST':
        form = ReworkForm(request.POST)
        if form.is_valid():
            cd = form.cleaned_data

            add_timeline(order, TIMELINE_REWORK_REQUEST, request.user,
                        f'返工原因：{cd["rework_reason"]}')

            new_rework_count = order.rework_count + 1

            rework_order = WorkOrder.objects.create(
                order_no=generate_order_no(),
                room=order.room,
                resident=order.resident,
                problem_type=order.problem_type,
                urgency=WorkOrder.URGENCY_HIGH,
                description=f'【返工单-原#{order.order_no}】原描述：{order.description}\n返工原因：{cd["rework_reason"]}',
                available_start=cd['available_start'],
                available_end=cd['available_end'],
                contact_phone=order.contact_phone,
                status=WorkOrder.STATUS_REWORK,
                is_rework=True,
                parent_order=order,
                rework_count=new_rework_count,
                rework_reason=cd['rework_reason'],
            )

            order.rework_count = new_rework_count
            if order.status == WorkOrder.STATUS_CLOSED:
                pass
            order.save()

            add_timeline(rework_order, TIMELINE_CREATED, request.user,
                        f'创建返工工单，原工单：{order.order_no}，返工原因：{cd["rework_reason"][:80]}')
            add_timeline(order, TIMELINE_REWORK_CREATED, request.user,
                        f'已生成返工单：{rework_order.order_no}（第{new_rework_count}次返工）')

            if request.headers.get('HX-Request'):
                context = _build_detail_context(request, rework_order)
                response = render(request, '_order_detail_panel.html', context)
                response['HX-Push-Url'] = reverse('order_detail', args=[rework_order.id])
                return response
            messages.success(request, f'返工申请成功！新返工单号：{rework_order.order_no}')
            return redirect('order_detail', order_id=rework_order.id)
        else:
            errors = '\n'.join([f'{k}: {v[0]}' for k, v in form.errors.items()])
            if request.headers.get('HX-Request'):
                return HttpResponse(f'<div class="alert alert-danger">{errors}</div>', status=422)
            messages.error(request, f'返工申请失败：{errors}')
            return redirect('order_detail', order_id=order.id)
    return redirect('order_detail', order_id=order.id)


@login_required
@transaction.atomic
def order_close(request, order_id):
    order = get_object_or_404(WorkOrder, id=order_id)
    role = get_user_role(request.user)
    if role not in ['dispatcher', 'admin']:
        return HttpResponseForbidden('没有权限')
    if not order.can_close():
        messages.error(request, '该工单不可关闭')
        return redirect('order_detail', order_id=order.id)

    if request.method == 'POST':
        order.status = WorkOrder.STATUS_CLOSED
        order.save()
        add_timeline(order, TIMELINE_CLOSED, request.user, '工单已正式关闭归档')
        messages.success(request, '工单已关闭！')
    return redirect('order_detail', order_id=order.id)


@login_required
def export_materials_csv(request):
    role = get_user_role(request.user)
    if role not in ['dispatcher', 'admin']:
        return HttpResponseForbidden('没有权限导出')

    now = timezone.now()
    year = int(request.GET.get('year', now.year))
    month = int(request.GET.get('month', now.month))

    start_date = datetime(year, month, 1, tzinfo=now.tzinfo)
    if month == 12:
        end_date = datetime(year + 1, 1, 1, tzinfo=now.tzinfo)
    else:
        end_date = datetime(year, month + 1, 1, tzinfo=now.tzinfo)

    order_ids = WorkOrder.objects.filter(
        finished_at__gte=start_date,
        finished_at__lt=end_date,
    ).values_list('id', flat=True)

    materials_qs = OrderMaterial.objects.filter(
        order_id__in=order_ids
    ).select_related('order', 'material').order_by('order__finished_at')

    response = HttpResponse(content_type='text/csv; charset=utf-8-sig')
    response['Content-Disposition'] = f'attachment; filename="维修耗材统计_{year}年{month}月.csv"'

    writer = csv.writer(response)
    writer.writerow([
        '工单号', '完工时间', '楼栋', '房号', '住户',
        '维修师傅', '耗材名称', '单位', '用量', '单价', '金额小计', '备注'
    ])

    summary = {}
    total_amount = Decimal('0')

    for om in materials_qs:
        row = [
            om.order.order_no,
            om.order.finished_at.strftime('%Y-%m-%d %H:%M') if om.order.finished_at else '',
            om.order.room.building,
            om.order.room.room_number,
            om.order.resident.name,
            om.order.technician.name if om.order.technician else '',
            om.material.name,
            om.material.unit,
            f'{om.quantity.normalize()}',
            f'{om.unit_price}',
            f'{om.subtotal}',
            om.remark,
        ]
        writer.writerow(row)
        key = (om.material.name, om.material.unit)
        if key not in summary:
            summary[key] = {'qty': Decimal('0'), 'amt': Decimal('0')}
        summary[key]['qty'] += om.quantity
        summary[key]['amt'] += om.subtotal
        total_amount += om.subtotal

    writer.writerow([])
    writer.writerow([f'===== {year}年{month}月耗材汇总 ====='])
    writer.writerow(['耗材名称', '单位', '总用量', '总金额'])
    for (name, unit), data in sorted(summary.items()):
        writer.writerow([name, unit, f'{data["qty"].normalize()}', f'{data["amt"]}'])
    writer.writerow(['合计', '', '', f'{total_amount}'])

    return response


@login_required
def dashboard(request):
    role = get_user_role(request.user)
    now = timezone.now()
    this_month_start = datetime(now.year, now.month, 1, tzinfo=now.tzinfo)
    if now.month == 12:
        next_month_start = datetime(now.year + 1, 1, 1, tzinfo=now.tzinfo)
    else:
        next_month_start = datetime(now.year, now.month + 1, 1, tzinfo=now.tzinfo)

    total_orders = WorkOrder.objects.count()
    month_orders = WorkOrder.objects.filter(created_at__gte=this_month_start, created_at__lt=next_month_start)
    month_count = month_orders.count()
    month_done_count = month_orders.filter(status__in=[WorkOrder.STATUS_CONFIRMED, WorkOrder.STATUS_CLOSED]).count()

    status_dist = {}
    for s, label in WorkOrder.STATUS_CHOICES:
        status_dist[label] = WorkOrder.objects.filter(status=s).count()

    top_materials = OrderMaterial.objects.filter(
        order__finished_at__gte=this_month_start,
        order__finished_at__lt=next_month_start,
    ).values('material__name', 'material__unit').annotate(
        total_qty=Sum('quantity'),
        total_amt=Sum(F('quantity') * F('unit_price'), output_field=DecimalField()),
        order_count=Count('order_id', distinct=True),
    ).order_by('-total_qty')[:10]

    tech_stats = WorkOrder.objects.filter(
        finished_at__gte=this_month_start,
        finished_at__lt=next_month_start,
        technician__isnull=False,
    ).values('technician__name').annotate(
        done_count=Count('id'),
        avg_sat=Avg('satisfaction'),
    ).order_by('-done_count')

    context = {
        'role': role,
        'total_orders': total_orders,
        'month_count': month_count,
        'month_done_count': month_done_count,
        'status_dist': status_dist,
        'top_materials': list(top_materials),
        'tech_stats': list(tech_stats),
        'year': now.year,
        'month': now.month,
        'month_timeout_count': month_orders.filter(is_timeout=True).count(),
        'total_timeout_count': WorkOrder.objects.filter(is_timeout=True).count(),
    }
    return render(request, 'dashboard.html', context)


@login_required
@transaction.atomic
def order_reschedule_request(request, order_id):
    order = get_object_or_404(WorkOrder, id=order_id)
    if not order.can_reschedule(request.user):
        if order.status in [WorkOrder.STATUS_IN_PROGRESS, WorkOrder.STATUS_DONE, WorkOrder.STATUS_CONFIRMED, WorkOrder.STATUS_CLOSED]:
            messages.error(request, '已到场、待确认、已确认、已关闭的工单不得改期')
        elif order.resident.user_id != request.user.id:
            messages.error(request, '非报修住户不能申请改期')
        else:
            messages.error(request, '当前工单状态不允许申请改期')
        return redirect('order_detail', order_id=order.id)

    if request.method == 'POST':
        form = RescheduleRequestForm(request.POST, order=order)
        if form.is_valid():
            cd = form.cleaned_data
            reschedule = RescheduleRequest.objects.create(
                order=order,
                requester=request.user,
                reason=cd['reason'],
                new_available_start=cd['new_available_start'],
                new_available_end=cd['new_available_end'],
            )
            add_timeline(order, TIMELINE_RESCHEDULE_REQUEST, request.user,
                        f'住户申请改期，原因：{cd["reason"][:80]}，新可上门时间：{cd["new_available_start"].strftime("%Y-%m-%d %H:%M")} ~ {cd["new_available_end"].strftime("%Y-%m-%d %H:%M")}')

            if request.headers.get('HX-Request'):
                context = _build_detail_context(request, order)
                return render(request, '_order_detail_panel.html', context)
            messages.success(request, '改期申请已提交，等待调度员审批')
            return redirect('order_detail', order_id=order.id)
        else:
            errors = '\n'.join([f'{k}: {v[0]}' for k, v in form.errors.items()])
            if request.headers.get('HX-Request'):
                return HttpResponse(f'<div class="alert alert-danger">{errors}</div>', status=422)
            messages.error(request, f'改期申请失败：{errors}')
            return redirect('order_detail', order_id=order.id)
    return redirect('order_detail', order_id=order.id)


@login_required
@transaction.atomic
def order_reschedule_approve(request, order_id, req_id):
    order = get_object_or_404(WorkOrder, id=order_id)
    reschedule = get_object_or_404(RescheduleRequest, id=req_id, order=order)
    role = get_user_role(request.user)
    if role not in ['dispatcher', 'admin']:
        return HttpResponseForbidden('没有审批权限')
    if reschedule.status != RescheduleRequest.STATUS_PENDING:
        messages.error(request, '该改期申请已处理')
        return redirect('order_detail', order_id=order.id)

    if request.method == 'POST':
        form = RescheduleReviewForm(request.POST, reschedule_request=reschedule)
        if form.is_valid():
            cd = form.cleaned_data
            tech = cd.get('new_technician')
            start = cd.get('new_scheduled_start')
            end = cd.get('new_scheduled_end')

            old_scheduled_start = order.scheduled_start
            old_scheduled_end = order.scheduled_end
            old_technician = order.technician

            if tech and start and end:
                reschedule.status = RescheduleRequest.STATUS_REASSIGNED
                reschedule.new_technician = tech
                reschedule.new_scheduled_start = start
                reschedule.new_scheduled_end = end
                reschedule.reviewed_by = request.user
                reschedule.review_note = cd.get('review_note', '')
                reschedule.reviewed_at = timezone.now()
                reschedule.save()

                if old_technician and old_technician.id != tech.id:
                    add_timeline(order, TIMELINE_UNASSIGNED, request.user,
                                f'改期改派：原派工师傅「{old_technician.name}」已取消')

                order.available_start = reschedule.new_available_start
                order.available_end = reschedule.new_available_end
                order.technician = tech
                order.scheduled_start = start
                order.scheduled_end = end
                order.status = WorkOrder.STATUS_ASSIGNED
                order.save()

                add_timeline(order, TIMELINE_RESCHEDULE_REASSIGNED, request.user,
                            f'改期改派：改派师傅「{tech.name}」，预约时间：{start.strftime("%Y-%m-%d %H:%M")} ~ {end.strftime("%Y-%m-%d %H:%M")}（原预约：{old_scheduled_start.strftime("%Y-%m-%d %H:%M") if old_scheduled_start else "-"} ~ {old_scheduled_end.strftime("%Y-%m-%d %H:%M") if old_scheduled_end else "-"}，原师傅：{old_technician.name if old_technician else "-"})')
            else:
                reschedule.status = RescheduleRequest.STATUS_APPROVED
                reschedule.reviewed_by = request.user
                reschedule.review_note = cd.get('review_note', '')
                reschedule.reviewed_at = timezone.now()
                reschedule.save()

                old_available_start = order.available_start
                old_available_end = order.available_end
                order.available_start = reschedule.new_available_start
                order.available_end = reschedule.new_available_end
                order.scheduled_start = None
                order.scheduled_end = None
                order.technician = None
                order.status = WorkOrder.STATUS_PENDING
                order.save()

                add_timeline(order, TIMELINE_RESCHEDULE_APPROVED, request.user,
                            f'改期批准：新可上门时间 {reschedule.new_available_start.strftime("%Y-%m-%d %H:%M")} ~ {reschedule.new_available_end.strftime("%Y-%m-%d %H:%M")}（原时间：{old_available_start.strftime("%Y-%m-%d %H:%M")} ~ {old_available_end.strftime("%Y-%m-%d %H:%M")}），需重新派工')

            if request.headers.get('HX-Request'):
                context = _build_detail_context(request, order)
                return render(request, '_order_detail_panel.html', context)
            messages.success(request, '改期审批完成')
            return redirect('order_detail', order_id=order.id)
        else:
            errors = '\n'.join([f'{k}: {v[0]}' for k, v in form.errors.items()])
            if request.headers.get('HX-Request'):
                return HttpResponse(f'<div class="alert alert-danger">{errors}</div>', status=422)
            messages.error(request, f'审批失败：{errors}')
            return redirect('order_detail', order_id=order.id)
    return redirect('order_detail', order_id=order.id)


@login_required
@transaction.atomic
def order_reschedule_reject(request, order_id, req_id):
    order = get_object_or_404(WorkOrder, id=order_id)
    reschedule = get_object_or_404(RescheduleRequest, id=req_id, order=order)
    role = get_user_role(request.user)
    if role not in ['dispatcher', 'admin']:
        return HttpResponseForbidden('没有审批权限')
    if reschedule.status != RescheduleRequest.STATUS_PENDING:
        messages.error(request, '该改期申请已处理')
        return redirect('order_detail', order_id=order.id)

    if request.method == 'POST':
        review_note = request.POST.get('review_note', '')
        reschedule.status = RescheduleRequest.STATUS_REJECTED
        reschedule.reviewed_by = request.user
        reschedule.review_note = review_note
        reschedule.reviewed_at = timezone.now()
        reschedule.save()

        content = f'改期申请已驳回'
        if review_note:
            content += f'，驳回原因：{review_note}'
        add_timeline(order, TIMELINE_RESCHEDULE_REJECTED, request.user, content)

        if request.headers.get('HX-Request'):
            context = _build_detail_context(request, order)
            return render(request, '_order_detail_panel.html', context)
        messages.success(request, '改期申请已驳回')
        return redirect('order_detail', order_id=order.id)
    return redirect('order_detail', order_id=order.id)


@login_required
@transaction.atomic
def order_technician_flag(request, order_id):
    order = get_object_or_404(WorkOrder, id=order_id)
    role = get_user_role(request.user)
    if role != 'technician':
        return HttpResponseForbidden('仅师傅可标记')
    if order.status != WorkOrder.STATUS_ASSIGNED:
        messages.error(request, '当前工单状态不允许标记')
        return redirect('order_detail', order_id=order.id)
    if not order.technician or order.technician.user_id != request.user.id:
        return HttpResponseForbidden('您不是此工单的负责师傅')

    if request.method == 'POST':
        form = TechnicianFlagForm(request.POST)
        if form.is_valid():
            cd = form.cleaned_data
            flag = TechnicianFlag.objects.create(
                order=order,
                flag_type=cd['flag_type'],
                suggestion=cd['suggestion'],
                created_by=request.user,
            )
            flag_label = dict(TechnicianFlag.FLAG_CHOICES).get(cd['flag_type'], cd['flag_type'])
            add_timeline(order, TIMELINE_TECHNICIAN_FLAG, request.user,
                        f'师傅标记：{flag_label}。下一步建议：{cd["suggestion"][:80]}')

            if request.headers.get('HX-Request'):
                context = _build_detail_context(request, order)
                return render(request, '_order_detail_panel.html', context)
            messages.success(request, f'已标记「{flag_label}」')
            return redirect('order_detail', order_id=order.id)
        else:
            errors = '\n'.join([f'{k}: {v[0]}' for k, v in form.errors.items()])
            if request.headers.get('HX-Request'):
                return HttpResponse(f'<div class="alert alert-danger">{errors}</div>', status=422)
            messages.error(request, f'标记失败：{errors}')
            return redirect('order_detail', order_id=order.id)
    return redirect('order_detail', order_id=order.id)


def check_and_mark_timeouts():
    now = timezone.now()
    configs = {c.urgency: c for c in TimeoutConfig.objects.all()}

    default_config = TimeoutConfig(assign_timeout_minutes=60, arrive_timeout_minutes=120)

    pending_orders = WorkOrder.objects.filter(
        status=WorkOrder.STATUS_PENDING,
        is_timeout=False,
    )
    for order in pending_orders:
        config = configs.get(order.urgency, default_config)
        deadline = order.created_at + timedelta(minutes=config.assign_timeout_minutes)
        if now > deadline:
            order.is_timeout = True
            order.timeout_type = WorkOrder.TIMEOUT_TYPE_ASSIGN
            order.timeout_at = now
            order.save()
            add_timeline(order, TIMELINE_TIMEOUT, None,
                        f'派工超时：创建时间 {order.created_at.strftime("%Y-%m-%d %H:%M")}，超时限制 {config.assign_timeout_minutes} 分钟')

    assigned_orders = WorkOrder.objects.filter(
        status=WorkOrder.STATUS_ASSIGNED,
        is_timeout=False,
        scheduled_start__isnull=False,
    )
    for order in assigned_orders:
        config = configs.get(order.urgency, default_config)
        deadline = order.scheduled_start + timedelta(minutes=config.arrive_timeout_minutes)
        if now > deadline:
            order.is_timeout = True
            order.timeout_type = WorkOrder.TIMEOUT_TYPE_ARRIVE
            order.timeout_at = now
            order.save()
            add_timeline(order, TIMELINE_TIMEOUT, None,
                        f'到场超时：预约时间 {order.scheduled_start.strftime("%Y-%m-%d %H:%M")}，超时限制 {config.arrive_timeout_minutes} 分钟')


@login_required
def export_timeout_csv(request):
    role = get_user_role(request.user)
    if role not in ['dispatcher', 'admin']:
        return HttpResponseForbidden('没有权限导出')

    now = timezone.now()
    year = int(request.GET.get('year', now.year))
    month = int(request.GET.get('month', now.month))

    start_date = datetime(year, month, 1, tzinfo=now.tzinfo)
    if month == 12:
        end_date = datetime(year + 1, 1, 1, tzinfo=now.tzinfo)
    else:
        end_date = datetime(year, month + 1, 1, tzinfo=now.tzinfo)

    timeout_orders = WorkOrder.objects.filter(
        is_timeout=True,
        timeout_at__gte=start_date,
        timeout_at__lt=end_date,
    ).select_related('room', 'resident', 'technician').order_by('-timeout_at')

    response = HttpResponse(content_type='text/csv; charset=utf-8-sig')
    response['Content-Disposition'] = f'attachment; filename="超时工单_{year}年{month}月.csv"'

    writer = csv.writer(response)
    writer.writerow([
        '工单号', '楼栋', '房号', '住户', '紧急程度', '当前状态',
        '超时类型', '创建时间', '预约时间', '超时标记时间', '维修师傅'
    ])

    for o in timeout_orders:
        writer.writerow([
            o.order_no,
            o.room.building,
            o.room.room_number,
            o.resident.name if o.resident else '',
            o.get_urgency_display(),
            o.get_status_display(),
            o.get_timeout_type_display(),
            o.created_at.strftime('%Y-%m-%d %H:%M'),
            o.scheduled_start.strftime('%Y-%m-%d %H:%M') if o.scheduled_start else '',
            o.timeout_at.strftime('%Y-%m-%d %H:%M') if o.timeout_at else '',
            o.technician.name if o.technician else '',
        ])

    return response
