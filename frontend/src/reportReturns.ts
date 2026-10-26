import type {Point} from './types';

export function curveInitial(points: Point[], totalReturn: number, initial?: number, key: 'equity' | 'benchmark' = 'equity'): number | undefined {
  if (initial !== undefined && Number.isFinite(initial) && initial > 0) return initial;
  const last = points[points.length - 1]?.[key];
  const inferred = last !== undefined && Number.isFinite(totalReturn) && totalReturn > -1 ? last / (1 + totalReturn) : undefined;
  return inferred !== undefined && Number.isFinite(inferred) && inferred > 0 ? inferred : undefined;
}
