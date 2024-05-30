from enum import Enum, auto
from tabulate import tabulate


class StateType(Enum):
    """Enumeration of state types."""
    PENDING = auto()
    RUNNING = auto()
    FAILED = auto()
    COMPLETED = auto()


class State:
    def __init__(self):
        self.headers = ["TASK", StateType.PENDING.name, StateType.RUNNING.name, StateType.FAILED.name,
                        StateType.COMPLETED.name, "TIME"]
        self.state_dict = {}

    def set_state(self, func, input_state, run_time=""):
        self.state_dict[func.__name__] = {input_state.value: "\u25CB", (len(self.headers) - 1): run_time}

    @property
    def state(self):
        state_list = []
        for key, values in self.state_dict.items():
            states = [key] + [""] * (len(self.headers) - 1)
            for sub_key, sub_value in values.items():
                states[sub_key] = sub_value
            state_list.append(states)
        state_table = tabulate(state_list, headers=self.headers, tablefmt="grid")
        return state_table

    def __repr__(self):
        return self.state


# Global State
state = State()
