import json
import signal
import subprocess
import sys
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from .db import Session, Job, Strategy, Asset, Setting, audit, now
from .bootstrap import initialize
from .schemas import RunInput
from .services import enqueue, propose_order

stopping = False


def stop(*_):
    global stopping
    stopping = True


def recover():
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()
    with Session.begin() as db:
        db.execute(update(Job).where(Job.status == 'running', Job.heartbeat < cutoff).values(
            status='failed', error='Worker 心跳过期。未自动重放交易副作用，请检查后重试。', finished_at=now()))


def schedule():
    local = datetime.now(ZoneInfo('Asia/Shanghai'))
    with Session.begin() as db:
        cfg = db.get(Setting, 'schedule').value
        if not cfg.get('asset_id') or not cfg.get('strategy_id'):
            return
        kinds = []
        # After all previous-date US regular sessions have finished; polling data once a day.
        if cfg.get('daily_enabled') and local.hour >= 9:
            kinds.append(('signal', local.date().isoformat()))
        if cfg.get('weekly_enabled') and local.weekday() == 5 and local.hour >= 12:
            kinds.append(('research', local.strftime('%G-W%V')))
        for kind, period in kinds:
            key = f'schedule:{kind}:{period}'
            if db.scalar(select(Job).where(Job.dedup == key)):
                continue
            today = datetime.now(timezone.utc).date()
            try:
                spec = RunInput(kind=kind, strategy_id=cfg['strategy_id'], asset_id=cfg['asset_id'],
                                mode=cfg['mode'], start=today - timedelta(days=365), end=today,
                                model_ids=cfg.get('model_ids', []), client_key=key)
                enqueue(db, spec, key)
            except ValueError as exc:
                db.add(Job(kind=kind, dedup=key, payload={'schedule': cfg}, status='failed', error=str(exc), finished_at=now()))


def claim():
    with Session.begin() as db:
        row = db.scalar(select(Job).where(Job.status == 'queued').order_by(Job.created_at).limit(1))
        if not row:
            return None
        stamp = now()
        count = db.execute(update(Job).where(Job.id == row.id, Job.status == 'queued').values(
            status='running', started_at=stamp, heartbeat=stamp, attempts=Job.attempts + 1),
            execution_options={'synchronize_session': False}).rowcount
        return (row.id, row.payload, stamp) if count == 1 else None


def save_optimization_progress(db, row, path):
    if row.kind == 'backtest':
        if path.exists():
            if path.stat().st_size > 4096:
                raise ValueError('回测进度超过大小预算')
            progress = json.loads(path.read_text())
            if (progress.get('phase') not in ('simulating', 'finalizing') or
                    type(progress.get('completed')) is not int or type(progress.get('total')) is not int or
                    progress['total'] < 1 or not 0 <= progress['completed'] <= progress['total'] or
                    (progress.get('date') is not None and not isinstance(progress['date'], str))):
                raise ValueError('回测进度格式无效')
            row.result = {'backtest_progress': progress}
        return
    if row.kind != 'optimization' or not path.exists():
        return
    if path.stat().st_size > 8_000_000:
        raise ValueError('优化进度超过大小预算')
    result = json.loads(path.read_text())
    from .optimization import persist_candidates
    row.result = persist_candidates(db, row, result)
    if result.get('snapshot'):
        row.payload = {**row.payload, 'frozen_data': {
            'path':result['snapshot'], 'hash':result['data_hash'], 'source':result['source']}}


def execute(job_id, payload, stamp):
    temporary = TemporaryDirectory(prefix='northstar-optimization-')
    progress_path = Path(temporary.name) / 'progress.json'
    process = subprocess.Popen([sys.executable, '-m', 'app.runner', str(progress_path)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    began = time.monotonic()
    first = True
    try:
        while True:
            try:
                stdout, stderr = process.communicate(json.dumps(payload) if first else None, timeout=2)
                break
            except subprocess.TimeoutExpired:
                first = False
                with Session.begin() as db:
                    row = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
                    if stopping or row.status != 'running' or row.started_at != stamp:
                        process.kill()
                        process.communicate()
                        if row.started_at == stamp and row.status in ('running', 'cancelled'):
                            save_optimization_progress(db, row, progress_path)
                        return
                    save_optimization_progress(db, row, progress_path)
                    row.heartbeat = now()
                budget = payload['spec'].get('time_budget_seconds', 300) + (30 if payload['spec']['kind'] == 'optimization' else 0)
                if time.monotonic() - began > budget:
                    raise TimeoutError(f'任务超过 {budget} 秒计算预算')
        response = json.loads(stdout)
        if not response.get('ok'):
            raise ValueError(response.get('error', '计算失败'))
        with Session.begin() as db:
            row = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
            if row.status != 'running' or row.started_at != stamp:
                if row.status == 'cancelled' and row.started_at == stamp:
                    save_optimization_progress(db, row, progress_path)
                return
            changed = db.execute(update(Job).where(Job.id == job_id, Job.status == 'running', Job.started_at == stamp).values(
                status='finalizing'), execution_options={'synchronize_session': False}).rowcount
            if changed != 1:
                return
            result = response['result']
            if row.kind == 'optimization':
                from .optimization import persist_candidates
                result = persist_candidates(db, row, result)
            if row.kind == 'research':
                selected = result['research']['selected']
                candidate = Strategy(name=selected['name'], code=selected['code'], params=selected['params'],
                                     parent_id=selected['parent_id'], notes=selected['notes'],
                                     provenance={'job_id': job_id, 'provider_id': selected['provider_id'],
                                                 'context_hash': selected['context_hash'], 'prompt_version': selected['prompt_version'],
                                                 'data_hash': result['data_hash'], 'usage': selected['usage'],
                                                 'feature_snapshot': result.get('feature_snapshot', {}),
                                                 'rebalance_every': result.get('rebalance_every', 1),
                                                 **{key:selected.get(key) for key in ('strategy_logic','change_summary','naming_reason','naming_source','change')},
                                                 'holdout': result['research']['holdout_candidate']})
                db.add(candidate)
                db.flush()
                result['research']['candidate_id'] = candidate.id
            if row.kind == 'signal':
                result['intent'] = propose_order(db, row, result)
            row.payload = {**row.payload, 'frozen_data': {
                'path': result['snapshot'], 'hash': result['data_hash'], 'source': result['source']}}
            row.result, row.status, row.finished_at = result, 'succeeded', now()
            optimization = result.get('optimization', {})
            if row.kind == 'optimization' and optimization.get('stop_reason') == 'provider_access_denied':
                row.status = 'failed'
                row.error = '模型访问受限或限流，已停止调用；请核验网关权限、密钥或网络条件。已完成轮次保留，不自动重试。'
            audit(db, 'job.' + row.status, job_id=job_id)
    except Exception as exc:
        if process.poll() is None:
            process.kill()
            process.communicate()
        with Session.begin() as db:
            row = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
            if row.status == 'running' and row.started_at == stamp:
                try:
                    save_optimization_progress(db, row, progress_path)
                except (ValueError, OSError):
                    pass
                row.status, row.error, row.finished_at = 'failed', str(exc)[:1000], now()
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()
        temporary.cleanup()


def main():
    initialize()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    while not stopping:
        recover()
        try:
            schedule()
        except IntegrityError:
            pass  # Another scheduler already inserted the same deterministic key.
        task = claim()
        if task:
            execute(*task)
        else:
            time.sleep(2)


if __name__ == '__main__':
    main()
