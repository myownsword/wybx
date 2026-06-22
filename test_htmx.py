"""
HTMX 局部刷新专项验证脚本
验证所有操作的 HTMX 请求是否返回正确的局部页面
"""
import os, sys, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'wybx_system.settings')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
django.setup()

from django.test import Client
from django.contrib.auth.models import User
from django.urls import reverse
from django.utils import timezone as django_tz
from datetime import timedelta, timezone as py_tz
from decimal import Decimal
import zoneinfo
import re

from repair.models import (
    Room, Resident, Technician, Dispatcher, Material,
    WorkOrder, OrderMaterial, Timeline, generate_order_no,
)

SH_TZ = zoneinfo.ZoneInfo('Asia/Shanghai')
now_local = django_tz.now().astimezone(SH_TZ)

def fmt_dt(dt):
    return dt.strftime('%Y-%m-%dT%H:%M')

def hdr():
    return {'HTTP_HX_REQUEST': 'true'}

def log(msg, level='INFO'):
    color = {
        'OK': '\033[32m✓\033[0m',
        'FAIL': '\033[31m✗\033[0m',
        'STEP': '\033[36m━\033[0m',
        'INFO': '\033[90m·\033[0m',
    }.get(level, '·')
    print(f'{color} {msg}')

all_pass = True
def verify(cond, msg):
    global all_pass
    if cond:
        log(msg, 'OK')
    else:
        log(msg, 'FAIL')
        all_pass = False
    return cond

print('=' * 70)
print('  物业报修系统 - HTMX 局部刷新专项验证')
print('=' * 70)

# === 准备：创建一个全新工单 ===
log('准备：创建测试工单', 'STEP')
resident_user = User.objects.get(username='zhangsan')
resident = resident_user.resident_profile
tech_user = User.objects.get(username='wangwu')
tech = tech_user.technician_profile
disp_user = User.objects.get(username='zhaoliu')

avail_s = now_local + timedelta(hours=500)
avail_e = now_local + timedelta(hours=550)

order = WorkOrder.objects.create(
    order_no=generate_order_no(),
    room=resident.room,
    resident=resident,
    problem_type='plumbing',
    urgency='high',
    description='【HTMX测试】厨房水龙头漏水，需要更换阀芯',
    available_start=avail_s.astimezone(py_tz.utc).replace(tzinfo=py_tz.utc),
    available_end=avail_e.astimezone(py_tz.utc).replace(tzinfo=py_tz.utc),
    contact_phone=resident.phone,
    status='pending',
)
verify(order.id is not None, f'测试工单已创建: {order.order_no} (id={order.id})')

# ========== 1. 调度员 HTMX 派工 ==========
print()
log('步骤1：调度员 HTMX 派工', 'STEP')
sch_s = avail_s + timedelta(hours=2)
sch_e = avail_s + timedelta(hours=4)

disp_client = Client()
disp_client.force_login(disp_user)

resp = disp_client.post(
    reverse('order_assign', args=[order.id]),
    {
        'technician': tech.id,
        'scheduled_start': fmt_dt(sch_s),
        'scheduled_end': fmt_dt(sch_e),
    },
    **hdr(),
)
verify(resp.status_code == 200, f'HTMX派工响应HTTP {resp.status_code}')

html = resp.content.decode('utf-8')
verify('order-detail-panel' in html, '返回内容包含 order-detail-panel 容器')
verify('已派工' in html or 'status_assigned' in html or 'badge' in html, '页面包含已派工状态标识')
verify(tech.name in html, f'页面显示分派的师傅姓名({tech.name})')
verify('派工' in html and '时间线' in html, '时间线区域存在')

timeline_has_assign = '分派' in html or '派工' in html
verify(timeline_has_assign, '时间线包含派工记录')

def check_timeline(html, keyword):
    return keyword in html

order.refresh_from_db()
verify(order.status == 'assigned', f'工单状态=已派工 (实际: {order.get_status_display()})')
tech_name = order.technician.name if order.technician else '无'
verify(order.technician_id == tech.id, f'分派师傅正确: {tech_name}')

