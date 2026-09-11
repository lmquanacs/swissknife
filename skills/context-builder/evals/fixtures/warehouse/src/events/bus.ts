type Handler = (evt: unknown) => Promise<void>;
const subs = new Map<string, Handler[]>();
export function on(topic: string, h: Handler): void {
  subs.set(topic, [...(subs.get(topic) ?? []), h]);
}
