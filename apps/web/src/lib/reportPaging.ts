/** Remove only the legacy renderer's exact navigation footer. Evidence is unchanged. */
export function reportPageContent(content: string): string {
  return content.replace(/\n\n---\n\n第 \d+\/\d+ 页。可继续说[^\n]*\s*$/u, '');
}
