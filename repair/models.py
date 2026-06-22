from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
from datetime import timedelta


class Room(models.Model):
    building = models.CharField('楼栋', max_length=20)
    unit = models.CharField('单元', max_length=10, blank=True, default='')
    room_number = models.CharField('房号', max_length=20)
    is_active = models.BooleanField('有效', default=True)

    class Meta:
        verbose_name = '房号'
        verbose_name_plural = '房号'
        unique_together = ('building', 'unit', 'room_number')

    def __str__(self):
        if self.unit:
            return f'{self.building}栋{self.unit}单元{self.room_number}室'
        return f'{self.building}栋{self.room_number}室'


class Resident(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='resident_profile')
    phone = models.CharField('电话', max_length=20)
    room = models.ForeignKey(Room, on_delete=models.CASCADE, verbose_name='房号')
    name = models.CharField('姓名', max_length=50)

    class Meta:
        verbose_name = '住户'
        verbose_name_plural = '住户'

    def __str__(self):
        return f'{self.name}({self.room})'


class Technician(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='technician_profile')
    phone = models.CharField('电话', max_length=20)
    name = models.CharField('姓名', max_length=50)
    specialty = models.CharField('专长', max_length=100, blank=True, default='')
    is_active = models.BooleanField('在职', default=True)

    class Meta:
        verbose_name = '维修师傅'
        verbose_name_plural = '维修师傅'

    def __str__(self):
        return f'{self.name}(师傅)'

    def has_conflict(self, scheduled_start, scheduled_end, exclude_order_id=None):
        qs = WorkOrder.objects.filter(
            technician=self,
            scheduled_start__isnull=False,
            status__in=[WorkOrder.STATUS_ASSIGNED, WorkOrder.STATUS_IN_PROGRESS]
        )
        if exclude_order_id:
            qs = qs.exclude(id=exclude_order_id)
        for order in qs:
            if (scheduled_start < order.scheduled_end and
                    scheduled_end > order.scheduled_start):
                return True, order
        return False, None


class Dispatcher(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='dispatcher_profile')
    name = models.CharField('姓名', max_length=50)

    class Meta:
        verbose_name = '调度员'
        verbose_name_plural = '调度员'

    def __str__(self):
        return f'{self.name}(调度)'


class Material(models.Model):
    name = models.CharField('耗材名称', max_length=100, unique=True)
    unit = models.CharField('单位', max_length=20, default='个')
    price = models.DecimalField('单价', max_digits=10, decimal_places=2, default=0)

    class Meta:
        verbose_name = '耗材'
        verbose_name_plural = '耗材'

    def __str__(self):
        return f'{self.name}({self.unit})'


