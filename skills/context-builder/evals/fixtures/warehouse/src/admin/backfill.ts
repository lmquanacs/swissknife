import { adjustStock } from "../inventory/stock.js";
/** One-off corrective tool run by hand; not on the order path. */
export async function backfillStock(sku: string, to: number): Promise<void> {
  await adjustStock(sku, to);
}
