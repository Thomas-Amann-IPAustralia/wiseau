"""CPU usage for the UI's meter: `GET /usage` (ADR-058).

Two sources, reported side by side, each saying what it is:

* **The deployment, this month** — Cloud Run's own
  `run.googleapis.com/container/billable_instance_time`, summed over every
  instance of this service since the first of the month (UTC), times the vCPUs
  each instance has. That is the figure Cloud Run bills against its free
  allowance, and it survives scale-to-zero and restarts because Google keeps it.
  It is read from the Cloud Monitoring API with the service's own identity (the
  metadata server's token), so it needs no secret — only that the service
  account may read monitoring data (`roles/monitoring.viewer`; the default
  compute account usually can). Off Cloud Run, or without that permission, it is
  `null` with the reason.
* **This process** — CPU seconds the container has used (`time.process_time`)
  and seconds it has spent with at least one conversion running, since it
  started. Always available; resets whenever Cloud Run starts a new instance.

Nothing here touches a conversion: it is read only when `/usage` is asked, the
Monitoring answer is cached, and a failure is a field in the response.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import math
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

logger = logging.getLogger(__name__)

_METADATA = "http://metadata.google.internal/computeMetadata/v1/"
_MONITORING = "https://monitoring.googleapis.com/v3/projects/{project}/timeSeries"
_METRIC = "run.googleapis.com/container/billable_instance_time"

# A month's reading moves slowly and Monitoring lags a few minutes anyway.
_CACHE_SECONDS = 300
# After a failure, wait before asking again rather than retrying on every call.
_FAILURE_CACHE_SECONDS = 600


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except ValueError:
        return default


def vcpus() -> float:
    """vCPUs per instance (`WISEAU_VCPUS`): Cloud Run bills instance time × vCPUs."""
    return max(0.1, _float_env("WISEAU_VCPUS", 1.0))


def allowance_vcpu_seconds() -> float:
    """The monthly vCPU-seconds the meter measures against (`WISEAU_FREE_VCPU_SECONDS`).

    Defaults to 180,000 — Cloud Run's free tier for request-based billing when
    this was written. Pricing changes; set the variable to what your account has.
    """
    return max(0.0, _float_env("WISEAU_FREE_VCPU_SECONDS", 180_000.0))


def month_start(now: dt.datetime) -> dt.datetime:
    return now.astimezone(dt.timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _iso(moment: dt.datetime) -> str:
    return moment.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --- This process ------------------------------------------------------------
class ProcessUsage:
    """Busy time (≥1 conversion running) and CPU time of this process."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._started = time.monotonic()
        self._running = 0
        self._busy_since = 0.0
        self._busy_total = 0.0
        self._jobs = 0

    def job_started(self) -> None:
        with self._lock:
            if self._running == 0:
                self._busy_since = time.monotonic()
            self._running += 1

    def job_finished(self) -> None:
        with self._lock:
            self._running = max(0, self._running - 1)
            self._jobs += 1
            if self._running == 0:
                self._busy_total += time.monotonic() - self._busy_since

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            busy = self._busy_total + (time.monotonic() - self._busy_since if self._running else 0.0)
            return {
                "cpu_seconds": round(time.process_time(), 1),
                "busy_seconds": round(busy, 1),
                "uptime_seconds": round(time.monotonic() - self._started, 1),
                "jobs": self._jobs,
            }


process_usage = ProcessUsage()


# --- The deployment (Cloud Monitoring) ----------------------------------------
Fetch = Callable[[urllib.request.Request], bytes]


def _default_fetch(request: urllib.request.Request) -> bytes:
    with urllib.request.urlopen(request, timeout=4) as response:  # noqa: S310 - fixed Google hosts
        return response.read()


def _metadata(fetch: Fetch, path: str) -> str:
    request = urllib.request.Request(_METADATA + path, headers={"Metadata-Flavor": "Google"})
    return fetch(request).decode("utf-8").strip()


