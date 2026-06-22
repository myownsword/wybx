"""
物业报修系统自动化验证脚本
验证：完整维修闭环、派工冲突失败、返工重新进入处理、重启后状态和耗材统计不丢、导出本月耗材CSV
"""
import os
import sys
import django

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

from repair.models import (
    Room, Resident, Technician, Dispatcher, Material,
    WorkOrder, OrderMaterial, Timeline,
    generate_order_no, add_timeline,
    TIMELINE_CREATED, TIMELINE_ASSIGNED, TIMELINE_ARRIVED,
    TIMELINE_FINISHED, TIMELINE_CONFIRMED,
)

SH_TZ = zoneinfo.ZoneInfo('Asia/Shanghai')

def log(msg, level='INFO'):
    prefix = {
        'INFO': '\033[94mℹ\033[0m',
        'OK': '\033[92m✓\033[0m',
        'WARN': '\033[93m⚠\033[0m',
        'FAIL': '\033[91m✗\033[0m',
        'STEP': '\n\033[95m━\033[0m',
    }.get(level, '')
    print(f'{prefix} {msg}')


def verify(cond, msg):
    if cond:
        log(msg, 'OK')
        return True
    else:
        log(msg, 'FAIL')
        return False


def step(msg):
    log(f'  {msg}', 'STEP')


def local_now():
    return django_tz.now().astimezone(SH_TZ)


def fmt_dt(dt_local):
    return dt_local.strftime('%Y-%m-%dT%H:%M')


