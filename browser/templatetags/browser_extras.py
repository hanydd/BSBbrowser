from django import template

from browser.models import Vipuser
from browser.category_labels import category_label as _category_label

register = template.Library()


@register.simple_tag
def is_vip(userid) -> bool:
    return Vipuser.objects.filter(userid=userid).exists()


@register.filter(name='category_label')
def category_label(value):
    return _category_label(value)
