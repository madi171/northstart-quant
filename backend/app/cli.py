"""Reproducible offline backtest. No HTTP service required."""
import argparse
import json
from pathlib import Path
from .runner import run
from .strategy import DEFAULT_CODE


def main():
    parser = argparse.ArgumentParser(description='Northstar 可复现回测')
    parser.add_argument('--manifest', help='任务导出的 payload JSON，优先于其他参数')
    parser.add_argument('--symbol', default='US.SPY')
    parser.add_argument('--start', default='2024-01-01')
    parser.add_argument('--end', default='2024-12-31')
    parser.add_argument('--code', help='受限Python策略文件')
    parser.add_argument('--params', default='{"fast":10,"slow":30,"weight":0.15}')
    parser.add_argument('--initial', type=float, default=100000)
    parser.add_argument('--lot', type=int, default=1)
    parser.add_argument('--fee-bps', type=float, default=5)
    parser.add_argument('--slippage-bps', type=float, default=5)
    parser.add_argument('--out', default='data/backtest-report.json')
    args = parser.parse_args()
    if args.manifest:
        payload = json.loads(Path(args.manifest).read_text())
        payload['spec']['kind'] = 'backtest'
    else:
        payload = {'spec': {'kind': 'backtest', 'mode': 'demo', 'start': args.start, 'end': args.end, 'initial': args.initial,
                            'fee_bps': args.fee_bps, 'slippage_bps': args.slippage_bps, 'model_ids': []},
                   'asset': {'symbol': args.symbol, 'currency': 'USD', 'lot': args.lot},
                   'strategy': {'id': 'cli', 'name': 'CLI', 'code': Path(args.code).read_text() if args.code else DEFAULT_CODE,
                                'params': json.loads(args.params)},
                   'providers': [{'id': 'demo', 'name': '演示行情', 'kind': 'market', 'driver': 'demo', 'enabled': True, 'priority': 1, 'config': {}}],
                   'risk': {'max_weight': .3}}
    from .schemas import RunInput, StrategyInput
    RunInput(**(payload['spec'] | {'strategy_id': 'cli', 'asset_id': 'cli', 'client_key': 'cli-backtest'}))
    StrategyInput(name='CLI', code=payload['strategy']['code'], params=payload['strategy']['params'])
    if payload['asset']['lot'] < 1:
        parser.error('--lot 必须大于0')
    result = run(payload)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({'payload': payload, 'result': result}, ensure_ascii=False, indent=2))
    print(json.dumps(result['metrics'], ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
