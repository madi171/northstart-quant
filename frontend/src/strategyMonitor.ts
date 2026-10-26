export interface MonitorAttempt {
  id: string;
  status: string;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  heartbeat: string | null;
  error: string;
}

export interface MonitorSignal {
  job_id: string;
  finished_at: string | null;
  target_weight: number | null;
  quote_date: string | null;
  price: number | null;
  quote_stale: boolean;
  source_name: string | null;
  message: string | null;
}

export interface MonitorOrder {
  id: string;
  status: string;
  side: string;
  quantity: number;
  price: number | null;
  created_at: string;
  fill_price: number | null;
  fee: number | null;
  filled_at: string | null;
}

export interface MonitorRun {
  mode: string | null;
  environment: 'paper';
  asset_id: string | null;
  symbol: string | null;
  asset_name: string | null;
  currency: string | null;
  asset_enabled: boolean | null;
  account_id: string | null;
  shared_position: {quantity: number; price: number | null; quote_date: string | null} | null;
  job_counts: Record<string, number>;
  latest_attempt: MonitorAttempt | null;
  latest_signal: MonitorSignal | null;
  order_counts: Record<string, number>;
  latest_order: MonitorOrder | null;
  latest_fill: MonitorOrder | null;
}

export interface MonitorStrategy {
  id: string;
  name: string;
  status: string;
  scheduled: boolean;
  schedule_mode: string | null;
  schedule_asset_id: string | null;
  blockers: string[];
  runs: MonitorRun[];
}

export interface StrategyMonitorData {
  server_time: string;
  real_trading: boolean;
  strategies: MonitorStrategy[];
  note: string;
}

export function monitorTotals(data: StrategyMonitorData) {
  return data.strategies.reduce((total, strategy) => {
    if (strategy.status === 'approved') total.approved += 1;
    for (const run of strategy.runs) {
      total.inProgress += (run.job_counts.queued || 0) + (run.job_counts.running || 0) + (run.job_counts.finalizing || 0);
      total.pending += run.order_counts.pending || 0;
    }
    return total;
  }, {approved: 0, inProgress: 0, pending: 0});
}

export function runState(run: MonitorRun): {text: string; tone: 'quiet' | 'active' | 'warning'} {
  const status = run.latest_attempt?.status;
  if (status === 'failed') return {text: '最近信号失败', tone: 'warning'};
  if (status === 'running' || status === 'finalizing') return {text: '信号计算中', tone: 'active'};
  if (status === 'queued') return {text: '信号排队中', tone: 'active'};
  if (status === 'cancelled') return {text: '最近信号已取消', tone: 'quiet'};
  if (run.latest_signal) return {text: '最近信号已完成', tone: 'quiet'};
  return {text: '尚无信号记录', tone: 'quiet'};
}
