from time import perf_counter

from .contract import Cost, ReviewRequest, ReviewResult, Timing


class NoopReviewSystem:
    def review(self, request: ReviewRequest) -> ReviewResult:
        started = perf_counter()
        cost = Cost(input_tokens=0, output_tokens=0, estimated_amount=None, model_id=None)
        timing = Timing(model_seconds=0, total_seconds=perf_counter() - started)
        return ReviewResult((), cost, timing, "done", False)
