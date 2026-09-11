import type { OrderPlaced } from "../events/handlers.js";
export function orderTotal(evt: OrderPlaced, unitPrice: (sku: string) => number): number {
  return evt.lines.reduce((sum, l) => sum + unitPrice(l.sku) * l.qty, 0);
}
