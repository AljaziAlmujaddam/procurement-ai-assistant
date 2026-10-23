"""Parse and validate LLM-generated aggregation pipelines before they touch the database."""

import re

from bson import json_util

MAX_RESULTS = 500

ALLOWED_STAGES = {
    "$match",
    "$group",
    "$sort",
    "$limit",
    "$skip",
    "$project",
    "$addFields",
    "$set",
    "$unset",
    "$unwind",
    "$count",
    "$facet",
    "$bucket",
    "$bucketAuto",
    "$sortByCount",
    "$replaceRoot",
    "$replaceWith",
    "$sample",
}

# Operators that write data, run arbitrary JavaScript, or reach outside the collection.
FORBIDDEN_OPERATORS = {
    "$out",
    "$merge",
    "$where",
    "$function",
    "$accumulator",
    "$lookup",
    "$graphLookup",
    "$unionWith",
    "$currentOp",
    "$collStats",
    "$indexStats",
    "$planCacheStats",
    "$listSessions",
}

_CODE_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$")


class InvalidPipelineError(ValueError):
    pass


def _iter_keys(obj):
    if isinstance(obj, dict):
        for key, value in obj.items():
            yield key
            yield from _iter_keys(value)
    elif isinstance(obj, list):
        for item in obj:
            yield from _iter_keys(item)


def parse_pipeline(text: str) -> list[dict]:
    """Parse a JSON / MongoDB Extended JSON pipeline (e.g. {"$date": "..."} becomes a datetime)."""
    text = _CODE_FENCE.sub("", text.strip())
    try:
        pipeline = json_util.loads(text)
    except ValueError as exc:
        raise InvalidPipelineError(f"Pipeline is not valid JSON: {exc}") from exc
    if isinstance(pipeline, dict):
        pipeline = [pipeline]
    if not isinstance(pipeline, list) or not pipeline:
        raise InvalidPipelineError("Pipeline must be a non-empty JSON array of stages.")
    return pipeline


def validate_pipeline(pipeline: list[dict]) -> list[dict]:
    for i, stage in enumerate(pipeline):
        if not isinstance(stage, dict) or len(stage) != 1:
            raise InvalidPipelineError(f"Stage {i} must be an object with exactly one stage operator.")
        [name] = stage
        if name not in ALLOWED_STAGES:
            raise InvalidPipelineError(f"Stage {i} uses '{name}', which is not allowed.")

    forbidden = FORBIDDEN_OPERATORS.intersection(_iter_keys(pipeline))
    if forbidden:
        raise InvalidPipelineError(f"Pipeline uses forbidden operator(s): {', '.join(sorted(forbidden))}.")
    return pipeline


def enforce_result_limit(pipeline: list[dict]) -> list[dict]:
    last_name, last_value = next(iter(pipeline[-1].items()))
    if last_name == "$count":
        return pipeline
    if last_name == "$limit" and isinstance(last_value, int) and last_value <= MAX_RESULTS:
        return pipeline
    return [*pipeline, {"$limit": MAX_RESULTS}]


def prepare_pipeline(text: str) -> list[dict]:
    return enforce_result_limit(validate_pipeline(parse_pipeline(text)))
