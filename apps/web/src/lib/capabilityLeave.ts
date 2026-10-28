/** One active source editor; navigation asks it before discarding a draft. */
let guard: (() => Promise<boolean>) | undefined;
export function registerCapabilityLeave(next: () => Promise<boolean>): () => void {
  guard = next;
  return () => {if (guard === next) guard = undefined;};
}
export async function confirmCapabilityLeave(): Promise<boolean> {
  return guard ? guard() : true;
}
