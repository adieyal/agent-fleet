// What a job's documents are and the order they are read in; plain data, shared by every page that lists them.
export const DOC_KIND = {
  report: { label: 'Report', glyph: '▤' },
  file:   { label: 'File',   glyph: '✎' },
  outbox: { label: 'Outbox', glyph: '⇪' },
  brief:   { label: 'Brief',   glyph: '☰' },
  context: { label: 'Context', glyph: '⧉' },
  prd:     { label: 'PRD',     glyph: '☰' },   // a Ralph prd.json in a project library, read as a page
};
// what the job was given rather than what it produced
export const INPUT_KINDS = new Set(['brief', 'context']);
export function docsOf(job) { return (job.documents || []).filter(d => !INPUT_KINDS.has(d.kind)).sort((a, b) => (a.mtime || 0) - (b.mtime || 0)); }
// step briefs in step order, then context files by name
export function inputDocsOf(job) {
  const inputs = (job.documents || []).filter(d => INPUT_KINDS.has(d.kind));
  return [...inputs.filter(d => d.kind === 'brief').sort((a, b) => (a.step ?? 0) - (b.step ?? 0)), ...inputs.filter(d => d.kind === 'context')];
}
// The order the panel lists a job's documents in, and the reader steps through: produced newest first, then inputs.
export function jobDocSequence(job) { return [...docsOf(job).reverse(), ...inputDocsOf(job)]; }
export function kindOf(doc) { return DOC_KIND[doc.kind] ? doc.kind : 'file'; }
