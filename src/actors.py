"""Warmo's durable mission memory (Terse durable actor).

The brain (brain.py) is stateless: every step it reads this actor, decides one move,
then records the result here. Kill the brain or the Terse server mid-hunt, restart,
and the mission picks up exactly where it left off.
"""
from durable_actors import Actor, persisted


class Mission(Actor):
    goal: str = persisted("")
    phase: str = persisted("idle")          # idle | searching | approaching | ramming | done
    step_no: int = persisted(0)
    search_turned_deg: float = persisted(0.0)  # how far we've spun while searching
    last_seen_step: int = persisted(-1)
    last_bearing_deg: float = persisted(0.0)
    last_distance_cm: float = persisted(0.0)
    log: list[str] = persisted([])          # last 20 actions, newest last
    resumes: int = persisted(0)
    # patrol mode memory
    sightings: list[str] = persisted([])    # last 12 things Warmo said it saw, with heading
    est_heading_deg: float = persisted(0.0)  # dead-reckoned heading since mission start
    driven_cm: float = persisted(0.0)

    def record_patrol(self, action: str, saw: str, turned_deg: float, moved_cm: float) -> int:
        """Save one patrol step: what it saw and what it did."""
        self.step_no += 1
        self.phase = "patrolling"
        seen_heading_deg = self.est_heading_deg
        self.est_heading_deg = (self.est_heading_deg + turned_deg) % 360
        self.driven_cm += moved_cm
        if saw:
            self.sightings = (self.sightings + [f"#{self.step_no} @{round(seen_heading_deg)}deg: {saw}"])[-12:]
        self.log = (self.log + [f"#{self.step_no} {action}"])[-20:]
        return self.step_no

    def start(self, goal: str) -> str:
        """Begin a mission. No-op if one is already running (that's what makes resume work)."""
        if self.phase not in ("idle", "done"):
            self.resumes += 1
            return f"resuming:{self.phase}:step{self.step_no}"
        self.goal = goal
        self.phase = "patrolling" if goal.lower().startswith("patrol") else "searching"
        self.step_no = 0
        self.search_turned_deg = 0.0
        self.last_seen_step = -1
        self.sightings, self.est_heading_deg, self.driven_cm = [], 0.0, 0.0
        self.log = [f"mission start: {goal}"]
        return "started"

    def record(self, action: str, phase: str, visible: bool, bearing_deg: float,
               distance_cm: float, turned_deg: float) -> int:
        """Save the result of one step. Returns the new step number."""
        self.step_no += 1
        self.phase = phase
        if visible:
            self.last_seen_step = self.step_no
            self.last_bearing_deg = bearing_deg
            self.last_distance_cm = distance_cm
            self.search_turned_deg = 0.0
        else:
            self.search_turned_deg += turned_deg
        self.log = (self.log + [f"#{self.step_no} {action}"])[-20:]
        return self.step_no

    def state(self) -> dict[str, str]:
        return {
            "goal": self.goal,
            "phase": self.phase,
            "step_no": str(self.step_no),
            "search_turned_deg": str(round(self.search_turned_deg)),
            "last_seen_step": str(self.last_seen_step),
            "last_bearing_deg": str(self.last_bearing_deg),
            "last_distance_cm": str(self.last_distance_cm),
            "resumes": str(self.resumes),
            "recent_log": " | ".join(self.log[-8:]),
            "est_heading_deg": str(round(self.est_heading_deg)),
            "driven_cm": str(round(self.driven_cm)),
            "sightings": " | ".join(self.sightings),
        }

    def reset(self) -> str:
        self.goal, self.phase, self.step_no = "", "idle", 0
        self.search_turned_deg, self.last_seen_step = 0.0, -1
        self.last_bearing_deg, self.last_distance_cm = 0.0, 0.0
        self.log, self.resumes = [], 0
        self.sightings, self.est_heading_deg, self.driven_cm = [], 0.0, 0.0
        return "reset"
