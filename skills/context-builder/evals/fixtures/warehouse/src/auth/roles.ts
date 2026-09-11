export type Role = "picker" | "supervisor" | "admin";
export const canAdjustStock = (r: Role): boolean => r !== "picker";
