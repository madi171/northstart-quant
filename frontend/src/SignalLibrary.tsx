import {useCallback, useEffect, useMemo, useState, type FormEvent} from 'react';
import {ArrowDownToLine, BookOpen, RefreshCw, Trash2} from 'lucide-react';
import {api, download} from './api';
import {time} from './components';
import type {Asset, News} from './types';
import './signalLibrary.css';

type Kind = 'news' | 'feature';
type Sentiment = 'positive' | 'negative' | 'neutral';
type Signal = {id: string; kind: Kind; title: string; asset_id: string | null; sentiment: Sentiment | null; feature_name: string | null; category: string | null; value: number | null; max_age_days: number | null; source: string; source_url: string; observed_at: string; available_at: string; note: string; created_at: string};
type Form = {kind: Kind; title: string; asset_id: string; sentiment: Sentiment; feature_name: string; category: string; value: string; max_age_days: string; source: string; source_url: string; observed_at: string; available_at: string; note: string};
const labels: Record<Sentiment, string> = {positive: '利好', negative: '利空', neutral: '中性'};
const categories: Record<string, string> = {fundamental: '基本面', macro: '宏观', orderbook: '盘口衍生', announcement: '公告', other: '其他'};
const fresh = (): Form => ({kind: 'news', title: '', asset_id: '', sentiment: 'neutral', feature_name: '', category: 'announcement', value: '', max_age_days: '120', source: '', source_url: '', observed_at: '', available_at: '', note: ''});
const localDate = (value: string) => {const date = new Date(value); if (Number.isNaN(date.getTime())) return ''; const offset = date.getTimezoneOffset() * 60000; return new Date(date.getTime() - offset).toISOString().slice(0, 16);};
const iso = (value: string) => {const date = new Date(value); if (!value || Number.isNaN(date.getTime())) throw new Error('请填写有效的观测时间与首次可用时间。'); return date.toISOString();};

