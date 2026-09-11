export interface Session { userId: string; expiresAt: number }
export function isExpired(s: Session): boolean { return Date.now() > s.expiresAt; }
