export const sectionColors = ['#477d61', '#4a78a8', '#8a6da7', '#b07c40', '#a45e73', '#508b91', '#777e45', '#756854'];
export function sectionColor(section) {
  const key = `${section.id} ${section.title}`.toLowerCase();
  const standard = [['education', 0], ['publication', 1], ['teaching', 2], ['research', 3], ['github', 6], ['service', 5], ['leadership', 4], ['experience', 7]];
  const match = standard.find(([term]) => key.includes(term));
  if (match) return sectionColors[match[1]];
  return sectionColors[[...section.id].reduce((sum, char) => sum + char.charCodeAt(0), 0) % sectionColors.length];
}
export function fraction(score, maximum) {
  return typeof score === 'number' && Number.isFinite(score) && maximum > 0 ? Math.max(0, Math.min(1, score / maximum)) : null;
}
export function heatColor(score, maximum) {
  const value = fraction(score, maximum);
  return value === null ? null : `hsl(145 27% ${96 - value * 58}%)`;
}
export function sortRows(rows, sort) {
  return [...rows].sort((a, b) => {
    if (sort.column === 'name') return a.name.localeCompare(b.name) * sort.direction;
    const av = sort.column === 'total' ? a.total : a.sections[sort.column];
    const bv = sort.column === 'total' ? b.total : b.sections[sort.column];
    if (av == null) return bv == null ? a.name.localeCompare(b.name) : 1;
    if (bv == null) return -1;
    return (av - bv) * sort.direction || a.name.localeCompare(b.name);
  });
}
export function incomplete(row, sectionId) {
  return row.assessment?.sections.find(section => section.section_id === sectionId)?.status === 'insufficient_evidence';
}
