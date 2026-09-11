export function shippingCost(weightKg: number, zone: string): number {
  return zone === "domestic" ? 5 + weightKg : 15 + weightKg * 2;
}
