import type { NormalizedPoint, SpatialLayout, SpatialRouteLeg } from "./types";


function toCanvas(point: NormalizedPoint, layout: SpatialLayout): [number, number] {
  return [point.x * layout.canvas.width, point.y * layout.canvas.height];
}

export function spatialRoutePolylinePoints(
  layout: SpatialLayout,
  legs: SpatialRouteLeg[],
): string {
  const pathById = new Map(layout.paths.map((path) => [path.path_id, path]));
  const points: NormalizedPoint[] = [];
  legs.forEach((leg) => {
    const path = pathById.get(leg.path_id);
    if (!path) return;
    const segment = leg.traversal === "reverse" ? [...path.points].reverse() : path.points;
    points.push(...(points.length ? segment.slice(1) : segment));
  });
  return points
    .map((point) => toCanvas(point, layout).map((value) => value.toFixed(2)).join(","))
    .join(" ");
}
