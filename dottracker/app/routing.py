"""Sort a small crew itinerary using caller-supplied road travel times."""

from typing import Annotated

from pydantic import BaseModel, Field, model_validator

Minute = Annotated[int, Field(strict=True, ge=0, le=10080)]
Equipment = Annotated[str, Field(min_length=1, max_length=80)]


class Job(BaseModel):
    id: str = Field(min_length=1, max_length=80)
    earliest_start: Minute = 0
    latest_finish: Minute = 1440
    service_minutes: Minute = 0
    equipment: list[Equipment] = Field(default_factory=list, max_length=20)

    @model_validator(mode='after')
    def validate_window(self):
        if self.earliest_start + self.service_minutes > self.latest_finish:
            raise ValueError('Service must fit inside the job time window')
        return self


class RouteRequest(BaseModel):
    departure_minute: Minute = 0
    crew_equipment: list[Equipment] = Field(default_factory=list, max_length=20)
    jobs: list[Job] = Field(min_length=1, max_length=8)
    travel_minutes: list[Annotated[list[Minute | None], Field(max_length=9)]] = Field(max_length=9)

    @model_validator(mode='after')
    def validate_matrix(self):
        size = len(self.jobs) + 1
        if len(self.travel_minutes) != size or any(len(row) != size for row in self.travel_minutes):
            raise ValueError('Matrix must have one row and column per job plus the starting point')
        if len({job.id for job in self.jobs}) != len(self.jobs):
            raise ValueError('Job IDs must be unique')
        return self


class ScheduledJob(BaseModel):
    job_id: str
    arrival_minute: int
    service_start_minute: int
    finish_minute: int


class RoutePlan(BaseModel):
    stops: list[ScheduledJob]
    driving_minutes: int
    finish_minute: int


def sort_route(request: RouteRequest) -> RoutePlan | None:
    equipment = set(request.crew_equipment)
    if any(not equipment.issuperset(job.equipment) for job in request.jobs):
        return None

    best_score = None
    best_stops = None

    def search(node, remaining, available, driving, stops):
        nonlocal best_score, best_stops
        # Travel times are nonnegative, so a more expensive prefix cannot win.
        if best_score is not None and driving > best_score[0]:
            return
        if not remaining:
            score = (driving, available)
            if best_score is None or score < best_score:
                best_score, best_stops = score, stops
            return

        for next_node in remaining:
            travel = request.travel_minutes[node][next_node]
            if travel is None:
                continue
            job = request.jobs[next_node - 1]
            arrival = available + travel
            start = max(arrival, job.earliest_start)
            finish = start + job.service_minutes
            if finish <= job.latest_finish:
                search(next_node, tuple(index for index in remaining if index != next_node),
                       finish, driving + travel, [*stops, (job.id, arrival, start, finish)])

    search(0, tuple(range(1, len(request.jobs) + 1)), request.departure_minute, 0, [])
    if best_score is None:
        return None
    return RoutePlan(
        stops=[ScheduledJob(job_id=job_id, arrival_minute=arrival, service_start_minute=start,
                            finish_minute=finish) for job_id, arrival, start, finish in best_stops],
        driving_minutes=best_score[0],
        finish_minute=best_score[1],
    )