# ========== 2. 师傅 HTMX 到场 ==========
print()
log('步骤2：维修师傅 HTMX 到场', 'STEP')
tech_client = Client()
tech_client.force_login(tech_user)

resp = tech_client.post(
    reverse('order_start', args=[order.id]),
    {'arrival_note': '已到达住户家，准备工具'},
    **hdr(),
)
verify(resp.status_code == 200, f'HTMX到场响应HTTP {resp.status_code}')

html = resp.content.decode('utf-8')
verify('处理中' in html, '页面显示"处理中"状态')
verify('到场' in html, '时间线包含到场记录')

order.refresh_from_db()
verify(order.status == 'in_progress', f'工单状态=处理中 (实际: {order.get_status_display()})')
verify(order.arrived_at is not None, '到场时间已记录')

# ========== 3. 师傅 HTMX 完工 + 耗材登记 ==========
print()
log('步骤3：维修师傅 HTMX 完工登记 + 耗材', 'STEP')

material1 = Material.objects.get(name__contains='生料带')
material2 = Material.objects.get(name__contains='水龙头(普通)')

resp = tech_client.post(
    reverse('order_finish', args=[order.id]),
    {
        'result': '已更换水龙头阀芯，测试无渗漏。更换配件：陶瓷阀芯1个，生料带1卷。',
        'photo_placeholder': '1. 漏水点特写 2. 新阀芯安装中 3. 完工测试照',
        'materials-TOTAL_FORMS': '2',
        'materials-INITIAL_FORMS': '0',
        'materials-0-material': str(material1.id),
        'materials-0-quantity': '1',
        'materials-0-remark': '密封用',
        'materials-1-material': str(material2.id),
        'materials-1-quantity': '1',
        'materials-1-remark': '35mm陶瓷阀芯',
    },
    **hdr(),
)
verify(resp.status_code == 200, f'HTMX完工响应HTTP {resp.status_code}')

html = resp.content.decode('utf-8')
verify('待确认' in html, '页面显示"待确认"状态')
verify('处理结果' in html, '显示处理结果区域')
verify('耗材清单' in html, '显示耗材清单')
verify(material1.name in html or '生料带' in html, '耗材清单包含生料带')
verify(material2.name in html or '阀芯' in html, '耗材清单包含阀芯')
verify('合计' in html, '耗材金额合计显示')

order.refresh_from_db()
verify(order.status == 'done', f'工单状态=待确认 (实际: {order.get_status_display()})')
verify(order.finished_at is not None, '完工时间已记录')
verify(order.materials.count() == 2, f'耗材登记数量=2 (实际: {order.materials.count()})')

mat_total = sum(m.subtotal for m in order.materials.all())
verify(mat_total > 0, f'耗材金额合计: ¥{mat_total}')

# ========== 4. 住户 HTMX 确认 ==========
print()
log('步骤4：住户 HTMX 确认服务', 'STEP')
resident_client = Client()
resident_client.force_login(resident_user)

resp = resident_client.post(
    reverse('order_confirm', args=[order.id]),
    {
        'satisfaction': '5',
        'confirm_remark': '师傅很专业，修得很快，非常满意！',
    },
    **hdr(),
)
verify(resp.status_code == 200, f'HTMX确认响应HTTP {resp.status_code}')

html = resp.content.decode('utf-8')
verify('已确认' in html, '页面显示"已确认"状态')
verify('住户评价' in html, '显示住户评价区域')
verify('5星' in html or 'star-fill' in html, '显示5星评价')

order.refresh_from_db()
verify(order.status == 'confirmed', f'工单状态=已确认 (实际: {order.get_status_display()})')
verify(order.satisfaction == 5, f'满意度=5星 (实际: {order.satisfaction})')
verify(order.confirmed_at is not None, '确认时间已记录')

# ========== 5. 住户 HTMX 申请返工 ==========
print()
log('步骤5：住户 HTMX 申请返工', 'STEP')

# 先把工单关闭
order.status = 'closed'
order.save()

