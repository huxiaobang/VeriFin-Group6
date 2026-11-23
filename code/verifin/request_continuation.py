"""Durable handoffs for a single research request."""
from .latest_quarter import acquire


def restore_acquisition(start, receipts, scope):
    """Rebuild the local fiscal-quarter iterator from its already saved observations."""
    if start is None:
        return None, None
    acquisition = acquire(*start)
    step = next(acquisition)
    for result in receipts:
        step = acquisition.send(result)
    if scope is not None and not receipts:
        step["scope"] = scope
    return acquisition, step


class RequestContinuation:
    def __init__(self, store, sid, request_id):
        self.store, self.sid, self.request_id = store, sid, request_id
        self.input_revision = None

    def save(self, episode, episode_checkpoint, bootstrap, stage, state):
        """Bind the next action to the commit of the observation it depends on."""
        state = state.copy()
        work = state["work"]
        if work is not None and work.get("selected"):
            state["work"] = {**work, "selected": [[side, metric, row] for (side, metric), row in work["selected"].items()]}
        event = self.store.add_event(self.sid, {
            "type": "python_checkpoint", "version": 1,
            "request_id": self.request_id, "episode_id": episode["id"], "stage": stage,
        })
        self.input_revision = self.store.research_revision(self.sid)
        self.store.save_research_handoff(self.sid, self.request_id, {
            "stage": stage, "bootstrap": bootstrap, "state": state,
            "checkpoint_sequence": event["event_sequence"],
            "episode_checkpoint": episode_checkpoint["event_sequence"],
            "input_revision": self.input_revision,
        })

    def inputs_current(self):
        return self.store.research_revision(self.sid) == self.input_revision

    def attempt(self, episode, kind, operation):
        frame = self.store.latest_event(self.sid, "python_checkpoint")
        self.store.add_event(self.sid, {
            "type": "python_attempt", "request_id": self.request_id, "episode_id": episode["id"],
            "checkpoint_sequence": frame["event_sequence"], "kind": kind, "operation": operation,
        })

    def restore(self, episode, engine_settings):
        with self.store.atomic_step():
            frame = self.store.latest_event(self.sid, "python_checkpoint")
            if (not frame or frame.get("request_id") != self.request_id
                    or frame["episode_id"] != episode["id"] or frame["version"] != 1):
                return None, "interrupted"
            resume = self.store.get_research_handoff(self.sid, self.request_id)
            if (not resume or resume["checkpoint_sequence"] != frame["event_sequence"]
                    or resume["stage"] not in {"plan", "route", "tool", "research", "critic", "reviewed", "deliver"}):
                return None, "interrupted"
            attempt = self.store.latest_event(self.sid, "python_attempt")
            if attempt and attempt.get("checkpoint_sequence") == frame["event_sequence"]:
                self.store.add_event(self.sid, {
                    "type": "python_resume_blocked", "request_id": self.request_id,
                    "episode_id": episode["id"], "stage": frame["stage"], "kind": attempt["kind"],
                    "operation": attempt["operation"], "reason": "unknown_external_outcome",
                })
                return None, "unknown_external_outcome"
            self.input_revision = resume["input_revision"]
            if not self.inputs_current():
                return None, "role_inputs_changed"
            if resume["bootstrap"]["engine_settings"] != engine_settings:
                raise ValueError("Resume requires the original research controller and source settings.")
            snapshot = self.store.event_at(self.sid, resume["episode_checkpoint"])
            if not snapshot or snapshot.get("request_id") != self.request_id:
                return None, "interrupted"
            resume["episode"] = snapshot["episode"]
            work = resume["state"]["work"]
            if work is not None and work.get("selected"):
                work["selected"] = {(side, metric): row for side, metric, row in work["selected"]}
            self.store.add_event(self.sid, {
                "type": "python_resume", "request_id": self.request_id,
                "episode_id": episode["id"], "stage": frame["stage"],
            })
            return resume, None
