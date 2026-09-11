import { getOnHand } from "../inventory/stock.js";
/** Nightly report of every SKU whose on-hand count has gone negative. */
export async function negativeStock(skus: string[]): Promise<string[]> {
  const out: string[] = [];
  for (const sku of skus) if ((await getOnHand(sku)) < 0) out.push(sku);
  return out;
}
