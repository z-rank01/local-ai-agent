"""Auditable arithmetic, not financial classification or semantic certification.

Usage: python calculate.py input.json output.json
Input: {operation: sum|subtract|multiply|divide, inputs: [{value, unit, period, source}], unit}
"""
from decimal import Decimal, localcontext
import json
from pathlib import Path
import sys


def calculate(request):
    inputs = request['inputs']
    if not inputs or any(not all(str(row.get(k, '')).strip() for k in
                                 ('value', 'unit', 'period', 'source')) for row in inputs):
        raise ValueError('每项输入必须有原值、单位、期间和来源')
    numbers = [Decimal(str(row['value'])) for row in inputs]
    if any(not n.is_finite() for n in numbers):
        raise ValueError('计算输入必须为有限数字')
    operation = request['operation']
    if operation in ('sum', 'subtract') and len({r['unit'] for r in inputs}) != 1:
        raise ValueError('加减运算须使用相同单位')
    with localcontext() as context:
        context.prec = 40
        if operation == 'sum':
            result = sum(numbers, Decimal(0))
        elif operation == 'subtract' and len(numbers) == 2:
            result = numbers[0] - numbers[1]
        elif operation == 'multiply' and len(numbers) == 2:
            result = numbers[0] * numbers[1]
        elif operation == 'divide' and len(numbers) == 2:
            result = numbers[0] / numbers[1]
        else:
            raise ValueError('未知操作或操作数数量错误')
    return {**request, 'result': str(result), 'precision': 40,
            'meaning': '仅核定算术；期间可比、来源真实性和业务解释须另行核对'}


if __name__ == '__main__':
    result = calculate(json.loads(Path(sys.argv[1]).read_text(encoding='utf-8')))
    encoded = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)
    with Path(sys.argv[2]).open('x', encoding='utf-8') as output:
        output.write(encoded)
    print(encoded)
