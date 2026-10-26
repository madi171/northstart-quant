from datetime import date, timedelta

import pytest

from app import runner
from app.backtest import simulate
from app.contracts import Bar


@pytest.mark.parametrize('with_external_warmup', [False, True])
def test_research_only_prior_prices_warm_up_holdout_and_segments(monkeypatch, tmp_path, with_external_warmup):
    first = date(2024, 1, 1)
    rows = [Bar((first + timedelta(days=i)).isoformat(), 100, 101, 99, 100.5, 10000) for i in range(200)]
    prior = [Bar('2023-12-30', 100, 100, 100, 100, 10000)] if with_external_warmup else []
    code = 'def target(history, params):\n    return 0.3 if len(history) >= 100 else 0.0'
    payload = {'spec': {'kind': 'research', 'mode': 'demo', 'initial': 10000, 'fee_bps': 0,
                        'slippage_bps': 0, 'start': rows[0].date, 'end': rows[-1].date, 'model_ids': ['test']},
               'asset': {'symbol': 'US.TEST', 'currency': 'USD', 'lot': 1},
               'strategy': {'id': 's', 'name': 'long window', 'code': code, 'params': {}},
               'risk': {'max_weight': .3}, 'providers': [{'id': 'test', 'enabled': True, 'driver': 'test',
                                                         'name': 'offline', 'config': {}}]}
    calls, contexts = [], []

    class Model:
        def __init__(self, config):
            pass

        def propose(self, context):
            contexts.append(context)
            return {'code': code, 'params': {}, **({'name':'长窗口预热 · 趋势持有',
                    'strategy_logic':'达到100条历史后持有三成仓位。', 'change_summary':'本例未改变交易代码，仅验证说明传递。',
                    'naming_reason':'名称体现长窗口预热。'} if with_external_warmup else {})}

    def traced(part, code, params, **options):
        warmup = options.get('warmup_bars', [])
        assert all(bar.date < part[0].date for bar in warmup)
        result = simulate(part, code, params, **options)
        calls.append((part, warmup, result))
        return result

    monkeypatch.setattr(runner, 'load_bars', lambda *args: (rows, {'name': 'fixture'}))
    monkeypatch.setattr(runner, 'read_warmup_bars', lambda *args: prior)
    monkeypatch.setattr(runner, 'load_news', lambda *args: {'items': [], 'failures': []})
    monkeypatch.setattr(runner, 'data_path', lambda path: tmp_path / path)
    monkeypatch.setitem(runner.MODEL_DRIVERS, 'test', Model)
    monkeypatch.setattr(runner, 'simulate', traced)
    result = runner.run(payload)
    assert [part for part, _, _ in calls] == [rows, rows[:140], rows[:140], rows[140:], rows[140:],
                                             rows[80:120], rows[120:160], rows[160:200]]
    for part, warmup, report in calls:
        assert warmup == prior + [bar for bar in rows if bar.date < part[0].date]
        assert report['initial_equity'] == 10000
        assert len(report['curve']) == len(part)
    for _, _, report in calls[3:5]:
        assert report['trades'][0]['date'] == rows[140].date
        assert report['trades'][0]['signal_date'] == rows[139].date
    assert result['research']['holdout_candidate']['trade_count'] > 0
    assert result['research']['holdout_warmup_days'] == 140 + len(prior)
    assert [part['warmup_days'] for part in result['research']['segments']] == [80 + len(prior), 120 + len(prior), 160 + len(prior)]
    assert result['research']['selected']['change'] == {'changed_lines':0, 'param_changes':{}, 'diff':''}
    assert result['research']['selected']['naming_source'] == ('model' if with_external_warmup else 'fallback')
    if with_external_warmup:
        assert result['research']['selected']['name'] == '长窗口预热 · 趋势持有'
        assert result['research']['selected']['naming_reason'] == '名称体现长窗口预热。'
    assert contexts[0]['training_end'] == rows[139].date
    assert max(item['date'] for item in contexts[0]['prices']) == rows[139].date
