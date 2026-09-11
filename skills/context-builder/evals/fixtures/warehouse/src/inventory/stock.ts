const onHand = new Map<string, number>();
const holds = new Map<string, number>();

export async function adjustStock(sku: string, delta: number): Promise<void> {
  onHand.set(sku, (onHand.get(sku) ?? 0) + delta);
}

export async function recordHold(sku: string, qty: number, ttl: number): Promise<void> {
  holds.set(`${sku}:${ttl}`, qty);
}

export async function getOnHand(sku: string): Promise<number> {
  return onHand.get(sku) ?? 0;
}