class WorkOrder(models.Model):
    STATUS_PENDING = 'pending'
    STATUS_ASSIGNED = 'assigned'
    STATUS_IN_PROGRESS = 'in_progress'
    STATUS_DONE = 'done'
    STATUS_CONFIRMED = 'confirmed'
    STATUS_REWORK = 'rework'
    STATUS_CLOSED = 'closed'

    STATUS_CHOICES = [
        (STATUS_PENDING, '待派工'),
        (STATUS_ASSIGNED, '已派工'),
        (STATUS_IN_PROGRESS, '处理中'),
        (STATUS_DONE, '待确认'),
        (STATUS_CONFIRMED, '已确认'),
        (STATUS_REWORK, '返工'),
        (STATUS_CLOSED, '已关闭'),
    ]

    URGENCY_LOW = 'low'
    URGENCY_NORMAL = 'normal'
    URGENCY_HIGH = 'high'
    URGENCY_URGENT = 'urgent'

    URGENCY_CHOICES = [
        (URGENCY_LOW, '低'),
        (URGENCY_NORMAL, '一般'),
        (URGENCY_HIGH, '紧急'),
        (URGENCY_URGENT, '特急'),
    ]

    URGENCY_ORDER = {
        URGENCY_URGENT: 0,
        URGENCY_HIGH: 1,
        URGENCY_NORMAL: 2,
        URGENCY_LOW: 3,
    }

    PROBLEM_TYPES = [
        ('electric', '水电维修'),
        ('plumbing', '管道疏通'),
        ('appliance', '家电维修'),
        ('door_lock', '门窗锁具'),
        ('structure', '土建装修'),
        ('elevator', '电梯故障'),
        ('other', '其他'),
    ]

    order_no = models.CharField('工单号', max_length=30, unique=True)
    room = models.ForeignKey(Room, on_delete=models.PROTECT, verbose_name='房号')
    resident = models.ForeignKey(Resident, on_delete=models.PROTECT, verbose_name='报修住户')
    problem_type = models.CharField('问题类型', max_length=30, choices=PROBLEM_TYPES)
    urgency = models.CharField('紧急程度', max_length=20, choices=URGENCY_CHOICES, default=URGENCY_NORMAL)
    description = models.TextField('问题描述')
    available_start = models.DateTimeField('可上门起始时间')
    available_end = models.DateTimeField('可上门结束时间')
    contact_phone = models.CharField('联系电话', max_length=20)

    status = models.CharField('状态', max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    technician = models.ForeignKey(Technician, on_delete=models.PROTECT, null=True, blank=True, verbose_name='维修师傅')
    dispatcher = models.ForeignKey(Dispatcher, on_delete=models.PROTECT, null=True, blank=True, verbose_name='调度员')
    scheduled_start = models.DateTimeField('预约上门时间', null=True, blank=True)
    scheduled_end = models.DateTimeField('预约结束时间', null=True, blank=True)

    arrived_at = models.DateTimeField('到场时间', null=True, blank=True)
    finished_at = models.DateTimeField('处理完成时间', null=True, blank=True)
    result = models.TextField('处理结果', blank=True, default='')
    photo_placeholder = models.TextField('照片说明占位', blank=True, default='')

    confirmed_at = models.DateTimeField('确认时间', null=True, blank=True)
    confirmed_by = models.ForeignKey(User, on_delete=models.PROTECT, null=True, blank=True, related_name='confirmed_orders', verbose_name='确认人')
    satisfaction = models.IntegerField('满意度(1-5)', null=True, blank=True)
    confirm_remark = models.TextField('确认备注', blank=True, default='')

    rework_reason = models.TextField('返工原因', blank=True, default='')
    rework_count = models.IntegerField('返工次数', default=0)

    created_at = models.DateTimeField('创建时间', default=timezone.now)
    updated_at = models.DateTimeField('更新时间', auto_now=True)
    is_rework = models.BooleanField('是否返工单', default=False)
    parent_order = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True, related_name='rework_orders', verbose_name='原工单')

    class Meta:
        verbose_name = '工单'
        verbose_name_plural = '工单'
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.order_no}-{self.get_status_display()}'

    @property
    def urgency_rank(self):
        return self.URGENCY_ORDER.get(self.urgency, 99)

    def can_assign(self):
        return self.status in [self.STATUS_PENDING, self.STATUS_REWORK]

    def can_start(self, user):
        if self.status != self.STATUS_ASSIGNED:
            return False
        if not self.technician:
            return False
        try:
            return self.technician.user_id == user.id
        except:
            return False

    def can_finish(self, user):
        if self.status != self.STATUS_IN_PROGRESS:
            return False
        if not self.technician:
            return False
        try:
            return self.technician.user_id == user.id
        except:
            return False

    def can_confirm(self, user):
        if self.status != self.STATUS_DONE:
            return False
        return self.resident.user_id == user.id

    def can_rework(self, user):
        if self.status not in [self.STATUS_DONE, self.STATUS_CLOSED]:
            return False
        if self.status == self.STATUS_CLOSED and self.rework_count >= 3:
            return False
        return self.resident.user_id == user.id

    def can_close(self):
        return self.status == self.STATUS_CONFIRMED

    def status_badge_class(self):
        return {
            self.STATUS_PENDING: 'bg-warning',
            self.STATUS_ASSIGNED: 'bg-info',
            self.STATUS_IN_PROGRESS: 'bg-primary',
            self.STATUS_DONE: 'bg-secondary',
            self.STATUS_CONFIRMED: 'bg-success',
            self.STATUS_REWORK: 'bg-danger',
            self.STATUS_CLOSED: 'bg-dark',
        }.get(self.status, 'bg-light')

    def urgency_badge_class(self):
        return {
            self.URGENCY_LOW: 'bg-secondary',
            self.URGENCY_NORMAL: 'bg-info',
            self.URGENCY_HIGH: 'bg-warning text-dark',
            self.URGENCY_URGENT: 'bg-danger',
        }.get(self.urgency, 'bg-light')


