import json
from types import SimpleNamespace

from app.backtest import simulate
from app.contracts import Bar
from app.worker import save_optimization_progress


def test_simulation_reports_actual_completed_bars_without_changing_result():
    bars = [Bar(f'2024-01-{day:02d}', 100, 100, 100, 100, 1000) for day in range(2, 16)]
    code = 'def target(history, params):\n    return 0.0'
    events = []
    report = simulate(bars, code, {}, on_progress=lambda done, total, day: events.append((done, total, day)))
    baseline = simulate(bars, code, {})
    assert events[0] == (1, 14, '2024-01-02')
    assert events[-1] == (14, 14, '2024-01-15')
    assert [done for done, _, _ in events] == list(range(1, 15))
    assert report == baseline


def test_worker_persists_only_valid_backtest_progress(tmp_path):
    path = tmp_path / 'progress.json'
    row = SimpleNamespace(kind='backtest', result={})
    path.write_text(json.dumps({'phase': 'simulating', 'completed': 7, 'total': 10, 'date': '2024-01-11'}))
    save_optimization_progress(None, row, path)
    assert row.result['backtest_progress']['completed'] == 7
    path.write_text(json.dumps({'phase': 'simulating', 'completed': 11, 'total': 10, 'date': None}))
    try:
        save_optimization_progress(None, row, path)
    except ValueError:
        pass
    else:
        raise AssertionError('超出交易日总数的进度应拒绝')
