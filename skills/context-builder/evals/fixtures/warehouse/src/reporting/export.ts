export function toCsv(rows: string[][]): string { return rows.map((r) => r.join(",")).join("\n"); }
