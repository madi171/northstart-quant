import type {Job} from './types';
import {time} from './components';

export function ResearchTaskContext({job}: {job: Job}) {
  const spec = job.spec || {};
  const original = job.payload?.strategy;
  const dates = [spec.start, spec.end].filter(Boolean).map(String).join(' — ');
  return <section className="research-task-context" aria-label="任务信息">
    <dl>
      <div><dt>研究标的</dt><dd>{job.result?.symbol || job.summary?.symbol || job.payload?.asset?.symbol || '等待执行'}</dd></div>
      <div><dt>起始策略</dt><dd>{original?.name || '名称未记录'}</dd></div>
      <div><dt>研究区间</dt><dd>{dates || '区间未记录'}</dd></div>
      <div><dt>开始 / 结束</dt><dd>{job.started_at ? time(job.started_at) : '尚未开始'} / {job.finished_at ? time(job.finished_at) : '尚未结束'}</dd></div>
    </dl>
    <details><summary>任务标识与起始策略</summary><p className="small muted">任务 ID <code>{job.id}</code></p>{original?.notes && <p>{original.notes}</p>}{original?.code && <pre className="code-editor original">{original.code}</pre>}{original?.params && <pre>{JSON.stringify(original.params, null, 2)}</pre>}</details>
  </section>;
}
