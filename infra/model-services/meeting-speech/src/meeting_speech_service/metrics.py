from __future__ import annotations

from collections import Counter
from threading import Lock


class Metrics:
    """Small dependency-free Prometheus collector with low-cardinality labels."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._counters: Counter[tuple[str, str]] = Counter()
        self._active_sessions = 0
        self._resident_sessions = 0
        self._partial_latency_sum = 0.0
        self._partial_latency_count = 0
        self._final_latency_sum = 0.0
        self._final_latency_count = 0

    def increment(self, metric: str, result: str, amount: int = 1) -> None:
        with self._lock:
            self._counters[(metric, result)] += amount

    def set_sessions(self, *, active: int, resident: int) -> None:
        with self._lock:
            self._active_sessions = active
            self._resident_sessions = resident

    def observe_asr_latency(self, kind: str, seconds: float) -> None:
        with self._lock:
            if kind == "partial":
                self._partial_latency_sum += seconds
                self._partial_latency_count += 1
            else:
                self._final_latency_sum += seconds
                self._final_latency_count += 1

    def render(self, *, asr_ready: bool, adapter: str) -> str:
        with self._lock:
            counters = dict(self._counters)
            lines = [
                "# TYPE meeting_speech_active_sessions gauge",
                f"meeting_speech_active_sessions {self._active_sessions}",
                "# TYPE meeting_speech_resident_sessions gauge",
                f"meeting_speech_resident_sessions {self._resident_sessions}",
                "# TYPE meeting_speech_asr_ready gauge",
                f"meeting_speech_asr_ready {1 if asr_ready else 0}",
                "# TYPE meeting_speech_adapter_info gauge",
                f'meeting_speech_adapter_info{{adapter="{adapter}"}} 1',
                "# TYPE meeting_speech_audio_frame_total counter",
            ]
            for (_, result), count in sorted(counters.items()):
                lines.append(f'meeting_speech_audio_frame_total{{result="{result}"}} {count}')
            lines.extend(
                [
                    "# TYPE meeting_speech_asr_partial_latency_seconds summary",
                    f"meeting_speech_asr_partial_latency_seconds_sum {self._partial_latency_sum:.9f}",
                    f"meeting_speech_asr_partial_latency_seconds_count {self._partial_latency_count}",
                    "# TYPE meeting_speech_asr_final_latency_seconds summary",
                    f"meeting_speech_asr_final_latency_seconds_sum {self._final_latency_sum:.9f}",
                    f"meeting_speech_asr_final_latency_seconds_count {self._final_latency_count}",
                ]
            )
        return "\n".join(lines) + "\n"
