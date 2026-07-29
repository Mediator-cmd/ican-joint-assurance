export function resolveSelectedId(
  selectedId: string,
  availableIds: readonly string[],
): string {
  if (availableIds.includes(selectedId)) return selectedId;
  return availableIds[0] ?? "";
}