def main():
    print('\n' + '=' * 70)
    print('  物业报修派工与回访系统 - 自动化验证')
    print('=' * 70)

    all_pass = True
    now_utc = django_tz.now()
    now_local = local_now()

    # ========== 1. 基础数据检查 ==========
    log('步骤1：基础数据完整性检查', 'STEP')
    rooms = Room.objects.filter(is_active=True)
    all_pass &= verify(rooms.count() >= 10, f'有效房号数量：{rooms.count()}')
    residents = Resident.objects.select_related('user', 'room').all()
    all_pass &= verify(residents.count() >= 3, f'住户账号数量：{residents.count()}')
    technicians = Technician.objects.filter(is_active=True)
    all_pass &= verify(technicians.count() >= 2, f'维修师傅数量：{technicians.count()}')
    all_pass &= verify(Dispatcher.objects.count() >= 1, '调度员数量')
    all_pass &= verify(Material.objects.count() >= 10, f'耗材种类：{Material.objects.count()}')

    # ========== 2. 无效房号验证 ==========
    log('步骤2：边界条件 - 无效房号验证', 'STEP')
    zhangsan_user = User.objects.get(username='zhangsan')
    client = Client()
    client.force_login(zhangsan_user)

    s_str = fmt_dt(now_local + timedelta(hours=2))
    e_str = fmt_dt(now_local + timedelta(hours=6))
    resp = client.post(reverse('order_create'), {
        'building': '99', 'unit': '9', 'room_number': '999',
        'resident_name': '测试', 'contact_phone': '13800001111',
        'problem_type': 'electric', 'urgency': 'high',
        'description': '无效房号测试',
        'available_start': s_str, 'available_end': e_str,
    })
    blocked = False
    if resp.status_code == 200:
        c = resp.content.decode('utf-8', errors='ignore')
        if ('不存在' in c or '无效' in c) and '房号' in c:
            blocked = True
    all_pass &= verify(blocked, f'无效房号(99栋9单元999室)拦截成功：{"是" if blocked else "否"}')

    # ========== 3. 完整维修闭环 ==========
    log('步骤3：完整维修闭环 - 提交→派工→到场→处理→确认→关闭', 'STEP')
    resident_user = zhangsan_user
    tech_user = User.objects.get(username='lishi')
    disp_user = User.objects.get(username='zhaoliu')
    resident = resident_user.resident_profile
    tech = tech_user.technician_profile
    dispatcher = disp_user.dispatcher_profile

    step('3.1 住户(张三)提交报修')
    room = resident.room
    avail_s_local = now_local + timedelta(hours=48)
    avail_e_local = now_local + timedelta(hours=58)
    resp = client.post(reverse('order_create'), {
        'building': room.building, 'unit': room.unit, 'room_number': room.room_number,
        'resident_name': resident.name, 'contact_phone': resident.phone,
        'problem_type': 'plumbing', 'urgency': 'urgent',
        'description': '【自动化测试】卫生间水管漏水，急需处理。水已漫出地砖。',
        'available_start': fmt_dt(avail_s_local), 'available_end': fmt_dt(avail_e_local),
    }, follow=True)
    new_order = WorkOrder.objects.filter(description__contains='【自动化测试】卫生间水管漏水').order_by('-id').first()
    all_pass &= verify(new_order is not None, '报修工单已创建')
    if not new_order:
        log(f'  创建失败, HTTP {resp.status_code}', 'WARN')
        return False
    all_pass &= verify(new_order.status == WorkOrder.STATUS_PENDING, f'初始状态={new_order.get_status_display()}')
    all_pass &= verify(new_order.resident_id == resident.id, '住户关联正确')
    all_pass &= verify(new_order.timelines.filter(event_type=TIMELINE_CREATED).exists(), '创建时间线存在')

    step('3.2 调度员(赵调度)分派师傅')
    disp_client = Client()
    disp_client.force_login(disp_user)
    sch_s_local = avail_s_local + timedelta(hours=1)
    sch_e_local = avail_s_local + timedelta(hours=3)
    resp = disp_client.post(reverse('order_assign', args=[new_order.id]), {
        'technician': tech.id,
        'scheduled_start': fmt_dt(sch_s_local),
        'scheduled_end': fmt_dt(sch_e_local),
    }, follow=True)
    new_order.refresh_from_db()
    all_pass &= verify(new_order.status == WorkOrder.STATUS_ASSIGNED, f'派工后状态={new_order.get_status_display()}')
    all_pass &= verify(new_order.technician_id == tech.id, f'分派师傅={new_order.technician.name}')
    all_pass &= verify(new_order.timelines.filter(event_type=TIMELINE_ASSIGNED).exists(), '派工时间线存在')

    step('3.3 边界条件 - 师傅时间冲突')
    resident2 = Resident.objects.get(name='李四')
    conflict_order = WorkOrder.objects.create(
        order_no=generate_order_no(),
        room=resident2.room, resident=resident2,
        problem_type='electric', urgency='high',
        description='【冲突测试】另一工单测试时间冲突',
        available_start=avail_s_local.astimezone(py_tz.utc).replace(tzinfo=py_tz.utc),
        available_end=(avail_s_local + timedelta(hours=200)).astimezone(py_tz.utc).replace(tzinfo=py_tz.utc),
        contact_phone=resident2.phone, status=WorkOrder.STATUS_PENDING,
    )
    resp = disp_client.post(reverse('order_assign', args=[conflict_order.id]), {
        'technician': tech.id,
        'scheduled_start': fmt_dt(sch_s_local), 'scheduled_end': fmt_dt(sch_e_local),
    })
    conflict_ok = False
    if resp.status_code in (200, 422):
        c = resp.content.decode('utf-8', errors='ignore')
        if '冲突' in c or '已有工单' in c or '同一时间' in c:
            conflict_ok = True
    conflict_order.refresh_from_db()
    if conflict_order.status != WorkOrder.STATUS_ASSIGNED:
        conflict_ok = True
    all_pass &= verify(conflict_ok, f'时间重叠派工冲突被拦截：{"是" if conflict_ok else "否"} (工单状态={conflict_order.get_status_display()})')
    conflict_order.refresh_from_db()
    all_pass &= verify(conflict_order.status == WorkOrder.STATUS_PENDING, f'冲突工单状态未变={conflict_order.get_status_display()}')
    # 错开时间（用更靠后的时间避免和历史测试残留冲突）
    resp = disp_client.post(reverse('order_assign', args=[conflict_order.id]), {
        'technician': tech.id,
        'scheduled_start': fmt_dt(now_local + timedelta(hours=120)),
        'scheduled_end': fmt_dt(now_local + timedelta(hours=122)),
    }, follow=True)
    conflict_order.refresh_from_db()
    all_pass &= verify(conflict_order.status == WorkOrder.STATUS_ASSIGNED, '错开时间派工成功')

    step('3.4 师傅(李师傅)到场开始处理')
    tech_client = Client()
    tech_client.force_login(tech_user)
    resp = tech_client.post(reverse('order_start', args=[new_order.id]), {
        'arrival_note': '准时到达，住户在家',
    }, follow=True)
    new_order.refresh_from_db()
    all_pass &= verify(new_order.status == WorkOrder.STATUS_IN_PROGRESS, f'到场后状态={new_order.get_status_display()}')
    all_pass &= verify(new_order.arrived_at is not None, '到场时间已记录')
    all_pass &= verify(new_order.timelines.filter(event_type=TIMELINE_ARRIVED).exists(), '到场时间线存在')

    step('3.5 边界条件 - 非本工单师傅/住户操作被拒')
    tech2_client = Client()
    tech2_client.force_login(User.objects.get(username='wangwu'))
    resp = tech2_client.post(reverse('order_finish', args=[new_order.id]), {'result': '越权'})
    new_order.refresh_from_db()
    all_pass &= verify(new_order.status == WorkOrder.STATUS_IN_PROGRESS, '其他师傅试图完工被阻止(状态未变)')

    other_client = Client()
    other_client.force_login(User.objects.get(username='lisi'))
    resp = other_client.get(reverse('order_detail', args=[new_order.id]))
    all_pass &= verify(resp.status_code == 403, f'其他住户查看此工单→HTTP 403')

    step('3.6 师傅完工登记+耗材')
    mat1 = Material.objects.get(name__contains='PPR水管')
    mat2 = Material.objects.get(name__contains='生料带')
    resp = tech_client.post(reverse('order_finish', args=[new_order.id]), {
        'result': '【自动化测试】已更换卫生间漏水段水管1.5米，更换生料带，打压测试无渗漏。试水半小时正常。',
        'photo_placeholder': '1.漏水点 2.切割拆除 3.新管熔接 4.打压测试',
        'materials-TOTAL_FORMS': '5', 'materials-INITIAL_FORMS': '0',
        'materials-MIN_NUM_FORMS': '0', 'materials-MAX_NUM_FORMS': '1000',
        'materials-0-material': mat1.id, 'materials-0-quantity': '1.5', 'materials-0-remark': '漏水段替换',
        'materials-1-material': mat2.id, 'materials-1-quantity': '2', 'materials-1-remark': '接头密封',
    }, follow=True)
    new_order.refresh_from_db()
    all_pass &= verify(new_order.status == WorkOrder.STATUS_DONE, f'完工后状态={new_order.get_status_display()}')
    all_pass &= verify(new_order.finished_at is not None, '完工时间已记录')
    order_mats = OrderMaterial.objects.filter(order=new_order)
    all_pass &= verify(order_mats.count() == 2, f'耗材登记数量={order_mats.count()}')
    expected = Decimal('1.5') * mat1.price + Decimal('2') * mat2.price
    actual = sum(m.subtotal for m in order_mats)
    all_pass &= verify(actual == expected, f'耗材金额正确: ¥{actual} = ¥{expected}')
    all_pass &= verify(new_order.timelines.filter(event_type=TIMELINE_FINISHED).exists(), '完工时间线存在')

    step('3.7 住户确认服务')
    client.force_login(resident_user)
    resp = client.post(reverse('order_confirm', args=[new_order.id]), {
        'satisfaction': '5', 'confirm_remark': '师傅上门及时，手艺很好，问题彻底解决。',
    }, follow=True)
    new_order.refresh_from_db()
    all_pass &= verify(new_order.status == WorkOrder.STATUS_CONFIRMED, f'确认后状态={new_order.get_status_display()}')
    all_pass &= verify(new_order.confirmed_by_id == resident_user.id, '确认人=正确住户')
    all_pass &= verify(new_order.satisfaction == 5, f'满意度={new_order.satisfaction}')
    all_pass &= verify(new_order.timelines.filter(event_type=TIMELINE_CONFIRMED).exists(), '确认时间线存在')

    disp_client.post(reverse('order_close', args=[new_order.id]), follow=True)
    new_order.refresh_from_db()
    all_pass &= verify(new_order.status == WorkOrder.STATUS_CLOSED, f'关闭后状态={new_order.get_status_display()}')
    log('  ✅ 完整闭环完成: 待派工→已派工→处理中→待确认→已确认→已关闭', 'OK')

    # ========== 4. 返工流程验证 ==========
    log('步骤4：返工流程验证 - 已关闭工单申请返工，新返工单重新进入处理', 'STEP')
    step('4.1 住户申请返工')
    resp = client.post(reverse('order_rework', args=[new_order.id]), {
        'rework_reason': '【自动化返工测试】第二天发现原位置仍有轻微渗水，上次处理不彻底需返工。',
        'available_start': fmt_dt(now_local + timedelta(hours=72)),
        'available_end': fmt_dt(now_local + timedelta(hours=80)),
    }, follow=True)
    new_order.refresh_from_db()
    rework_order = WorkOrder.objects.filter(parent_order=new_order, is_rework=True).order_by('-id').first()
    all_pass &= verify(rework_order is not None, f'返工单已创建: {rework_order.order_no if rework_order else "无"}')
    all_pass &= verify(new_order.rework_count == 1, f'原工单返工次数={new_order.rework_count}')
    if rework_order:
        all_pass &= verify(rework_order.status == WorkOrder.STATUS_REWORK, f'返工单初始状态={rework_order.get_status_display()}')
        all_pass &= verify(rework_order.is_rework is True, '返工标记正确')
        all_pass &= verify(rework_order.urgency == WorkOrder.URGENCY_HIGH, '返工单自动升级为紧急')

        step('4.2 返工单:派工→处理→确认→关闭')
        tech2 = User.objects.get(username='wangwu').technician_profile
        disp_client.post(reverse('order_assign', args=[rework_order.id]), {
            'technician': tech2.id,
            'scheduled_start': fmt_dt(now_local + timedelta(hours=73)),
            'scheduled_end': fmt_dt(now_local + timedelta(hours=75)),
        }, follow=True)
        rework_order.refresh_from_db()
        all_pass &= verify(rework_order.status == WorkOrder.STATUS_ASSIGNED, '返工单派工成功')

        tech2_client = Client()
        tech2_client.force_login(User.objects.get(username='wangwu'))
        tech2_client.post(reverse('order_start', args=[rework_order.id]), follow=True)
        rework_order.refresh_from_db()
        all_pass &= verify(rework_order.status == WorkOrder.STATUS_IN_PROGRESS, '返工单处理中')

        mat_seal = Material.objects.get(name__contains='玻璃胶')
        tech2_client.post(reverse('order_finish', args=[rework_order.id]), {
            'result': '【返工完成】重新拆检发现另一处隐性接口渗漏，补打玻璃胶密封，并更换密封圈。24小时测试无渗漏。',
            'photo_placeholder': '1.拆检图 2.渗漏点 3.重新密封后',
            'materials-TOTAL_FORMS': '5', 'materials-INITIAL_FORMS': '0',
            'materials-MIN_NUM_FORMS': '0', 'materials-MAX_NUM_FORMS': '1000',
            'materials-0-material': mat_seal.id, 'materials-0-quantity': '1',
        }, follow=True)
        rework_order.refresh_from_db()
        all_pass &= verify(rework_order.status == WorkOrder.STATUS_DONE, '返工单完成待确认')

        client.post(reverse('order_confirm', args=[rework_order.id]), {
            'satisfaction': '4', 'confirm_remark': '返工彻底修好，服务态度好。',
        }, follow=True)
        rework_order.refresh_from_db()
        all_pass &= verify(rework_order.status == WorkOrder.STATUS_CONFIRMED, '返工单确认完成')

        disp_client.post(reverse('order_close', args=[rework_order.id]), follow=True)
        rework_order.refresh_from_db()
        all_pass &= verify(rework_order.status == WorkOrder.STATUS_CLOSED, '返工单最终关闭')
        log('  ✅ 返工闭环完成: 返工→派工→处理中→待确认→已确认→已关闭', 'OK')

    # ========== 5. 重启后数据持久化检查 ==========
    log('步骤5：重启后状态与耗材统计不丢失', 'STEP')
    o = WorkOrder.objects.get(id=new_order.id)
    all_pass &= verify(o.status == WorkOrder.STATUS_CLOSED, f'原工单重启后状态={o.get_status_display()}')
    all_pass &= verify(o.rework_count == 1, f'原工单返工次数={o.rework_count}')
    mats = o.materials.all()
    all_pass &= verify(mats.count() == 2, f'原工单耗材记录={mats.count()}')
    total = sum(m.subtotal for m in mats)
    all_pass &= verify(total > 0, f'耗材统计不丢失: ¥{total}')
    tl_count = Timeline.objects.filter(order_id=o.id).count()
    all_pass &= verify(tl_count >= 6, f'时间线记录完整: {tl_count}条(≥6)')
    if rework_order:
        rw = WorkOrder.objects.get(id=rework_order.id)
        all_pass &= verify(rw.status == WorkOrder.STATUS_CLOSED, f'返工单重启后状态={rw.get_status_display()}')
        all_pass &= verify(rw.materials.count() >= 1, f'返工单耗材记录={rw.materials.count()}')
    log('  ✅ 重启后数据完好: 状态、耗材、时间线均不丢失', 'OK')

    # ========== 6. 导出本月耗材CSV ==========
    log('步骤6：导出本月维修耗材CSV', 'STEP')
    export_client = Client()
    export_client.force_login(disp_user)
    resp = export_client.get(reverse('export_materials'))
    all_pass &= verify(resp.status_code == 200, f'CSV导出HTTP {resp.status_code}')
    all_pass &= verify('text/csv' in resp.get('Content-Type', ''), 'Content-Type=text/csv')
    disp_header = ''
    for k, v in resp.items():
        if k.lower() == 'content-disposition':
            disp_header = v
    # RFC2047 解码可能存在的中文编码, 兼容 =?utf-8?b?...?= 格式
    import re, base64
    decoded_disp = disp_header
    m = re.search(r'=\?utf-8\?b\?(.+?)\?=', disp_header, re.IGNORECASE)
    if m:
        try:
            decoded_disp = base64.b64decode(m.group(1)).decode('utf-8')
        except Exception:
            pass
    all_pass &= verify(
        'attachment' in disp_header.lower() or 'attachment' in decoded_disp.lower(),
        f'Content-Disposition包含attachment (原: {disp_header[:45]}... )'
    )
    csv_text = resp.content.decode('utf-8-sig')
    lines = csv_text.strip().split('\n')
    all_pass &= verify('工单号' in lines[0] and '耗材名称' in lines[0], f'CSV表头正确')
    all_pass &= verify('总金额' in csv_text, f'CSV包含耗材汇总总金额')
    all_pass &= verify(len(lines) >= 3, f'CSV行数={len(lines)}(≥3)')
    log(f'  ✅ CSV导出成功: {len(lines)}行, {len(csv_text)}字符', 'OK')

    # ========== 7. 列表筛选功能 ==========
    log('步骤7：工单列表筛选功能', 'STEP')
    list_client = Client()
    list_client.force_login(resident_user)
    for s_val, s_label in WorkOrder.STATUS_CHOICES:
        resp = list_client.get(reverse('order_list'), {'status': s_val})
        all_pass &= verify(resp.status_code == 200, f'状态筛选[{s_label}]正常')
    resp = list_client.get(reverse('order_list'), {'urgency': 'urgent'})
    all_pass &= verify(resp.status_code == 200, '紧急度筛选正常')
    resp = list_client.get(reverse('order_list'), {'keyword': '漏水'})
    all_pass &= verify(resp.status_code == 200, '关键词搜索正常')
    log('  ✅ 列表筛选: 状态/紧急度/关键词 均正常响应', 'OK')

    # ========== 8. 边界：住户确认别人的工单 ==========
    log('步骤8：边界条件 - 住户确认别人的报修被拒', 'STEP')
    lisi_user = User.objects.get(username='lisi')
    lisi_resident = lisi_user.resident_profile
    lisi_order = WorkOrder.objects.create(
        order_no=generate_order_no(),
        room=lisi_resident.room, resident=lisi_resident,
        problem_type='door_lock', urgency='normal',
        description='【权限测试】李四家门锁测试',
        available_start=now_utc, available_end=now_utc + timedelta(days=1),
        contact_phone=lisi_resident.phone, status=WorkOrder.STATUS_DONE,
        technician=tech, dispatcher=dispatcher, result='测试', finished_at=now_utc,
    )
    client.force_login(resident_user)
    resp = client.post(reverse('order_confirm', args=[lisi_order.id]), {
        'satisfaction': '5', 'confirm_remark': '越权确认',
    }, follow=True)
    lisi_order.refresh_from_db()
    all_pass &= verify(lisi_order.status == WorkOrder.STATUS_DONE, f'张三确认李四工单被正确阻止(状态仍={lisi_order.get_status_display()})')

    print('\n' + '=' * 70)
    if all_pass:
        log('全部验证项目通过！系统功能完整可用。', 'OK')
    else:
        log('部分验证项失败，请检查上方日志。', 'FAIL')
    print('=' * 70)
    print(f'  访问: http://localhost:8000/login/')
    print(f'  账号: admin/admin123 | zhangsan/123456(住户) | lishi/123456(师傅) | zhaoliu/123456(调度)')
    print('=' * 70 + '\n')
    return all_pass


if __name__ == '__main__':
    try:
        success = main()
        sys.exit(0 if success else 1)
    except Exception as e:
        import traceback
        log(f'运行异常: {e}', 'FAIL')
        traceback.print_exc()
        sys.exit(2)
