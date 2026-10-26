import type {Asset} from './types';

export interface AssetValuation {id: string; price?: number; quote_date?: string; source?: string}

export function holdingValue(asset: Asset, quote?: AssetValuation): number | undefined {
  if (!asset.holding_quantity) return 0;
  return quote?.price !== undefined && Number.isFinite(quote.price) && quote.price > 0
    ? asset.holding_quantity * quote.price : undefined;
}

export function sortAssetsByValue(assets: Asset[], quotes: AssetValuation[]): Asset[] {
  const byId = new Map(quotes.map(quote => [quote.id, quote]));
  return [...assets].sort((a, b) => {
    const currency = a.currency.localeCompare(b.currency);
    if (currency) return currency;
    const av = holdingValue(a, byId.get(a.id)), bv = holdingValue(b, byId.get(b.id));
    if (av === undefined && bv !== undefined) return 1;
    if (bv === undefined && av !== undefined) return -1;
    return (bv ?? 0) - (av ?? 0) || a.market.localeCompare(b.market) || a.symbol.localeCompare(b.symbol);
  });
}