export function SignalLibrary({assets, news, newsError}: {assets: Asset[]; news: News[]; newsError: string}) {
  const [rows, setRows] = useState<Signal[]>([]);
  const [form, setForm] = useState<Form>(fresh);
  const [filter, setFilter] = useState<'all' | Kind>('all');
  const [query, setQuery] = useState('');
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const load = useCallback(async (signal?: AbortSignal) => {
    try {const result = await api<Signal[]>('/investment-signals', undefined, undefined, signal); setRows(result); setError('');}
    catch (failure) {if (!signal?.aborted) setError((failure as Error).message);}
    finally {if (!signal?.aborted) setLoading(false);}
  }, []);
  useEffect(() => {const control = new AbortController(); void load(control.signal); return () => control.abort();}, [load]);
  const shown = useMemo(() => rows.filter(row => (filter === 'all' || row.kind === filter) &&
    [row.title, row.source, row.feature_name || '', assets.find(asset => asset.id === row.asset_id)?.symbol || ''].some(value => value.toLowerCase().includes(query.toLowerCase().trim()))), [rows, assets, filter, query]);
  const counts = {news: rows.filter(row => row.kind === 'news').length, feature: rows.filter(row => row.kind === 'feature').length};
  const update = <K extends keyof Form>(key: K, value: Form[K]) => setForm(current => ({...current, [key]: value}));
  async function save(event: FormEvent) {
    event.preventDefault(); if (busy) return;
    setBusy(true); setError(''); setMessage('');
    try {
      if (form.kind === 'feature' && (!form.value.trim() || !Number.isFinite(Number(form.value)))) throw new Error('数值特征需填写有效数值。');
      const observed_at = iso(form.observed_at), available_at = iso(form.available_at);
      if (available_at < observed_at) throw new Error('首次可用时间不能早于观测时间。');
      const body = {kind: form.kind, asset_id: form.asset_id || null, title: form.title.trim(), sentiment: form.kind === 'news' ? form.sentiment : null,
        feature_name: form.kind === 'feature' ? form.feature_name.trim() : null, category: form.kind === 'feature' ? form.category : null,
        value: form.kind === 'feature' ? Number(form.value) : null, max_age_days: form.kind === 'feature' ? Number(form.max_age_days) : null,
        source: form.source.trim(), source_url: form.source_url.trim(), observed_at, available_at, note: form.note.trim()};
      await api('/investment-signals', body);
      setForm(fresh()); setMessage('记录已保存；不会自动影响交易或策略决策。'); await load();
    } catch (failure) {setError((failure as Error).message);} finally {setBusy(false);}
  }
  async function remove(row: Signal) {
    if (busy || !window.confirm(`删除“${row.title}”？历史研究快照不会随之修改。`)) return;
    setBusy(true); setError(''); setMessage('');
    try {await api('/investment-signals/' + encodeURIComponent(row.id), undefined, 'DELETE'); await load(); setMessage('记录已删除。');}
    catch (failure) {setError((failure as Error).message);} finally {setBusy(false);}
  }
  async function exportFeatures() {
    setBusy(true); setError('');
    try {const data = await api<{records: unknown[]}>('/investment-signals/features'); download('investment-features.json', data.records); setMessage('数值特征已导出，可用于研究流程的特征记录。');}
    catch (failure) {setError((failure as Error).message);} finally {setBusy(false);}
  }
  function prefill(item: News) {
    setForm({...fresh(), title: item.title, source: item.provider || '待核实来源', source_url: item.url || '',
      observed_at: localDate(item.published_at), available_at: localDate(item.first_seen_at),
      note: item.synthetic ? '演示资讯；仅用于流程验证，不代表真实消息。' : (item.body || '').slice(0, 2000)});
    setMessage('已填入资讯来源与时间。请选择关联标的及利好／利空／中性后保存；系统不会自动判断情绪。');
    document.getElementById('signal-entry')?.scrollIntoView({behavior: 'smooth', block: 'start'});
  }
  return <div className="signal-library">
    <header className="signal-heading"><div><h2>让每一个判断回到证据</h2><p>资讯观点由你标注；数值特征记录何时被观察、何时实际可用。两者均不会自动下单。</p></div><div className="signal-heading-tools"><button onClick={() => {setLoading(true); void load();}} disabled={loading || busy}><RefreshCw size={15}/>刷新记录</button><button onClick={() => void exportFeatures()} disabled={!counts.feature || busy}><ArrowDownToLine size={15}/>导出数值特征</button></div></header>
    {error && <p className="signal-alert" role="alert">{error}</p>}{message && <p className="signal-notice" role="status">{message}</p>}
    <div className="signal-layout">
      <section className="panel signal-editor" id="signal-entry" aria-labelledby="signal-editor-title"><div className="signal-panel-title"><h3 id="signal-editor-title">记录新判断</h3><small>先核验，再归档</small></div>
        <form onSubmit={save} className="signal-form"><fieldset disabled={busy}><div className="signal-switch" role="group" aria-label="记录类型"><button type="button" aria-pressed={form.kind === 'news'} onClick={() => update('kind', 'news')}>资讯信号</button><button type="button" aria-pressed={form.kind === 'feature'} onClick={() => update('kind', 'feature')}>数值特征</button></div>
          <label>名称 / 标题<input required maxLength={160} value={form.title} onChange={event => update('title', event.target.value)} placeholder={form.kind === 'news' ? '简述这则资讯及其影响对象' : '例如：季度营收同比增速'}/></label>
          <label>关联标的<select value={form.asset_id} onChange={event => update('asset_id', event.target.value)}><option value="">全市场 / 宏观</option>{assets.map(asset => <option value={asset.id} key={asset.id}>{asset.symbol} · {asset.name}</option>)}</select></label>
          {form.kind === 'news' ? <label>人工判断<select value={form.sentiment} onChange={event => update('sentiment', event.target.value as Sentiment)}><option value="positive">利好</option><option value="negative">利空</option><option value="neutral">中性／影响未明</option></select><small>这是人工标签，不代表价格会按该方向变化。</small></label> : <div className="signal-feature-inputs"><label>特征字段<input required pattern="[a-z][a-z0-9_.]*" maxLength={64} value={form.feature_name} onChange={event => update('feature_name', event.target.value)} placeholder="fundamental.revenue_growth"/></label><label>类别<select value={form.category} onChange={event => update('category', event.target.value)}>{Object.entries(categories).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label><div className="signal-form-pair"><label>数值<input required type="number" step="any" value={form.value} onChange={event => update('value', event.target.value)}/></label><label>有效期（天）<input required type="number" min={1} max={730} value={form.max_age_days} onChange={event => update('max_age_days', event.target.value)}/></label></div></div>}
          <label>数据来源<input required maxLength={200} value={form.source} onChange={event => update('source', event.target.value)} placeholder="媒体／公告／数据提供方"/></label><label>原始链接（可选）<input type="url" maxLength={1000} value={form.source_url} onChange={event => update('source_url', event.target.value)} placeholder="https://..."/></label>
          <div className="signal-form-pair"><label>观测／发布时间<input required type="datetime-local" value={form.observed_at} onChange={event => update('observed_at', event.target.value)}/></label><label>首次实际可用时间<input required type="datetime-local" value={form.available_at} onChange={event => update('available_at', event.target.value)}/></label></div><p className="signal-hint">时间按浏览器本地时区填写并保存为 UTC。首次可用时间不能早于观测时间；回测会再保守排除同日信息。</p>
          <label>核验说明（可选）<textarea rows={3} maxLength={2000} value={form.note} onChange={event => update('note', event.target.value)} placeholder="标注理由、证据链接、口径限制…"/></label><button className="primary" type="submit">{busy ? '正在保存…' : '保存记录'}</button>
        </fieldset></form>
        {newsError && <p className="signal-alert" role="alert">资讯来源读取失败：{newsError}。仍可手动记录。</p>}
        {news.length > 0 && <div className="signal-inbox"><h4><BookOpen size={15}/>资讯待标注</h4><p>从当前消息源载入，不会自动判断利好或利空。</p>{news.slice(0, 6).map((item, index) => <button key={`${item.url}-${index}`} onClick={() => prefill(item)}><strong>{item.title}</strong><small>{item.provider} · {time(item.first_seen_at)}{item.synthetic ? ' · 演示' : ''}</small></button>)}</div>}
      </section>
      <section className="panel signal-records" aria-labelledby="signal-records-title"><div className="signal-panel-title"><div><h3 id="signal-records-title">证据与特征档案</h3><small>最近最多 1000 条 · 不等于交易指令</small></div><label className="signal-search">筛选<input type="search" value={query} onChange={event => setQuery(event.target.value)} placeholder="标题、字段、标的或来源"/></label></div><div className="signal-tabs" role="group" aria-label="筛选信号"><button aria-pressed={filter === 'all'} onClick={() => setFilter('all')}>全部 {rows.length}</button><button aria-pressed={filter === 'news'} onClick={() => setFilter('news')}>资讯 {counts.news}</button><button aria-pressed={filter === 'feature'} onClick={() => setFilter('feature')}>特征 {counts.feature}</button></div>
        {loading ? <p className="signal-empty">正在读取记录…</p> : shown.length ? <div className="signal-record-list">{shown.map(row => <article key={row.id} className="signal-record"><div className="signal-record-top"><span className={row.kind === 'feature' ? 'signal-mark feature' : `signal-mark ${row.sentiment}`}>{row.kind === 'feature' ? '数值特征' : labels[row.sentiment || 'neutral']}</span><span>{assets.find(asset => asset.id === row.asset_id)?.symbol || '全市场'}</span><button aria-label={`删除${row.title}`} disabled={busy} title="删除记录" onClick={() => void remove(row)}><Trash2 size={15}/></button></div><h4>{row.title}</h4>{row.kind === 'feature' && <p className="signal-value">{row.feature_name} <strong>{row.value}</strong><span>{categories[row.category || ''] || row.category} · 有效期 {row.max_age_days} 天</span></p>}{row.note && <p className="signal-record-note">{row.note}</p>}<div className="signal-record-meta"><span>来源 {row.source}</span><span>观测 {time(row.observed_at)}</span><span>首次可用 {time(row.available_at)}</span>{row.source_url && <a href={row.source_url} target="_blank" rel="noopener noreferrer">查看原始来源</a>}</div></article>)}</div> : <p className="signal-empty">{rows.length ? '没有符合筛选条件的记录。' : '暂无信号。可先从资讯待标注中选一条，核验后保存。'}</p>}
      </section>
    </div>
  </div>;
}
