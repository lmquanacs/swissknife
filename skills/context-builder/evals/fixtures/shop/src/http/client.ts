import { RetryPolicy, defaultRetryPolicy } from "../config/settings";

// One of the two retry implementations in this repo. See billing/charge.ts.
export async function fetchWithRetry(
  url: string,
  policy: RetryPolicy = defaultRetryPolicy,
): Promise<Response> {
  let lastError: unknown;
  for (let attempt = 0; attempt < policy.attempts; attempt++) {
    try {
      return await fetch(url);
    } catch (error) {
      lastError = error;
      await new Promise((r) => setTimeout(r, policy.backoffMs * attempt));
    }
  }
  throw lastError;
}
