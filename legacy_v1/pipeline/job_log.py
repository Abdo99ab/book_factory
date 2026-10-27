import json
import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)


def _log_path(job_dir: Path) -> Path:
    return job_dir / "logs" / "events.jsonl"


def ensure_log_dir(job_dir: Path):
    (job_dir / "logs").mkdir(parents=True, exist_ok=True)


def append_event(job_dir: Path, event_type: str, message: str, **payload):
    """Append a structured event to the job audit log."""
    # PARKED: data protection (spec §14) - event payloads can hold child data (names, inputs); redaction/retention goes here
    ensure_log_dir(job_dir)
    event = {
        "ts": time.time(),
        "iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "type": event_type,
        "message": message,
        **payload,
    }
    path = _log_path(job_dir)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=True) + "\n")
    logger.info("[job %s] %s: %s", job_dir.name, event_type, message)
    return event


def read_events(job_dir: Path, limit=500):
    path = _log_path(job_dir)
    if not path.is_file():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    events = []
    for line in lines[-limit:]:
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


def write_json(path: Path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=True)
        handle.write("\n")
    temporary.replace(path)


def read_json(path: Path, default=None):
    if not path.is_file():
        return default
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)