def query_billable_seconds(service: str, project: str, token: str, start: dt.datetime,
                           end: dt.datetime, fetch: Fetch = _default_fetch) -> float:
    """Billable instance seconds for `service` between `start` and `end`, all revisions."""
    window = max(60, math.ceil((end - start).total_seconds() / 60) * 60)
    params = {
        "filter": (
            f'metric.type="{_METRIC}" AND resource.type="cloud_run_revision" '
            f'AND resource.labels.service_name="{service}"'
        ),
        "interval.startTime": _iso(start),
        "interval.endTime": _iso(end),
        "aggregation.alignmentPeriod": f"{window}s",
        "aggregation.perSeriesAligner": "ALIGN_SUM",
        "aggregation.crossSeriesReducer": "REDUCE_SUM",
    }
    total = 0.0
    page_token = ""
    for _ in range(20):  # pages; one is the norm after REDUCE_SUM
        query = dict(params, pageToken=page_token) if page_token else params
        url = _MONITORING.format(project=urllib.parse.quote(project)) + "?" + urllib.parse.urlencode(query)
        request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        body = json.loads(fetch(request).decode("utf-8") or "{}")
        for series in body.get("timeSeries", []):
            for point in series.get("points", []):
                value = point.get("value", {})
                total += float(value.get("doubleValue", value.get("int64Value", 0)) or 0)
        page_token = body.get("nextPageToken", "")
        if not page_token:
            break
    return total


class DeploymentUsage:
    """The month's billable vCPU-seconds from Cloud Monitoring, cached."""

    def __init__(self, fetch: Fetch = _default_fetch, clock: Callable[[], float] = time.monotonic) -> None:
        self._fetch = fetch
        self._clock = clock
        self._lock = threading.Lock()
        self._cached: dict[str, Any] | None = None
        self._expires = 0.0

    def read(self, now: dt.datetime | None = None) -> dict[str, Any]:
        with self._lock:
            if self._cached is not None and self._clock() < self._expires:
                return self._cached
        result, ttl = self._query(now or dt.datetime.now(dt.timezone.utc))
        with self._lock:
            self._cached, self._expires = result, self._clock() + ttl
        return result

    def _query(self, now: dt.datetime) -> tuple[dict[str, Any], float]:
        if os.environ.get("WISEAU_USAGE_MONITORING", "auto").strip().lower() in {"0", "off", "false", "no"}:
            return {"source": None, "reason": "Turned off (WISEAU_USAGE_MONITORING=off)."}, _FAILURE_CACHE_SECONDS
        service = os.environ.get("K_SERVICE", "").strip()
        if not service:
            return {"source": None, "reason": "Not running on Cloud Run."}, _FAILURE_CACHE_SECONDS
        start = month_start(now)
        try:
            project = os.environ.get("GOOGLE_CLOUD_PROJECT", "").strip() or _metadata(self._fetch, "project/project-id")
            token = json.loads(_metadata(self._fetch, "instance/service-accounts/default/token"))["access_token"]
            seconds = query_billable_seconds(service, project, token, start, now, self._fetch)
        except urllib.error.HTTPError as exc:
            reason = (
                "The service account may not read monitoring data — grant it roles/monitoring.viewer."
                if exc.code in (401, 403)
                else f"Cloud Monitoring answered HTTP {exc.code}."
            )
            logger.warning("usage: Cloud Monitoring query failed", extra={"wiseau": {"status": exc.code}})
            return {"source": None, "reason": reason}, _FAILURE_CACHE_SECONDS
        except Exception as exc:  # noqa: BLE001 - the meter is an extra
            logger.warning("usage: Cloud Monitoring unavailable", extra={"wiseau": {"error": str(exc)}})
            return {"source": None, "reason": "Cloud Monitoring could not be reached."}, _FAILURE_CACHE_SECONDS
        return {
            "source": "cloud_monitoring",
            "billable_instance_seconds": round(seconds, 1),
            "vcpu_seconds": round(seconds * vcpus(), 1),
            "as_of": _iso(now),
        }, _CACHE_SECONDS


deployment_usage = DeploymentUsage()


def snapshot(now: dt.datetime | None = None) -> dict[str, Any]:
    """The `/usage` body."""
    moment = now or dt.datetime.now(dt.timezone.utc)
    return {
        "period_start": _iso(month_start(moment)),
        "vcpus": vcpus(),
        "allowance_vcpu_seconds": allowance_vcpu_seconds(),
        "deployment": deployment_usage.read(moment),
        "process": process_usage.snapshot(),
    }
