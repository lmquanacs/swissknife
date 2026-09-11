export const carriers = ["apex", "borealis", "cedar"] as const;
export type Carrier = (typeof carriers)[number];
