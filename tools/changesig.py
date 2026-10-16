#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""将字幕清洗改动压成稳定的“决策指纹”。

指纹只用于判断某类改动是否已经人工确认；不修改字幕文本。
"""

import json
import re


NUMBER_RULES = {'指数名补全'}
CLASS_RULES = {'删语气词'}
EXACT_RULES = {'删单字残句'}


def _char_class(text):
    """保留边界类型，忽略具体汉字和数字。"""
    out = []
    for ch in text:
        if '\u4e00' <= ch <= '\u9fff':
            kind = '汉'
        elif ch.isdigit():
            kind = '数'
        elif ch.isspace():
            kind = '空'
        elif re.match(r'[\W_]', ch):
            kind = '标'
        else:
            kind = '字'
        if not out or out[-1] != kind:
            out.append(kind)
    return '/'.join(out)


def _number_context(change):
    """指数名补全的判据：紧跟在改动位置后的数字及其后一字。"""
    context = change.get('context', '')
    tail = context.split('】', 1)[-1]
    match = re.search(r'(\d+)(.)?', tail)
    return ''.join(match.groups('')) if match else tail[:8]


def signature(change):
    rule = change.get('rule', '')
    kind = change.get('kind', '')
    if rule in NUMBER_RULES:
        discriminator = _number_context(change)
    elif rule in CLASS_RULES:
        discriminator = _char_class(change.get('context', ''))
    elif rule in EXACT_RULES:
        discriminator = change.get('context', '')
    else:
        discriminator = ''
    return json.dumps([rule, kind, discriminator], ensure_ascii=False,
                      separators=(',', ':'))


def describe(change):
    before = change.get('before') or '（无）'
    after = change.get('after') or '（无）'
    context = change.get('context', '')
    return '%s　%s → %s　%s' % (
        change.get('kind', '改'), before, after, context)
