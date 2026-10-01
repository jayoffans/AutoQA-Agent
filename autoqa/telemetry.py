import logging
import os
from contextlib import contextmanager, nullcontext

from .security import redact

log = logging.getLogger(__name__)


class Telemetry:
    def __init__(self, enabled=True):
        self.client = None
        if enabled and os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY"):
            from langfuse import get_client
            self.client = get_client()

    @contextmanager
    def span(self, name: str, data: dict, generation: bool = False):
        context = nullcontext(None)
        if self.client:
            try:
                context = self.client.start_as_current_observation(
                    name=name, as_type="generation" if generation else "span", input=redact(data)
                )
            except Exception:
                log.warning("Langfuse observation could not start", exc_info=False)
        with context as observation:
            yield observation

    def flush(self):
        if self.client:
            self.client.flush()
