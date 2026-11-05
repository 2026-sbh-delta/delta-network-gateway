"""매핑한 서버의 월 예상 비용을 같은 사용 시간으로 비교한다. 참고 값이며 finding 이나 판정을 만들지 않는다."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from delta_ai.contract import CostEstimate, CostLine, SourceServer
from delta_ai.mapping_catalog import COST_EXCLUDES, HOURS_PER_MONTH, PRICED_ON, Route


def _monthly(prices: Mapping[str, Mapping[str, float]], region: str, instance_type: str | None) -> float | None:
    hourly = prices.get(region, {}).get(instance_type or "")
    return None if hourly is None else round(hourly * HOURS_PER_MONTH, 2)


def _gap(source: float | None, target: float | None) -> float | None:
    return None if source is None or target is None else round(target - source, 2)


def _notes(route: Route, source_type: str | None, target_type: str | None, source: float | None,
           target: float | None) -> tuple[str, ...]:
    notes = []
    if target_type is not None:
        spec, chosen = route.source_instances[source_type], route.target_instances[target_type]
        if chosen.memory_gib > spec.memory_gib:
            notes.append(f"대상 메모리가 더 크다({spec.memory_gib} → {chosen.memory_gib} GiB). 차액에 사양 차이가 포함된다")
        if source is None or target is None:
            notes.append("이 리전의 단가가 표에 없어 추정하지 않는다")
    return tuple(notes)


def _line(route: Route, server: SourceServer, target_type: str | None, source_region: str,
          target_region: str) -> CostLine:
    source = _monthly(route.source_prices, source_region, server.instance_type)
    target = _monthly(route.target_prices, target_region, target_type)
    return CostLine(server=server.name, source_type=server.instance_type, target_type=target_type,
                    source_monthly=source, target_monthly=target, difference=_gap(source, target),
                    notes=_notes(route, server.instance_type, target_type, source, target))


def _total(values: Sequence[float | None]) -> float | None:
    return None if any(value is None for value in values) else round(sum(values), 2)


def estimate_cost(route: Route, servers: Sequence[tuple[SourceServer, str | None]], source_region: str,
                  target_region: str) -> CostEstimate | None:
    """(서버, 매핑된 대상 타입) 목록을 받는다. 대상 타입은 매핑 표와 같은 값을 쓴다."""
    if not servers:
        return None
    lines = tuple(_line(route, server, target_type, source_region, target_region) for server, target_type in servers)
    source_total = _total([line.source_monthly for line in lines])
    target_total = _total([line.target_monthly for line in lines])
    return CostEstimate(hours_per_month=HOURS_PER_MONTH, priced_on=PRICED_ON, source_region=source_region,
                        target_region=target_region, lines=lines, source_total=source_total, target_total=target_total,
                        difference_total=_gap(source_total, target_total), excludes=COST_EXCLUDES)
