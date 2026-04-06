# SPDX-License-Identifier: AGPL-3.0-or-later

CATEGORY_LABELS = {
    'sponsor': '赞助/恰饭',
    'selfpromo': '无偿/自我推广',
    'exclusive_access': '独家访问/抢先体验',
    'interaction': '三连/互动提醒',
    'poi_highlight': '精彩时刻/重点',
    'intro': '过场/开场动画',
    'outro': '鸣谢/结束画面',
    'preview': '回顾/概要',
    'padding': '填充内容/前黑/后黑',
    'filler': '离题闲聊/玩笑',
    'music_offtopic': '音乐:非音乐部分',
    # Kept for compatibility with existing data even if it is not in the new list.
    'chapter': '章节',
}


def category_label(value: str) -> str:
    return CATEGORY_LABELS.get(value, value)
