"""OpenAI-compatible model calls and per-task budget enforcement."""

from dataclasses import asdict, dataclass, field
import json
from math import isfinite
import os
from pathlib import Path
from queue import Empty, Queue
from tempfile import NamedTemporaryFile
from threading import Thread
from time import perf_counter
from typing import Any, Callable, Literal, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from uuid import uuid4

from jsonschema import ValidationError
from jsonschema.validators import validator_for

try:
    import tomllib
except ImportError:  # Python 3.10
    import tomli as tomllib


class EndpointError(RuntimeError):
    """The configured endpoint did not give a usable response."""


@dataclass(frozen=True)
class Price:
    input_per_million: float
    output_per_million: float


@dataclass(frozen=True)
class ModelConfig:
    base_url: str
    api_key: str = field(repr=False)
    model_id: str
    price_table_version: str
    price: Price | None = None


def _required_text(table: Mapping, key: str) -> str:
    value = table[key]
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-empty string")
    return value.strip()


def _price_rate(table: Mapping, key: str) -> float:
    value = table[key]
    if type(value) not in (int, float) or not isfinite(value) or value < 0:
        raise ValueError(f"{key} must be a finite non-negative number")
    return float(value)


def load_model_config(path: Path | None = None) -> ModelConfig:
    path = path if path is not None else Path.home() / ".openkb" / "config.toml"
    try:
        with path.open("rb") as source:
            data = tomllib.load(source)
        model = data["model"]
        prices = data["prices"]
        base_url = _required_text(model, "base_url").rstrip("/")
        parsed = urlsplit(base_url)
        if (parsed.scheme not in ("http", "https") or not parsed.hostname
                or parsed.username or parsed.password or parsed.query or parsed.fragment):
            raise ValueError("base_url must be an HTTP(S) API root without credentials, query or fragment")
        model_id = _required_text(model, "model_id")
        model_prices = prices.get("models", {}).get(model_id)
        price = None if model_prices is None else Price(
            _price_rate(model_prices, "input_per_million"),
            _price_rate(model_prices, "output_per_million"),
        )
        api_key = _required_text(model, "api_key")
        if "\n" in api_key or "\r" in api_key:
            raise ValueError("api_key must be a single line")
        return ModelConfig(base_url, api_key, model_id, _required_text(prices, "version"), price)
    except (AttributeError, KeyError, OSError, TypeError, ValueError) as error:
        raise ValueError(f"Model configuration {path}: {error}") from error


Transport = Callable[[str, str, dict[str, str], dict[str, Any] | None, float], dict[str, Any]]


def _http_transport(method, url, headers, payload, timeout):
    results: Queue = Queue(maxsize=1)

    def fetch():
        try:
            data = None if payload is None else json.dumps(payload).encode("utf-8")
            request = Request(url, data=data, method=method)
            for name, value in headers.items():
                request.add_unredirected_header(name, value)
            with urlopen(request, timeout=timeout) as response:
                results.put(json.load(response))
        except HTTPError as error:
            results.put(EndpointError(f"HTTP {error.code}"))
            error.close()
        except URLError as error:
            if isinstance(error.reason, TimeoutError):
                results.put(error.reason)
            else:
                results.put(EndpointError(f"connection failed: {error.reason}"))
        except (UnicodeError, ValueError, OSError) as error:
            results.put(error)

    Thread(target=fetch, daemon=True).start()
    try:
        response = results.get(timeout=timeout)
    except Empty:
        raise TimeoutError("model request timed out") from None
    if isinstance(response, Exception):
        raise response
    return response


@dataclass(frozen=True)
class CallUsage:
    input_tokens: int | None
    output_tokens: int | None
    estimated_amount: float | None
    model_seconds: float


@dataclass(frozen=True)
class Usage:
    steps: int
    input_tokens: int | None
    output_tokens: int | None
    estimated_amount: float | None
    model_seconds: float


@dataclass(frozen=True)
class Generation:
    state: Literal["done", "paused", "failed"]
    value: Any = None
    reason: str | None = None
    gaps: tuple[str, ...] = ()
    checkpoint: Path | None = None


