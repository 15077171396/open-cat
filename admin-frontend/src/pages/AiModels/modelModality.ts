export type ModelModality = 'text' | 'image'

export function modelsForModality<T extends { modality?: string }>(
  rows: T[],
  modality: ModelModality,
): T[] {
  return rows.filter((row) => (row.modality || 'text') === modality)
}