class OrderMaterial(models.Model):
    order = models.ForeignKey(WorkOrder, on_delete=models.CASCADE, related_name='materials', verbose_name='工单')
    material = models.ForeignKey(Material, on_delete=models.PROTECT, verbose_name='耗材')
    quantity = models.DecimalField('用量', max_digits=10, decimal_places=2)
    unit_price = models.DecimalField('单价', max_digits=10, decimal_places=2, default=0)
    remark = models.CharField('备注', max_length=200, blank=True, default='')

    class Meta:
        verbose_name = '工单耗材'
        verbose_name_plural = '工单耗材'

    def __str__(self):
        return f'{self.order.order_no}-{self.material.name}x{self.quantity}'

    @property
    def subtotal(self):
        return self.quantity * self.unit_price


TIMELINE_CREATED = 'created'
TIMELINE_ASSIGNED = 'assigned'
TIMELINE_UNASSIGNED = 'unassigned'
TIMELINE_ARRIVED = 'arrived'
TIMELINE_FINISHED = 'finished'
TIMELINE_CONFIRMED = 'confirmed'
TIMELINE_REWORK_REQUEST = 'rework_request'
TIMELINE_REWORK_CREATED = 'rework_created'
TIMELINE_CLOSED = 'closed'
TIMELINE_NOTE = 'note'

TIMELINE_TYPES = [
    (TIMELINE_CREATED, '工单创建'),
    (TIMELINE_ASSIGNED, '派工'),
    (TIMELINE_UNASSIGNED, '取消派工'),
    (TIMELINE_ARRIVED, '师傅到场'),
    (TIMELINE_FINISHED, '处理完成'),
    (TIMELINE_CONFIRMED, '住户确认'),
    (TIMELINE_REWORK_REQUEST, '申请返工'),
    (TIMELINE_REWORK_CREATED, '返工单创建'),
    (TIMELINE_CLOSED, '工单关闭'),
    (TIMELINE_NOTE, '备注'),
]


class Timeline(models.Model):
    order = models.ForeignKey(WorkOrder, on_delete=models.CASCADE, related_name='timelines', verbose_name='工单')
    event_type = models.CharField('事件类型', max_length=30, choices=TIMELINE_TYPES)
    operator = models.ForeignKey(User, on_delete=models.PROTECT, null=True, blank=True, verbose_name='操作人')
    operator_name = models.CharField('操作人名称', max_length=100, blank=True, default='')
    content = models.TextField('事件内容', blank=True, default='')
    created_at = models.DateTimeField('时间', default=timezone.now)

    class Meta:
        verbose_name = '时间线'
        verbose_name_plural = '时间线'
        ordering = ['created_at', 'id']

    def __str__(self):
        return f'{self.order.order_no}-{self.get_event_type_display()}'

    def icon_class(self):
        return {
            TIMELINE_CREATED: 'bi-plus-circle text-success',
            TIMELINE_ASSIGNED: 'bi-person-check text-primary',
            TIMELINE_UNASSIGNED: 'bi-person-x text-warning',
            TIMELINE_ARRIVED: 'bi-geo-alt text-info',
            TIMELINE_FINISHED: 'bi-check-circle text-success',
            TIMELINE_CONFIRMED: 'bi-hand-thumbs-up text-success',
            TIMELINE_REWORK_REQUEST: 'bi-arrow-repeat text-danger',
            TIMELINE_REWORK_CREATED: 'bi-file-earmark-plus text-danger',
            TIMELINE_CLOSED: 'bi-x-circle text-secondary',
            TIMELINE_NOTE: 'bi-chat-left text-muted',
        }.get(self.event_type, 'bi-dot text-muted')


def add_timeline(order, event_type, operator=None, content=''):
    op_name = ''
    if operator:
        op_name = getattr(operator, 'get_full_name', lambda: operator.username)()
        if not op_name:
            op_name = operator.username
        if hasattr(operator, 'resident_profile'):
            op_name = f'{operator.resident_profile.name}(住户)'
        elif hasattr(operator, 'technician_profile'):
            op_name = f'{operator.technician_profile.name}(师傅)'
        elif hasattr(operator, 'dispatcher_profile'):
            op_name = f'{operator.dispatcher_profile.name}(调度)'
    return Timeline.objects.create(
        order=order,
        event_type=event_type,
        operator=operator,
        operator_name=op_name,
        content=content,
    )


def generate_order_no():
    now = timezone.now()
    prefix = now.strftime('%Y%m%d%H%M')
    count = WorkOrder.objects.filter(created_at__year=now.year, created_at__month=now.month, created_at__day=now.day).count() + 1
    return f'GD{prefix}{count:04d}'
