from django import forms
from .models import WorkOrder, Room, Technician, OrderMaterial, Material
from django.utils import timezone
from datetime import datetime, timedelta


class DateTimeLocalInput(forms.DateTimeInput):
    input_type = 'datetime-local'


class RepairSubmitForm(forms.Form):
    building = forms.CharField(label='楼栋', max_length=20, widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': '例如：1'}))
    unit = forms.CharField(label='单元', max_length=10, required=False, widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': '例如：2（可选）'}))
    room_number = forms.CharField(label='房号', max_length=20, widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': '例如：101'}))
    resident_name = forms.CharField(label='住户姓名', max_length=50, widget=forms.TextInput(attrs={'class': 'form-control'}))
    contact_phone = forms.CharField(label='联系电话', max_length=20, widget=forms.TextInput(attrs={'class': 'form-control'}))
    problem_type = forms.ChoiceField(label='问题类型', choices=WorkOrder.PROBLEM_TYPES, widget=forms.Select(attrs={'class': 'form-select'}))
    urgency = forms.ChoiceField(label='紧急程度', choices=WorkOrder.URGENCY_CHOICES, widget=forms.Select(attrs={'class': 'form-select'}))
    description = forms.CharField(label='问题描述', widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 4, 'placeholder': '请详细描述问题...'}))
    available_start = forms.DateTimeField(label='可上门起始时间', widget=DateTimeLocalInput(attrs={'class': 'form-control'}))
    available_end = forms.DateTimeField(label='可上门结束时间', widget=DateTimeLocalInput(attrs={'class': 'form-control'}))

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)
        if self.user and hasattr(self.user, 'resident_profile'):
            resident = self.user.resident_profile
            self.fields['building'].initial = resident.room.building
            self.fields['unit'].initial = resident.room.unit
            self.fields['room_number'].initial = resident.room.room_number
            self.fields['resident_name'].initial = resident.name
            self.fields['contact_phone'].initial = resident.phone
            self.fields['building'].widget.attrs['readonly'] = True
            self.fields['unit'].widget.attrs['readonly'] = True
            self.fields['room_number'].widget.attrs['readonly'] = True
            self.fields['resident_name'].widget.attrs['readonly'] = True

    def clean(self):
        cleaned_data = super().clean()
        building = cleaned_data.get('building', '').strip()
        unit = cleaned_data.get('unit', '').strip()
        room_number = cleaned_data.get('room_number', '').strip()

        if not building or not room_number:
            raise forms.ValidationError('请填写完整的楼栋和房号信息')

        try:
            room = Room.objects.get(building=building, unit=unit, room_number=room_number, is_active=True)
        except Room.DoesNotExist:
            raise forms.ValidationError(f'房号「{building}栋{unit + "单元" if unit else ""}{room_number}室」不存在或无效，请核实！')

        cleaned_data['room_obj'] = room

        start = cleaned_data.get('available_start')
        end = cleaned_data.get('available_end')
        if start and end:
            if start >= end:
                raise forms.ValidationError('可上门结束时间必须晚于起始时间')
            if end < timezone.now():
                raise forms.ValidationError('可上门结束时间不能早于当前时间')

        return cleaned_data


class AssignForm(forms.Form):
    technician = forms.ModelChoiceField(
        label='维修师傅',
        queryset=Technician.objects.filter(is_active=True),
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    scheduled_start = forms.DateTimeField(label='预约上门时间', widget=DateTimeLocalInput(attrs={'class': 'form-control'}))
    scheduled_end = forms.DateTimeField(label='预约结束时间', widget=DateTimeLocalInput(attrs={'class': 'form-control'}))

    def __init__(self, *args, **kwargs):
        self.order = kwargs.pop('order', None)
        super().__init__(*args, **kwargs)

    def clean(self):
        cleaned_data = super().clean()
        tech = cleaned_data.get('technician')
        start = cleaned_data.get('scheduled_start')
        end = cleaned_data.get('scheduled_end')

        if start and end:
            if start >= end:
                raise forms.ValidationError('结束时间必须晚于开始时间')

            if self.order:
                if start < self.order.available_start or end > self.order.available_end:
                    raise forms.ValidationError(
                        f'预约时间需在住户可上门时间段内（{self.order.available_start.strftime("%Y-%m-%d %H:%M")} ~ {self.order.available_end.strftime("%Y-%m-%d %H:%M")}）'
                    )

            if tech:
                exclude_id = self.order.id if self.order else None
                has_conflict, conflict_order = tech.has_conflict(start, end, exclude_id)
                if has_conflict:
                    raise forms.ValidationError(
                        f'师傅「{tech.name}」在此时间段已有工单：{conflict_order.order_no}（{conflict_order.scheduled_start.strftime("%Y-%m-%d %H:%M")} ~ {conflict_order.scheduled_end.strftime("%Y-%m-%d %H:%M")}），请重新选择时间或师傅！'
                    )

        return cleaned_data


class StartWorkForm(forms.Form):
    arrival_note = forms.CharField(label='到场备注', required=False, widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 2, 'placeholder': '可选：记录到场情况'}))


class FinishWorkForm(forms.Form):
    result = forms.CharField(label='处理结果', widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 5, 'placeholder': '请详细描述处理过程和结果...'}))
    photo_placeholder = forms.CharField(label='照片说明（占位）', required=False, widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 3, 'placeholder': '此处填写拍摄照片的说明，例如：1. 水管漏水点特写 2. 更换零件后整体照'}))


class MaterialForm(forms.ModelForm):
    class Meta:
        model = OrderMaterial
        fields = ['material', 'quantity', 'remark']
        widgets = {
            'material': forms.Select(attrs={'class': 'form-select material-select'}),
            'quantity': forms.NumberInput(attrs={'class': 'form-control quantity-input', 'step': '0.01', 'min': '0'}),
            'remark': forms.TextInput(attrs={'class': 'form-control', 'placeholder': '备注（可选）'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['material'].queryset = Material.objects.all().order_by('name')


MaterialFormSet = forms.inlineformset_factory(
    WorkOrder, OrderMaterial, form=MaterialForm, extra=5, can_delete=True
)


class ConfirmForm(forms.Form):
    SATISFACTION_CHOICES = [(i, f'{i}星 - {"非常不满意" if i == 1 else "不满意" if i == 2 else "一般" if i == 3 else "满意" if i == 4 else "非常满意"}') for i in range(1, 6)]
    satisfaction = forms.ChoiceField(label='服务满意度', choices=SATISFACTION_CHOICES, widget=forms.RadioSelect(attrs={'class': 'form-check-input me-2'}))
    confirm_remark = forms.CharField(label='评价备注', required=False, widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 3, 'placeholder': '可选：补充说明或建议'}))


class ReworkForm(forms.Form):
    rework_reason = forms.CharField(label='返工原因', widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 4, 'placeholder': '请详细说明需要返工的原因...'}))
    available_start = forms.DateTimeField(label='可上门起始时间', widget=DateTimeLocalInput(attrs={'class': 'form-control'}))
    available_end = forms.DateTimeField(label='可上门结束时间', widget=DateTimeLocalInput(attrs={'class': 'form-control'}))

    def clean(self):
        cleaned_data = super().clean()
        start = cleaned_data.get('available_start')
        end = cleaned_data.get('available_end')
        if start and end:
            if start >= end:
                raise forms.ValidationError('可上门结束时间必须晚于起始时间')
            if end < timezone.now():
                raise forms.ValidationError('可上门结束时间不能早于当前时间')
        return cleaned_data
