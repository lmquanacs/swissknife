import { defaultReservationPolicy } from "../config/limits.js";
import { adjustStock, recordHold } from "./stock.js";

/**
 * Place a hold on stock for an order line.
 */
export async function reserveStock(sku: string, qty: number): Promise<void> {
  const policy = defaultReservationPolicy;
  if (qty > policy.maxHold) throw new Error(`hold of ${qty} exceeds maxHold`);

  await recordHold(sku, qty, policy.holdSeconds);

  if (policy.decrementOnReserve) {
    await adjustStock(sku, -qty);
  }
}
