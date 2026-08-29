export interface RetryPolicy {
  attempts: number;
  backoffMs: number;
}

export const defaultRetryPolicy: RetryPolicy = { attempts: 3, backoffMs: 250 };
