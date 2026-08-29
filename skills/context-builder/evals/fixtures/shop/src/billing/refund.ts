import { fetchWithRetry } from "../http/client";

export async function refundCustomer(id: string, cents: number) {
  const res = await fetchWithRetry(`/api/refund/${id}?amount=${cents}`);
  return res.json();
}
