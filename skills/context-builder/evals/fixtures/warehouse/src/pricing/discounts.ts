export function bulkDiscount(qty: number): number {
  return qty >= 20 ? 0.1 : 0;
}
