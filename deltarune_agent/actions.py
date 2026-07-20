from dataclasses import dataclass


@dataclass(frozen=True)
class Action:
    name: str
    keys: tuple[str, ...] = ()
    duration: float = 0.08
    cooldown: float = 0.18
    continuous: bool = False


WAIT = Action("wait", duration=0.08, cooldown=0.04)
ACTIONS = {
    "wait": WAIT,
    "confirm": Action("confirm", ("z",), 0.05, 0.18),
    "cancel": Action("cancel", ("x",), 0.05, 0.18),
    "menu": Action("menu", ("c",), 0.05, 0.18),
    "up": Action("up", ("up",), 0.10, 0.0, continuous=True),
    "down": Action("down", ("down",), 0.10, 0.0, continuous=True),
    "left": Action("left", ("left",), 0.10, 0.0, continuous=True),
    "right": Action("right", ("right",), 0.10, 0.0, continuous=True),
}
