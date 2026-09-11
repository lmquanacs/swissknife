export function auditLine(actor: string, action: string): string {
  return `${new Date().toISOString()} ${actor} ${action}`;
}
