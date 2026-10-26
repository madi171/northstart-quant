import {Activity} from 'lucide-react';
import type {Job} from './types';
import './backtestProgress.css';

export function backtestProgress(job: Job) {
  const progress = job.result?.backtest_progress;
  if (job.kind !== 'backtest' || !progress || !Number.isFinite(progress.total) || !Number.isFinite(progress.completed) || progress.total <= 0 || progress.completed < 0 || progress.completed > progress.total) return null;
  return progress;
}

export function BacktestProgressPanel({job}: {job: Job}) {
  if (job.kind !== 'backtest') return null;
  const progress = backtestProgress(job);
  const running = job.status === 'running' || job.status === 'finalizing';
  const finished = job.status === 'succeeded';
  const ratio = finished ? 100 : progress ? Math.min(99, Math.floor(progress.completed / progress.total * 90)) : 0;
  const label = job.status === 'queued' ? '等待后台计算节点领取任务' :
    job.status === 'finalizing' || (running && progress?.phase === 'finalizing') ? '交易日计算完成，正在保存结果与生成报告' :
    running && progress ? `已计算 ${progress.completed} / ${progress.total} 个交易日${progress.date ? `，最近完成 ${progress.date}` : ''}` :
    running ? '正在读取行情并准备历史数据' : finished ? '回测报告已生成' :
    job.status === 'cancelled' ? '回测已取消' : `回测失败${job.error ? `：${job.error}` : ''}`;
  return <section className={'backtest-progress'+(running ? ' is-running' : '')} aria-label="回测计算进度">
    <div className="backtest-progress-heading"><span className="backtest-progress-icon"><Activity size={19}/></span><div><h3>{running ? '正在推演历史交易日' : finished ? '计算完成' : job.status === 'queued' ? '等待开始回测' : '计算已停止'}</h3><p role="status" aria-live="polite">{label}</p></div><strong>{running && !progress ? '准备中' : `${ratio}%`}</strong></div>
    {running && !progress ? <div className="backtest-progress-track indeterminate" role="progressbar" aria-label="历史行情准备中" aria-valuetext={label}><span/></div> :
      <div className="backtest-progress-track" role="progressbar" aria-label="回测计算进度" aria-valuemin={0} aria-valuemax={100} aria-valuenow={ratio} aria-valuetext={label}><span style={{width: `${ratio}%`}}/></div>}
    <p className="backtest-progress-foot">{running && progress?.phase === 'finalizing' ? '交易日已处理完毕，尚未完成报告落盘。' : '进度以实际完成交易日计算；最后 10% 预留给报告生成，成功后才显示 100%。'}</p>
  </section>;
}
