import type {Job} from './types';

export function researchManifest(job: Job) {
  const opt = job.result?.optimization;
  if (job.status !== 'succeeded' || !job.payload?.strategy || !opt?.selected?.code || !opt.selected.params || !job.result?.snapshot) return null;
  return {
    ...job.payload,
    spec: {...job.payload.spec, kind: 'backtest', model_ids: []},
    strategy: {...job.payload.strategy, ...opt.selected, id: opt.candidate_id || job.payload.strategy.id},
    feature_snapshot: job.result.feature_snapshot || job.payload.feature_snapshot,
    rebalance_every: opt.style?.rebalance_every || 1,
    frozen_data: {path: job.result.snapshot, hash: job.result.data_hash, source: job.result.source},
  };
}
