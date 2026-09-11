import { catalog } from "./products.js";
export function findBySku(sku: string) { return catalog.find((p) => p.sku === sku); }
