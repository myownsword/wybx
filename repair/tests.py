from django.test import TestCase, Client
from django.contrib.auth.models import User
from django.urls import reverse
from django.utils import timezone
from datetime import timedelta
from decimal import Decimal

from .models import (
    Room, Resident, Technician, Dispatcher, Material,
    WorkOrder, RescheduleRequest, TechnicianFlag, TimeoutConfig,
    generate_order_no, add_timeline,
    TIMELINE_RESCHEDULE_REQUEST, TIMELINE_RESCHEDULE_APPROVED,
    TIMELINE_RESCHEDULE_REJECTED, TIMELINE_RESCHEDULE_REASSIGNED,
    TIMELINE_TECHNICIAN_FLAG, TIMELINE_TIMEOUT,
)


class RescheduleTestCase(TestCase):
    def setUp(self):
        self.room1 = Room.objects.create(building='1', unit='1', room_number='101')
        self.room2 = Room.objects.create(building='1', unit='1', room_number='102')

        self.user1 = User.objects.create_user('zhangsan', password='123456', first_name='张三')
        self.user2 = User.objects.create_user('lisi', password='123456', first_name='李四')
        self.user_tech = User.objects.create_user('lishi', password='123456', first_name='李师傅')
        self.user_tech2 = User.objects.create_user('wangwu', password='123456', first_name='王师傅')
        self.user_disp = User.objects.create_user('zhaoliu', password='123456', first_name='赵调度')

        self.resident1 = Resident.objects.create(user=self.user1, room=self.room1, name='张三', phone='13800001111')
        self.resident2 = Resident.objects.create(user=self.user2, room=self.room2, name='李四', phone='13800002222')
        self.tech = Technician.objects.create(user=self.user_tech, name='李师傅', phone='13900001111', specialty='水电维修')
        self.tech2 = Technician.objects.create(user=self.user_tech2, name='王师傅', phone='13900002222', specialty='管道疏通')
        self.dispatcher = Dispatcher.objects.create(user=self.user_disp, name='赵调度')

        TimeoutConfig.objects.get_or_create(urgency='urgent', defaults={'assign_timeout_minutes': 15, 'arrive_timeout_minutes': 30})
        TimeoutConfig.objects.get_or_create(urgency='high', defaults={'assign_timeout_minutes': 30, 'arrive_timeout_minutes': 60})
        TimeoutConfig.objects.get_or_create(urgency='normal', defaults={'assign_timeout_minutes': 60, 'arrive_timeout_minutes': 120})
        TimeoutConfig.objects.get_or_create(urgency='low', defaults={'assign_timeout_minutes': 120, 'arrive_timeout_minutes': 240})

        self.now = timezone.now()

    def _fmt_dt(self, dt):
        return dt.astimezone().strftime('%Y-%m-%dT%H:%M')

    def _create_order(self, status=WorkOrder.STATUS_PENDING, tech=None, scheduled_start=None, scheduled_end=None):
        order = WorkOrder.objects.create(
            order_no=generate_order_no(),
            room=self.room1,
            resident=self.resident1,
            problem_type='plumbing',
            urgency='high',
            description='测试工单',
            available_start=self.now + timedelta(hours=2),
            available_end=self.now + timedelta(hours=8),
            contact_phone='13800001111',
            status=status,
            technician=tech,
            dispatcher=self.dispatcher if tech else None,
            scheduled_start=scheduled_start,
            scheduled_end=scheduled_end,
        )
        return order

    def test_normal_reschedule_flow(self):
        order = self._create_order()

        client = Client()
        client.force_login(self.user1)

        new_start = self.now + timedelta(days=1, hours=2)
        new_end = self.now + timedelta(days=1, hours=8)

        resp = client.post(reverse('order_reschedule_request', args=[order.id]), {
            'reason': '临时有事需要改期',
            'new_available_start': self._fmt_dt(new_start),
            'new_available_end': self._fmt_dt(new_end),
        }, follow=True)
        self.assertEqual(resp.status_code, 200)

        order.refresh_from_db()
        self.assertEqual(order.reschedule_requests.count(), 1)
        req = order.reschedule_requests.first()
        self.assertEqual(req.status, RescheduleRequest.STATUS_PENDING)
        self.assertEqual(req.reason, '临时有事需要改期')
        self.assertTrue(order.timelines.filter(event_type=TIMELINE_RESCHEDULE_REQUEST).exists())

        disp_client = Client()
        disp_client.force_login(self.user_disp)

        resp = disp_client.post(reverse('order_reschedule_approve', args=[order.id, req.id]), {
            'review_note': '同意改期',
        }, follow=True)
        self.assertEqual(resp.status_code, 200)

        req.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual(req.status, RescheduleRequest.STATUS_APPROVED)
        self.assertEqual(order.status, WorkOrder.STATUS_PENDING)
        self.assertIsNone(order.technician)
        self.assertIsNone(order.scheduled_start)
        self.assertEqual(order.available_start.replace(second=0, microsecond=0),
                        new_start.replace(second=0, microsecond=0))
        self.assertTrue(order.timelines.filter(event_type=TIMELINE_RESCHEDULE_APPROVED).exists())

    def test_reschedule_with_reassignment(self):
        sch_start = self.now + timedelta(hours=3)
        sch_end = self.now + timedelta(hours=5)
        order = self._create_order(status=WorkOrder.STATUS_ASSIGNED, tech=self.tech,
                                   scheduled_start=sch_start, scheduled_end=sch_end)

        client = Client()
        client.force_login(self.user1)

        new_start = self.now + timedelta(days=1, hours=2)
        new_end = self.now + timedelta(days=1, hours=8)

        client.post(reverse('order_reschedule_request', args=[order.id]), {
            'reason': '需要改期并改派其他师傅',
            'new_available_start': self._fmt_dt(new_start),
            'new_available_end': self._fmt_dt(new_end),
        }, follow=True)

        req = order.reschedule_requests.first()
        disp_client = Client()
        disp_client.force_login(self.user_disp)

        new_sch_start = self.now + timedelta(days=1, hours=3)
        new_sch_end = self.now + timedelta(days=1, hours=5)

        resp = disp_client.post(reverse('order_reschedule_approve', args=[order.id, req.id]), {
            'review_note': '同意改期并改派',
            'new_technician': self.tech2.id,
            'new_scheduled_start': self._fmt_dt(new_sch_start),
            'new_scheduled_end': self._fmt_dt(new_sch_end),
        }, follow=True)
        self.assertEqual(resp.status_code, 200)

        req.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual(req.status, RescheduleRequest.STATUS_REASSIGNED)
        self.assertEqual(req.new_technician_id, self.tech2.id)
        self.assertEqual(order.status, WorkOrder.STATUS_ASSIGNED)
        self.assertEqual(order.technician_id, self.tech2.id)
        self.assertEqual(order.scheduled_start.replace(second=0, microsecond=0),
                        new_sch_start.replace(second=0, microsecond=0))
        self.assertTrue(order.timelines.filter(event_type=TIMELINE_RESCHEDULE_REASSIGNED).exists())
        self.assertTrue(RescheduleRequest.objects.filter(
            order=order,
            status=RescheduleRequest.STATUS_REASSIGNED,
            new_technician=self.tech2,
        ).exists())

    def test_reschedule_conflict_fails(self):
        sch_start = self.now + timedelta(days=2, hours=3)
        sch_end = self.now + timedelta(days=2, hours=5)
        existing_order = self._create_order(status=WorkOrder.STATUS_ASSIGNED, tech=self.tech,
                                            scheduled_start=sch_start, scheduled_end=sch_end)

        order = self._create_order()

        client = Client()
        client.force_login(self.user1)

        new_start = self.now + timedelta(days=2, hours=2)
        new_end = self.now + timedelta(days=2, hours=8)

        client.post(reverse('order_reschedule_request', args=[order.id]), {
            'reason': '改期申请',
            'new_available_start': self._fmt_dt(new_start),
            'new_available_end': self._fmt_dt(new_end),
        }, follow=True)

        req = order.reschedule_requests.first()
        disp_client = Client()
        disp_client.force_login(self.user_disp)

        resp = disp_client.post(reverse('order_reschedule_approve', args=[order.id, req.id]), {
            'review_note': '同意改期',
            'new_technician': self.tech.id,
            'new_scheduled_start': self._fmt_dt(sch_start),
            'new_scheduled_end': self._fmt_dt(sch_end),
        })

        req.refresh_from_db()
        self.assertEqual(req.status, RescheduleRequest.STATUS_PENDING)
        self.assertNotEqual(order.status, WorkOrder.STATUS_ASSIGNED)

    def test_unauthorized_reschedule_fails(self):
        order = self._create_order()

        client = Client()
        client.force_login(self.user2)

        new_start = self.now + timedelta(days=1, hours=2)
        new_end = self.now + timedelta(days=1, hours=8)

        resp = client.post(reverse('order_reschedule_request', args=[order.id]), {
            'reason': '越权申请改期',
            'new_available_start': self._fmt_dt(new_start),
            'new_available_end': self._fmt_dt(new_end),
        }, follow=True)
        self.assertEqual(order.reschedule_requests.count(), 0)

    def test_status_restricted_reschedule_fails(self):
        for status in [WorkOrder.STATUS_IN_PROGRESS, WorkOrder.STATUS_DONE,
                       WorkOrder.STATUS_CONFIRMED, WorkOrder.STATUS_CLOSED]:
            order = self._create_order(status=status)
            client = Client()
            client.force_login(self.user1)

            new_start = self.now + timedelta(days=1, hours=2)
            new_end = self.now + timedelta(days=1, hours=8)

            resp = client.post(reverse('order_reschedule_request', args=[order.id]), {
                'reason': f'测试{status}状态改期',
                'new_available_start': self._fmt_dt(new_start),
                'new_available_end': self._fmt_dt(new_end),
            }, follow=True)
            self.assertEqual(order.reschedule_requests.count(), 0,
                           f'{status} 状态的工单应该不允许改期')

    def test_technician_flag(self):
        sch_start = self.now + timedelta(hours=3)
        sch_end = self.now + timedelta(hours=5)
        order = self._create_order(status=WorkOrder.STATUS_ASSIGNED, tech=self.tech,
                                   scheduled_start=sch_start, scheduled_end=sch_end)

        client = Client()
        client.force_login(self.user_tech)

        resp = client.post(reverse('order_technician_flag', args=[order.id]), {
            'flag_type': 'cannot_contact',
            'suggestion': '电话无人接听，建议改期或联系物业协助',
        }, follow=True)
        self.assertEqual(resp.status_code, 200)

        order.refresh_from_db()
        self.assertEqual(order.technician_flags.count(), 1)
        flag = order.technician_flags.first()
        self.assertEqual(flag.flag_type, 'cannot_contact')
        self.assertEqual(flag.suggestion, '电话无人接听，建议改期或联系物业协助')
        self.assertTrue(order.timelines.filter(event_type=TIMELINE_TECHNICIAN_FLAG).exists())

    def test_timeout_persistence(self):
        order = WorkOrder.objects.create(
            order_no=generate_order_no(),
            room=self.room1,
            resident=self.resident1,
            problem_type='electric',
            urgency='urgent',
            description='超时测试工单',
            available_start=self.now + timedelta(hours=1),
            available_end=self.now + timedelta(hours=4),
            contact_phone='13800001111',
            status=WorkOrder.STATUS_PENDING,
            created_at=self.now - timedelta(minutes=30),
        )

        from repair.views import check_and_mark_timeouts
        check_and_mark_timeouts()

        order.refresh_from_db()
        self.assertTrue(order.is_timeout)
        self.assertEqual(order.timeout_type, WorkOrder.TIMEOUT_TYPE_ASSIGN)
        self.assertIsNotNone(order.timeout_at)
        self.assertTrue(order.timelines.filter(event_type=TIMELINE_TIMEOUT).exists())

        order2_id = order.id
        order = WorkOrder.objects.get(id=order2_id)
        self.assertTrue(order.is_timeout)
        self.assertEqual(order.timeout_type, WorkOrder.TIMEOUT_TYPE_ASSIGN)

    def test_timeout_arrive(self):
        sch_start = self.now - timedelta(hours=2)
        sch_end = self.now - timedelta(hours=1)
        order = WorkOrder.objects.create(
            order_no=generate_order_no(),
            room=self.room1,
            resident=self.resident1,
            problem_type='electric',
            urgency='high',
            description='到场超时测试工单',
            available_start=self.now - timedelta(hours=3),
            available_end=self.now + timedelta(hours=1),
            contact_phone='13800001111',
            status=WorkOrder.STATUS_ASSIGNED,
            technician=self.tech,
            dispatcher=self.dispatcher,
            scheduled_start=sch_start,
            scheduled_end=sch_end,
        )

        from repair.views import check_and_mark_timeouts
        check_and_mark_timeouts()

        order.refresh_from_db()
        self.assertTrue(order.is_timeout)
        self.assertEqual(order.timeout_type, WorkOrder.TIMEOUT_TYPE_ARRIVE)

    def test_reschedule_reject(self):
        order = self._create_order()

        client = Client()
        client.force_login(self.user1)

        new_start = self.now + timedelta(days=1, hours=2)
        new_end = self.now + timedelta(days=1, hours=8)

        client.post(reverse('order_reschedule_request', args=[order.id]), {
            'reason': '改期申请',
            'new_available_start': self._fmt_dt(new_start),
            'new_available_end': self._fmt_dt(new_end),
        }, follow=True)

        req = order.reschedule_requests.first()
        disp_client = Client()
        disp_client.force_login(self.user_disp)

        resp = disp_client.post(reverse('order_reschedule_reject', args=[order.id, req.id]), {
            'review_note': '改期理由不充分，请重新申请',
        }, follow=True)
        self.assertEqual(resp.status_code, 200)

        req.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual(req.status, RescheduleRequest.STATUS_REJECTED)
        self.assertTrue(order.timelines.filter(event_type=TIMELINE_RESCHEDULE_REJECTED).exists())

    def test_timeout_list_filter(self):
        order1 = self._create_order()
        order1.is_timeout = True
        order1.timeout_type = WorkOrder.TIMEOUT_TYPE_ASSIGN
        order1.save()

        order2 = self._create_order()

        client = Client()
        client.force_login(self.user_disp)

        resp = client.get(reverse('order_list'), {'timeout': '1'})
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode('utf-8')
        self.assertIn(order1.order_no, content)
        self.assertNotIn(order2.order_no, content)

    def test_export_timeout_csv(self):
        order = self._create_order()
        order.is_timeout = True
        order.timeout_type = WorkOrder.TIMEOUT_TYPE_ASSIGN
        order.timeout_at = self.now
        order.save()

        client = Client()
        client.force_login(self.user_disp)

        resp = client.get(reverse('export_timeout'), {'year': self.now.year, 'month': self.now.month})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get('Content-Type'), 'text/csv; charset=utf-8-sig')
        content = resp.content.decode('utf-8-sig')
        self.assertIn(order.order_no, content)
        self.assertIn('超时', content)

    def test_original_record_preserved_after_reassignment(self):
        sch_start = self.now + timedelta(hours=3)
        sch_end = self.now + timedelta(hours=5)
        order = self._create_order(status=WorkOrder.STATUS_ASSIGNED, tech=self.tech,
                                   scheduled_start=sch_start, scheduled_end=sch_end)
        original_tech_id = self.tech.id
        original_sch_start = sch_start

        client = Client()
        client.force_login(self.user1)

        new_start = self.now + timedelta(days=1, hours=2)
        new_end = self.now + timedelta(days=1, hours=8)

        client.post(reverse('order_reschedule_request', args=[order.id]), {
            'reason': '改期改派',
            'new_available_start': self._fmt_dt(new_start),
            'new_available_end': self._fmt_dt(new_end),
        }, follow=True)

        req = order.reschedule_requests.first()
        disp_client = Client()
        disp_client.force_login(self.user_disp)

        new_sch_start = self.now + timedelta(days=1, hours=3)
        new_sch_end = self.now + timedelta(days=1, hours=5)

        disp_client.post(reverse('order_reschedule_approve', args=[order.id, req.id]), {
            'review_note': '同意改派',
            'new_technician': self.tech2.id,
            'new_scheduled_start': self._fmt_dt(new_sch_start),
            'new_scheduled_end': self._fmt_dt(new_sch_end),
        }, follow=True)

        req.refresh_from_db()
        self.assertEqual(req.status, RescheduleRequest.STATUS_REASSIGNED)
        self.assertIsNotNone(req.new_technician)
        self.assertIsNotNone(req.new_scheduled_start)

        timeline_content = order.timelines.filter(event_type=TIMELINE_RESCHEDULE_REASSIGNED).first().content
        self.assertIn(str(original_tech_id), timeline_content)
        self.assertIn(self.tech.name, timeline_content)
