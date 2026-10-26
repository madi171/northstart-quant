# -*- coding: utf-8 -*-
"""注释语法和内置策略可读性的离线回归。"""

import unittest

from app.strategy import DEFAULT_CODE
from app.strategy import parse
from app.strategy import StrategyError
from app.strategy import target


class StrategyCommentsTest(unittest.TestCase):
    """#注释不改变逻辑，常规模块写法不能误用到受限DSL。"""

    def test_default_comments_keep_targets_unchanged(self) -> None:
        """注释版与去注释版在预热、涨跌和横盘下仓位一致。"""
        plain = '\n'.join(line for line in DEFAULT_CODE.splitlines() if not line.lstrip().startswith('#')) + '\n'
        params = {'fast': 10, 'slow': 30, 'weight': 0.15}
        cases = [[], [100] * 20, [100] * 100, list(range(100, 200)), list(range(200, 100, -1))]
        self.assertIn('    # ', DEFAULT_CODE)
        for values in cases:
            with self.subTest(length=len(values)):
                self.assertEqual(target(DEFAULT_CODE, values, params), target(plain, values, params))

    def test_standalone_and_trailing_comments_preserve_trace(self) -> None:
        """硬解析允许#，诊断仍使用注释之后的真实return行号。"""
        code = 'def target(history, params):\n    # 目标权重，不是股数。\n    return 0.15  # 平台风控仍生效。\n'
        trace = {}
        self.assertEqual(target(code, [], {}, trace=trace), 0.15)
        self.assertEqual(trace['return_line'], 3)

    def test_docstrings_and_annotations_are_not_strategy_documentation(self) -> None:
        """文档字符串、返回注解和局部注解不能作为规范策略文本。"""
        invalid = [
            '"""模块文档。"""\ndef target(history, params):\n    return 0.0\n',
            'def target(history, params):\n    """函数文档。"""\n    return 0.0\n',
            'def target(history, params) -> float:\n    return 0.0\n',
            'def target(history, params):\n    weight: float = 0.15\n    return weight\n',
        ]
        for code in invalid:
            with self.subTest(code=code):
                with self.assertRaises(StrategyError):
                    parse(code)

    def test_comments_count_towards_character_limit(self) -> None:
        """注释不增加AST节点，但仍计入代码长度。"""
        code = 'def target(history, params):\n    return 0.0\n'
        with self.assertRaisesRegex(StrategyError, '12000'):
            parse('# ' + '说明' * 12000 + '\n' + code)
