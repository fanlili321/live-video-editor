#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import unittest

import changesig as C


class ChangeSignatureTests(unittest.TestCase):
    def test_number_rule_distinguishes_number_context(self):
        a = {'rule': '指数名补全', 'kind': '增', 'before': '', 'after': '中证',
             'context': '全指呢从【】2000多点'}
        b = dict(a, context='除此之外【】800、')
        self.assertNotEqual(C.signature(a), C.signature(b))

    def test_rule_without_context_strategy_is_stable(self):
        a = {'rule': '年份补全', 'kind': '增', 'context': '在14年'}
        b = {'rule': '年份补全', 'kind': '增', 'context': '到15年'}
        self.assertEqual(C.signature(a), C.signature(b))

    def test_exact_rule_keeps_context(self):
        a = {'rule': '删单字残句', 'kind': '删', 'context': '甲【的】乙'}
        b = {'rule': '删单字残句', 'kind': '删', 'context': '丙【的】丁'}
        self.assertNotEqual(C.signature(a), C.signature(b))


if __name__ == '__main__':
    unittest.main()