rw_avail_s = now_local + timedelta(hours=600)
rw_avail_e = now_local + timedelta(hours=650)

resp = resident_client.post(
    reverse('order_rework', args=[order.id]),
    {
        'rework_reason': '第二天发现水龙头根部还有点渗水，上次没处理好。',
        'available_start': fmt_dt(rw_avail_s),
        'available_end': fmt_dt(rw_avail_e),
    },
    **hdr(),
)
verify(resp.status_code == 200, f'HTMX返工响应HTTP {resp.status_code}')

html = resp.content.decode('utf-8')
verify('返工' in html, '页面包含返工标识')
verify('order-detail-panel' in html, '返回完整的详情面板')

# 找到新创建的返工单
rework_order = WorkOrder.objects.filter(parent_order=order, is_rework=True).order_by('-id').first()
verify(rework_order is not None, f'返工单已创建: {rework_order.order_no if rework_order else "无"}')

if rework_order:
    rework_order.refresh_from_db()
    verify(rework_order.status == 'rework', f'返工单状态=返工 (实际: {rework_order.get_status_display()})')
    verify(rework_order.is_rework is True, '返工标记正确')
    verify(rework_order.urgency == 'high', '返工单自动升级为紧急')

# ========== 6. 验证所有表单在局部刷新中正确渲染 ==========
print()
log('步骤6：验证各状态下表单正确渲染', 'STEP')

# 找一个待派工的工单，验证派工表单
pending_order = WorkOrder.objects.filter(status='pending').first()
if pending_order:
    resp = disp_client.get(reverse('order_detail', args=[pending_order.id]))
    html = resp.content.decode('utf-8')
    verify('分派维修师傅' in html, '待派工工单：显示派工表单标题')
    verify('form-select' in html and 'technician' in html, '待派工工单：师傅下拉选择器存在')
    verify('datetime-local' in html, '待派工工单：时间选择器存在')

# 找一个已派工的工单，验证到场表单
assigned_order = WorkOrder.objects.filter(status='assigned').filter(technician=tech).first()
if assigned_order:
    resp = tech_client.get(reverse('order_detail', args=[assigned_order.id]))
    html = resp.content.decode('utf-8')
    verify('确认到场' in html, '已派工工单：显示到场按钮')

# 找一个处理中的工单，验证完工表单+耗材
in_progress_order = WorkOrder.objects.filter(status='in_progress').filter(technician=tech).first()
if in_progress_order:
    resp = tech_client.get(reverse('order_detail', args=[in_progress_order.id]))
    html = resp.content.decode('utf-8')
    verify('完工登记' in html, '处理中工单：显示完工登记表单')
    verify('耗材登记' in html, '处理中工单：显示耗材登记区域')
    verify('TOTAL_FORMS' in html or 'form-' in html, '处理中工单：formset管理字段存在')

# ========== 7. 验证时间线更新 ==========
print()
log('步骤7：验证时间线完整记录', 'STEP')
order.refresh_from_db()
tl_count = order.timelines.count()
verify(tl_count >= 4, f'原工单时间线记录≥4条 (实际: {tl_count}条)')

events = [t.get_event_type_display() for t in order.timelines.all()]
log(f'  时间线事件: {" → ".join(events)}', 'INFO')

# ========== 8. 验证状态、耗材持久化（重启模拟） ==========
print()
log('步骤8：验证数据持久化', 'STEP')
from django.db import close_old_connections
close_old_connections()

order2 = WorkOrder.objects.get(id=order.id)
verify(order2.status == 'closed', f'重启后状态=已关闭 (实际: {order2.get_status_display()})')
verify(order2.materials.count() == 2, f'重启后耗材记录数=2 (实际: {order2.materials.count()})')
verify(order2.timelines.count() == tl_count, f'重启后时间线记录数={tl_count}')

print()
print('=' * 70)
if all_pass:
    print('  ✓ 全部 HTMX 验证通过！局部刷新功能正常。')
else:
    print('  ✗ 部分验证失败，请检查上方日志。')
print('=' * 70)

sys.exit(0 if all_pass else 1)
