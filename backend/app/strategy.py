"""A bounded Python AST interpreter. Never eval/exec untrusted strategy source.

Supported: one target(history, params) function, local assignments, if/return,
finite arithmetic, comparisons, slices, mean/min/max/sum/len/abs. No loops,
imports, attributes, IO, recursion, comprehensions or arbitrary function calls.
"""
import ast
import math
import operator
from .indicators import INDICATORS

DEFAULT_CODE = '''def target(history, params):
    # fast/slow为日线根数且应满足fast<slow；weight=0.15表示15%目标仓位。
    # 先保护空历史和预热，避免把不足窗口的数据当成完整均线。
    if len(history) < params["slow"]:
        return 0.0
    # 简单均线包含当前收盘价，不使用未来行情。
    fast = mean(history[-params["fast"]:])
    slow = mean(history[-params["slow"]:])
    if fast > slow:
        # 返回目标权重而非买入股数，之后仍受平台风控约束。
        return params["weight"]
    return 0.0
'''
OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
       ast.Div: operator.truediv, ast.Mod: operator.mod}
CMP = {ast.Gt: operator.gt, ast.GtE: operator.ge, ast.Lt: operator.lt,
       ast.LtE: operator.le, ast.Eq: operator.eq, ast.NotEq: operator.ne}
FUNCS = {'mean': lambda xs: sum(xs) / len(xs), 'len': len, 'sum': sum,
         'min': min, 'max': max, 'abs': abs, 'feature': lambda name, default: default, **INDICATORS}
ALLOWED = (ast.Module, ast.FunctionDef, ast.arguments, ast.arg, ast.Assign,
           ast.Return, ast.If, ast.Name, ast.Load, ast.Store, ast.Constant,
           ast.BinOp, ast.UnaryOp, ast.USub, ast.UAdd, ast.Not, ast.Compare,
           ast.BoolOp, ast.And, ast.Or, ast.Subscript, ast.Slice, ast.Call, ast.IfExp,
           *OPS.keys(), *CMP.keys())


class StrategyError(ValueError):
    pass


def parse(code):
    if len(code) > 12000:
        raise StrategyError('策略代码超过 12000 字符')
    try:
        tree = ast.parse(code)
    except (SyntaxError, RecursionError) as exc:
        raise StrategyError('Python 语法无效') from exc
    if len(list(ast.walk(tree))) > 600:
        raise StrategyError('策略过于复杂')
    if len(tree.body) != 1 or not isinstance(tree.body[0], ast.FunctionDef):
        raise StrategyError('仅允许一个 target(history, params) 函数')
    fn = tree.body[0]
    if (fn.name != 'target' or [a.arg for a in fn.args.args] != ['history', 'params']
            or fn.decorator_list or fn.args.defaults or fn.args.kwonlyargs
            or fn.args.posonlyargs or fn.args.vararg or fn.args.kwarg or fn.returns):
        raise StrategyError('函数签名必须为 target(history, params)')
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node is not fn:
            raise StrategyError('禁止嵌套函数')
        if isinstance(node, ast.Constant) and not isinstance(node.value, (int, float, str, bool, type(None))):
            raise StrategyError('不支持此常量类型')
        if not isinstance(node, ALLOWED):
            raise StrategyError(f'安全策略子集不支持 {type(node).__name__}')
        if isinstance(node, ast.Call) and (not isinstance(node.func, ast.Name) or node.func.id not in FUNCS or node.keywords):
            raise StrategyError('仅允许白名单数学函数')
        if isinstance(node, ast.Call) and node.func.id == 'feature':
            if len(node.args) != 2 or not isinstance(node.args[0], ast.Constant) or not isinstance(node.args[0].value, str):
                raise StrategyError('特征须使用 feature("字段名", 缺失默认值) 且字段名为常量')
        if isinstance(node, ast.Assign) and (len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name) or node.targets[0].id in {'history', 'params', *FUNCS}):
            raise StrategyError('仅允许赋值给普通局部变量')
        if isinstance(node, ast.Name) and node.id.startswith('_'):
            raise StrategyError('不支持私有名称')
    return fn


def target(code, history, params, trace=None, features=None):
    fn = parse(code) if isinstance(code, str) else code
    env = {'history': history, 'params': params}
    functions = {**FUNCS, 'feature': lambda name, default: (features or {}).get(name, default)}
    budget = 2000

    def evaluate(n):
        nonlocal budget
        budget -= 1
        if budget < 0:
            raise StrategyError('计算预算耗尽')
        if isinstance(n, ast.Constant):
            return n.value
        if isinstance(n, ast.Name):
            return env[n.id]
        if isinstance(n, ast.Slice):
            return slice(*(evaluate(v) if v else None for v in [n.lower, n.upper, n.step]))
        if isinstance(n, ast.Subscript):
            return evaluate(n.value)[evaluate(n.slice)]
        if isinstance(n, ast.BinOp):
            a, b = evaluate(n.left), evaluate(n.right)
            if type(a) not in (int, float) or type(b) not in (int, float):
                raise StrategyError('算术只接受数值')
            result = OPS[type(n.op)](a, b)
            if not math.isfinite(result) or abs(result) > 1e15:
                raise StrategyError('数值越界')
            return result
        if isinstance(n, ast.UnaryOp):
            v = evaluate(n.operand)
            return not v if isinstance(n.op, ast.Not) else -v if isinstance(n.op, ast.USub) else +v
        if isinstance(n, ast.Compare):
            a = evaluate(n.left)
            for op, rhs in zip(n.ops, n.comparators):
                b = evaluate(rhs)
                if not CMP[type(op)](a, b):
                    return False
                a = b
            return True
        if isinstance(n, ast.BoolOp):
            if isinstance(n.op, ast.And):
                return all(evaluate(v) for v in n.values)
            return any(evaluate(v) for v in n.values)
        if isinstance(n, ast.IfExp):
            return evaluate(n.body if evaluate(n.test) else n.orelse)
        if isinstance(n, ast.Call):
            return functions[n.func.id](*(evaluate(a) for a in n.args))
        raise StrategyError('不支持的表达式')

    def statements(nodes):
        for node in nodes:
            if isinstance(node, ast.Assign):
                env[node.targets[0].id] = evaluate(node.value)
            elif isinstance(node, ast.Return):
                value = evaluate(node.value)
                if trace is not None:
                    trace['return_line'] = node.lineno
                return True, value
            elif isinstance(node, ast.If):
                found, value = statements(node.body if evaluate(node.test) else node.orelse)
                if found:
                    return True, value
        return False, None

    try:
        found, value = statements(fn.body)
        if not found or type(value) not in (float, int) or not math.isfinite(value) or not 0 <= value <= 1:
            raise StrategyError('目标仓位必须是 0 到 1 之间的有限数值')
        return float(value)
    except (KeyError, TypeError, IndexError, ZeroDivisionError, OverflowError, RecursionError) as exc:
        raise StrategyError(f'策略计算失败：{type(exc).__name__}') from exc
