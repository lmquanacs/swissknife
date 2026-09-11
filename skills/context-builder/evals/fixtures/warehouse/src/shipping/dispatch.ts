import { getOnHand } from "../inventory/stock.js";
/** Refuses to dispatch an order line the warehouse cannot cover. */
export async function canDispatch(sku: string, qty: number): Promise<boolean> {
  return (await getOnHand(sku)) >= qty;
}
