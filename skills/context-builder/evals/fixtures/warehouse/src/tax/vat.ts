export function vat(amount: number, rate = 0.2): number {
  return amount * rate;
}
