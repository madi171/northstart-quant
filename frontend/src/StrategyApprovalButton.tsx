import {RefreshCw} from 'lucide-react';

export type ApprovalPending = {id: string; action: 'approve' | 'revoke'} | null;
export type ApprovalControls = {
  pending: ApprovalPending;
  onChange: (id: string, revoke: boolean) => void;
};
type Props = ApprovalControls & {
  strategyId: string;
  status?: string;
  busy: boolean;
  allowRevoke?: boolean;
  label?: string;
};

export function StrategyApprovalButton({strategyId, status, busy, pending, onChange, allowRevoke = false, label = '批准策略'}: Props) {
  const processing = pending?.id === strategyId;
  const approved = status === 'approved';
  return <button
    type="button"
    className={'strategy-approval-button '+(allowRevoke ? 'text-button' : 'primary')}
    disabled={busy || !!pending || !status || (approved && !allowRevoke)}
    aria-busy={processing}
    title={approved && allowRevoke ? '取消批准，阻止新增信号及待确认订单成交；不会自动平仓' : '人工批准此版本用于后续模拟信号；不会自动运行或下单'}
    onClick={() => onChange(strategyId, approved && allowRevoke)}
  >
    {processing && <RefreshCw size={15} className="is-spinning" aria-hidden="true"/>}
    <span role={processing ? 'status' : undefined}>{processing ? pending.action === 'revoke' ? '撤销中…' : '批准中…' : !status ? '正在同步状态…' : approved ? allowRevoke ? '撤销批准' : '已人工批准' : label}</span>
  </button>;
}
