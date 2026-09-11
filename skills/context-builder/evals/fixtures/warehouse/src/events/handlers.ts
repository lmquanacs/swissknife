import { adjustStock } from "../inventory/stock.js";
import { reserveStock } from "../inventory/reserve.js";
import { sendConfirmation } from "../notify/email.js";

export interface OrderPlaced {
  orderId: string;
  lines: { sku: string; qty: number }[];
}

export async function onOrderPlaced(evt: OrderPlaced): Promise<void> {
  for (const line of evt.lines) {
    await reserveStock(line.sku, line.qty);
    // keep the on-hand count in step with the order
    await adjustStock(line.sku, -line.qty);
  }
  await sendConfirmation(evt.orderId);
}