class ModelClient:
    def __init__(
        self, config: ModelConfig, *, transport: Transport = _http_transport,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.config = config
        self._transport = transport
        self._clock = clock if clock is not None else perf_counter

    def _call(self, method: str, path: str, payload: dict | None, timeout: float) -> dict:
        headers = {"Authorization": f"Bearer {self.config.api_key}"}
        if payload is not None:
            headers["Content-Type"] = "application/json"
        response = self._transport(method, f"{self.config.base_url}/{path}", headers, payload, timeout)
        if not isinstance(response, dict):
            raise EndpointError("endpoint response must be a JSON object")
        return response

    def preflight(self, *, timeout: float = 5) -> None:
        """Check reachability and credentials once before an evaluation."""
        if not isfinite(timeout) or timeout <= 0:
            raise ValueError("preflight timeout must be positive and finite")
        try:
            response = self._call("GET", "models", None, timeout)
            models = response.get("data")
            if not isinstance(models, list) or not any(
                isinstance(model, dict) and model.get("id") == self.config.model_id
                for model in models
            ):
                raise EndpointError(f"configured model {self.config.model_id!r} was absent from /models")
        except (EndpointError, OSError, ValueError) as error:
            raise EndpointError(f"Model endpoint preflight failed: {error}") from error

    def session(
        self, *, max_steps: int, max_tokens: int, max_wall_seconds: float,
        checkpoint_dir: Path | None = None,
    ) -> "BudgetSession":
        if type(max_steps) is not int or type(max_tokens) is not int or min(max_steps, max_tokens) < 0:
            raise ValueError("step and token budgets must be non-negative integers")
        if (type(max_wall_seconds) not in (int, float) or
                not isfinite(max_wall_seconds) or max_wall_seconds < 0):
            raise ValueError("wall clock budget must be finite and non-negative")
        return BudgetSession(
            self, max_steps=max_steps, max_tokens=max_tokens, max_wall_seconds=max_wall_seconds,
            checkpoint_dir=checkpoint_dir or Path.home() / ".openkb" / "paused",
        )


def _invalid_constant(value: str) -> None:
    raise ValueError(f"{value} is not a JSON value")


class BudgetSession:
    def __init__(
        self, client: ModelClient, *, max_steps: int, max_tokens: int,
        max_wall_seconds: float, checkpoint_dir: Path,
    ) -> None:
        self._client = client
        self._max_steps = max_steps
        self._max_tokens = max_tokens
        self._max_wall_seconds = max_wall_seconds
        self._checkpoint_dir = checkpoint_dir
        self._deadline = client._clock() + max_wall_seconds
        self._steps = 0
        self._input_tokens = 0
        self._output_tokens = 0
        self._known_usage = True
        self._model_seconds = 0.0
        self._calls: list[CallUsage] = []
        self._terminal: Generation | None = None

    @property
    def calls(self) -> tuple[CallUsage, ...]:
        return tuple(self._calls)

    def _estimated_amount(self, input_tokens: int, output_tokens: int) -> float | None:
        price = self._client.config.price
        if price is None:
            return None
        return (
            input_tokens * price.input_per_million + output_tokens * price.output_per_million
        ) / 1_000_000

    @property
    def usage(self) -> Usage:
        if not self._known_usage:
            return Usage(self._steps, None, None, None, self._model_seconds)
        return Usage(
            self._steps, self._input_tokens, self._output_tokens,
            self._estimated_amount(self._input_tokens, self._output_tokens),
            self._model_seconds,
        )

    def _pause(self, reason: str, gaps: tuple[str, ...]) -> Generation:
        self._checkpoint_dir.mkdir(parents=True, exist_ok=True)
        path = self._checkpoint_dir / f"{uuid4().hex}.json"
        checkpoint = {
            "state": "paused",
            "reason": reason,
            "gaps": gaps,
            "model_id": self._client.config.model_id,
            "budget": {
                "max_steps": self._max_steps, "max_tokens": self._max_tokens,
                "max_wall_seconds": self._max_wall_seconds,
            },
            "usage": asdict(self.usage),
            "calls": [asdict(call) for call in self.calls],
        }
        with NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            try:
                json.dump(checkpoint, temporary, ensure_ascii=False, indent=2)
                temporary.flush()
                os.fsync(temporary.fileno())
                temporary.close()
                temporary_path.replace(path)
            finally:
                temporary.close()
                temporary_path.unlink(missing_ok=True)
        self._terminal = Generation("paused", reason=reason, gaps=gaps, checkpoint=path)
        return self._terminal

    def _fail(self, reason: str, gaps: tuple[str, ...]) -> Generation:
        self._terminal = Generation("failed", reason=reason, gaps=gaps)
        return self._terminal

    def _record_usage(self, response: dict, started: float) -> bool:
        elapsed = self._client._clock() - started
        self._model_seconds += elapsed
        usage = response.get("usage")
        if isinstance(usage, dict):
            counts = (usage.get("prompt_tokens"), usage.get("completion_tokens"))
            if all(type(value) is int and value >= 0 for value in counts):
                input_tokens, output_tokens = counts
                self._input_tokens += input_tokens
                self._output_tokens += output_tokens
                self._calls.append(CallUsage(
                    input_tokens, output_tokens,
                    self._estimated_amount(input_tokens, output_tokens), elapsed,
                ))
                return True
        self._calls.append(CallUsage(None, None, None, elapsed))
        self._known_usage = False
        return False

    def generate(
        self, prompt: str, schema: dict, *, gaps: tuple[str, ...], max_attempts: int = 3,
    ) -> Generation:
        """Request and validate JSON. Each transport attempt consumes one step."""
        if self._terminal is not None:
            return self._terminal
        if type(max_attempts) is not int or max_attempts < 1:
            raise ValueError("max_attempts must be a positive integer")
        validator_class = validator_for(schema)
        validator_class.check_schema(schema)
        validator = validator_class(schema)
        instructions = (
            "Return only JSON matching this schema.\n"
            f"{json.dumps(schema, ensure_ascii=False)}\n\n{prompt}"
        )
        messages = [{"role": "user", "content": instructions}]
        for attempt in range(max_attempts):
            timeout = self._deadline - self._client._clock()
            if timeout <= 0:
                return self._pause("wall clock budget exhausted", gaps)
            if self._steps >= self._max_steps:
                return self._pause("step budget exhausted", gaps)

            # Reserve one token per UTF-8 byte plus common chat framing.
            prompt_reserve = len(messages[0]["content"].encode("utf-8")) + 64
            remaining = self._max_tokens - self._input_tokens - self._output_tokens
            completion_cap = remaining - prompt_reserve
            if completion_cap <= 0:
                return self._pause("token budget exhausted", gaps)
            payload = {
                "model": self._client.config.model_id, "messages": messages,
                "max_completion_tokens": completion_cap,
            }
            self._steps += 1
            started = self._client._clock()
            try:
                response = self._client._call("POST", "chat/completions", payload, timeout)
            except TimeoutError:
                self._record_usage({}, started)
                return self._pause("wall clock budget exhausted", gaps)
            except (EndpointError, OSError, ValueError) as error:
                self._record_usage({}, started)
                return self._fail(f"Model request failed: {error}", gaps)
            usage_reported = self._record_usage(response, started)
            if self._client._clock() >= self._deadline:
                return self._pause("wall clock budget exhausted", gaps)
            if not usage_reported:
                return self._fail("Model response lacks valid token usage", gaps)
            if self._input_tokens + self._output_tokens > self._max_tokens:
                return self._pause("token budget exhausted", gaps)

            try:
                content = response["choices"][0]["message"]["content"]
                value = json.loads(content, parse_constant=_invalid_constant)
                validator.validate(value)
            except (IndexError, KeyError, TypeError, ValueError, ValidationError):
                if attempt + 1 == max_attempts:
                    return self._fail("Model response did not match JSON Schema", gaps)
                messages = [{
                    "role": "user",
                    "content": "The previous output was invalid. Follow the schema exactly.\n" + instructions,
                }]
            else:
                if self._client._clock() >= self._deadline:
                    return self._pause("wall clock budget exhausted", gaps)
                return Generation("done", value=value)
