export type DiffKind = 'added' | 'removed' | 'hunk' | 'header' | 'context';
export interface DiffLine {text: string; kind: DiffKind; before: number | null; after: number | null}

export function parseUnifiedDiff(diff: string): DiffLine[] {
  let before = 0, after = 0;
  return diff.split('\n').filter((line, index, lines) => line !== '' || index !== lines.length - 1).map(text => {
    const hunk = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/.exec(text);
    if (hunk) {before = Number(hunk[1]); after = Number(hunk[2]); return {text, kind: 'hunk', before: null, after: null};}
    if (text.startsWith('--- ') || text.startsWith('+++ ') || text.startsWith('\\')) return {text, kind: 'header', before: null, after: null};
    if (text.startsWith('+')) return {text, kind: 'added', before: null, after: after++};
    if (text.startsWith('-')) return {text, kind: 'removed', before: before++, after: null};
    return {text, kind: 'context', before: before++, after: after++};
  });
}
