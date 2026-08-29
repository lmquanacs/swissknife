import { fetchWithRetry } from "../http/client";

// The second retry path: it retries the *charge*, and each attempt already
// goes through fetchWithRetry. That nesting is the bug an agent should find.
export async function chargeCustomer(id: string, cents: number) {
  for (let attempt = 0; attempt < 3; attempt++) {
    const res = await fetchWithRetry(`/api/charge/${id}?amount=${cents}`);
    if (res.ok) return res.json();
  }
  throw new Error("charge failed");
}
