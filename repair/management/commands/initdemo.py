from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from django.db import transaction
from repair.models import (
    Room, Resident, Technician, Dispatcher, Material,
    WorkOrder, OrderMaterial, TimeoutConfig, generate_order_no, add_timeline,
    TIMELINE_CREATED,
)
from django.utils import timezone
from datetime import timedelta


class Command(BaseCommand):
    help = '初始化测试数据：房号、用户(住户/师傅/调度/管理员)、耗材'

    @transaction.atomic
    def handle(self, *args, **options):
        self.stdout.write('开始初始化数据...')

        rooms_data = [
            ('1', '1', '101'), ('1', '1', '102'), ('1', '1', '201'), ('1', '1', '202'),
            ('1', '2', '101'), ('1', '2', '201'),
            ('2', '1', '101'), ('2', '1', '102'), ('2', '1', '301'),
            ('3', '', '101'), ('3', '', '102'),
        ]
        rooms = []
        for b, u, r in rooms_data:
            room, _ = Room.objects.get_or_create(building=b, unit=u, room_number=r, defaults={'is_active': True})
            rooms.append(room)
        self.stdout.write(f'  房号: {len(rooms)} 条')

        users_info = [
            ('admin', 'admin123', 'admin', None, '管理员'),
            ('zhangsan', '123456', 'resident', rooms[0], '张三'),
            ('lisi', '123456', 'resident', rooms[2], '李四'),
            ('wangshuai', '123456', 'resident', rooms[7], '王帅'),
            ('zhaoxiaoming', '123456', 'resident', rooms[10], '赵小明'),
            ('lishi', '123456', 'technician', None, '李师傅'),
            ('wangwu', '123456', 'technician', None, '王师傅'),
            ('chenliu', '123456', 'technician', None, '陈师傅'),
            ('zhaoliu', '123456', 'dispatcher', None, '赵调度'),
        ]

        for username, pwd, role, room, name in users_info:
            user, created = User.objects.get_or_create(username=username, defaults={
                'is_active': True, 'first_name': name,
            })
            if created:
                user.set_password(pwd)
                user.save()
            if role == 'admin' and not user.is_superuser:
                user.is_superuser = True
                user.is_staff = True
                user.save()
            if role == 'resident':
                Resident.objects.get_or_create(user=user, defaults={
                    'room': room, 'name': name, 'phone': '1380000%04d' % (1000 + len(Resident.objects.all())),
                })
            elif role == 'technician':
                Technician.objects.get_or_create(user=user, defaults={
                    'name': name, 'phone': '1390000%04d' % (2000 + len(Technician.objects.filter(name=name))),
                    'specialty': '水电维修、家电维修' if username == 'lishi' else ('管道疏通、土建装修' if username == 'wangwu' else '综合维修'),
                })
            elif role == 'dispatcher':
                Dispatcher.objects.get_or_create(user=user, defaults={'name': name})

        self.stdout.write('  用户和角色创建完成')

        materials_data = [
            ('PPR水管（米）', '米', 15.0),
            ('生料带', '卷', 3.0),
            ('水龙头(普通)', '个', 45.0),
            ('水龙头(品牌)', '个', 120.0),
            ('三角阀', '个', 25.0),
            ('电胶布', '卷', 5.0),
            ('节能灯(15W)', '个', 20.0),
            ('LED灯(30W)', '个', 65.0),
            ('开关插座', '个', 30.0),
            ('空气开关', '个', 80.0),
            ('玻璃胶', '支', 18.0),
            ('膨胀螺丝', '套', 2.5),
            ('疏通剂', '瓶', 35.0),
            ('密封条', '米', 10.0),
            ('门锁芯', '个', 150.0),
        ]
        for name, unit, price in materials_data:
            Material.objects.get_or_create(name=name, defaults={'unit': unit, 'price': price})
        self.stdout.write(f'  耗材: {len(materials_data)} 种')

        timeout_configs = [
            ('urgent', 15, 30),
            ('high', 30, 60),
            ('normal', 60, 120),
            ('low', 120, 240),
        ]
        for urgency, assign_min, arrive_min in timeout_configs:
            TimeoutConfig.objects.get_or_create(
                urgency=urgency,
                defaults={'assign_timeout_minutes': assign_min, 'arrive_timeout_minutes': arrive_min}
            )
        self.stdout.write(f'  超时配置: {len(timeout_configs)} 条')

        now = timezone.now()
        resident1 = Resident.objects.filter(name='张三').first()
        resident2 = Resident.objects.filter(name='李四').first()
        tech1 = Technician.objects.filter(name='李师傅').first()
        tech2 = Technician.objects.filter(name='王师傅').first()
        dispatcher = Dispatcher.objects.filter(name='赵调度').first()

        demo_orders = []
        if not WorkOrder.objects.exists():
            d1 = WorkOrder.objects.create(
                order_no=generate_order_no(),
                room=resident1.room, resident=resident1,
                problem_type='plumbing', urgency='high',
                description='厨房水龙头漏水严重，无法关闭。需要尽快处理，否则会淹到楼下。',
                available_start=now + timedelta(hours=1),
                available_end=now + timedelta(hours=6),
                contact_phone=resident1.phone,
                status='done', technician=tech1, dispatcher=dispatcher,
                scheduled_start=now + timedelta(hours=2),
                scheduled_end=now + timedelta(hours=4),
                arrived_at=now + timedelta(hours=2, minutes=5),
                finished_at=now + timedelta(hours=3, minutes=40),
                result='已更换厨房水龙头和三角阀，检查供水管路无渗漏，试水正常。',
                photo_placeholder='1. 旧水龙头拆卸前拍照 2. 新水龙头安装后 3. 底部管路接口密封 4. 试水无渗漏',
            )
            mats = Material.objects.filter(name__in=['水龙头(品牌)', '三角阀', '生料带'])
            qtys = [1, 2, 1]
            for m, q in zip(mats, qtys):
                OrderMaterial.objects.create(order=d1, material=m, quantity=q, unit_price=m.price)
            add_timeline(d1, TIMELINE_CREATED, resident1.user, f'住户提交报修')
            demo_orders.append(d1)

            d2 = WorkOrder.objects.create(
                order_no=generate_order_no(),
                room=resident2.room, resident=resident2,
                problem_type='electric', urgency='normal',
                description='客厅吊灯其中两盏灯不亮，可能是镇流器故障。',
                available_start=now + timedelta(hours=3),
                available_end=now + timedelta(hours=10),
                contact_phone=resident2.phone,
                status='in_progress', technician=tech2, dispatcher=dispatcher,
                scheduled_start=now + timedelta(hours=4),
                scheduled_end=now + timedelta(hours=6),
                arrived_at=now + timedelta(hours=4, minutes=10),
            )
            add_timeline(d2, TIMELINE_CREATED, resident2.user, f'住户提交报修')
            demo_orders.append(d2)

            d3 = WorkOrder.objects.create(
                order_no=generate_order_no(),
                room=resident1.room, resident=resident1,
                problem_type='door_lock', urgency='urgent',
                description='入户门锁卡死，钥匙无法转动，现无法出门！紧急求助！',
                available_start=now,
                available_end=now + timedelta(hours=2),
                contact_phone=resident1.phone,
                status='pending',
            )
            add_timeline(d3, TIMELINE_CREATED, resident1.user, f'住户提交报修')
            demo_orders.append(d3)

            d4 = WorkOrder.objects.create(
                order_no=generate_order_no(),
                room=resident2.room, resident=resident2,
                problem_type='appliance', urgency='low',
                description='空调制冷效果差，已使用多年，建议添加冷媒或深度清洗。',
                available_start=now + timedelta(days=1),
                available_end=now + timedelta(days=2),
                contact_phone=resident2.phone,
                status='closed', technician=tech1, dispatcher=dispatcher,
                scheduled_start=now - timedelta(days=2) + timedelta(hours=5),
                scheduled_end=now - timedelta(days=2) + timedelta(hours=7),
                arrived_at=now - timedelta(days=2) + timedelta(hours=5, minutes=15),
                finished_at=now - timedelta(days=2) + timedelta(hours=6, minutes=50),
                confirmed_at=now - timedelta(days=1) + timedelta(hours=12),
                satisfaction=5,
                confirm_remark='师傅很专业，加了冷媒后空调效果明显改善，点赞！',
                result='已完成空调冷媒补充及滤网清洗，出风温度测试正常，制冷效果良好。',
                photo_placeholder='1. 空调外观拍照 2. 冷媒压力表读数 3. 清洗前滤网 4. 清洗后滤网',
                is_rework=False,
            )
            mats = Material.objects.filter(name__in=[])
            add_timeline(d4, TIMELINE_CREATED, resident2.user, f'住户提交报修')
            demo_orders.append(d4)
            self.stdout.write(f'  示例工单: {len(demo_orders)} 条')

        self.stdout.write(self.style.SUCCESS('初始化完成！'))
        self.stdout.write('  登录账号：')
        self.stdout.write('    管理员  : admin / admin123')
        self.stdout.write('    住户    : zhangsan / 123456 (张三)  |  lisi / 123456 (李四)')
        self.stdout.write('    维修师傅: lishi / 123456 (李师傅)  |  wangwu / 123456 (王师傅)')
        self.stdout.write('    调度员  : zhaoliu / 123456')
