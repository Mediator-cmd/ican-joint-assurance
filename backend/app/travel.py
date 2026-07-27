"""Shortest travel-time calculations over the scenario zone graph."""

from __future__ import annotations

import heapq

from .models import Scenario


class RouteNotFoundError(ValueError):
    """Raised when no directed route exists between two zones."""


def shortest_travel_minutes(scenario: Scenario, source_zone_id: str, target_zone_id: str) -> int:
    if source_zone_id == target_zone_id:
        return 0

    graph = {zone.zone_id: zone.travel_minutes for zone in scenario.zones}
    if source_zone_id not in graph or target_zone_id not in graph:
        raise RouteNotFoundError(f"unknown route endpoint {source_zone_id} -> {target_zone_id}")

    queue: list[tuple[int, str]] = [(0, source_zone_id)]
    best: dict[str, int] = {source_zone_id: 0}

    while queue:
        minutes, zone_id = heapq.heappop(queue)
        if zone_id == target_zone_id:
            return minutes
        if minutes != best.get(zone_id):
            continue

        for neighbor_id, edge_minutes in graph[zone_id].items():
            candidate = minutes + edge_minutes
            if candidate < best.get(neighbor_id, candidate + 1):
                best[neighbor_id] = candidate
                heapq.heappush(queue, (candidate, neighbor_id))

    raise RouteNotFoundError(f"no route from {source_zone_id} to {target_zone_id}")
