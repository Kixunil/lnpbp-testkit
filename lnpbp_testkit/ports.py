import fcntl
import os
from pathlib import Path
from xdg.BaseDirectory import save_data_path
import typing

def allocate(count: int = 1) -> typing.Iterable:
    state_dir = Path(save_data_path("lnpbp-testkit"))
    state_path = state_dir.joinpath("ports")
    os.makedirs(state_dir, exist_ok=True)
    state_fd = os.open(state_path, os.O_RDWR | os.O_CREAT)
    with os.fdopen(state_fd, "r+") as state_file:
        fcntl.flock(state_file, fcntl.LOCK_EX)
        state = state_file.read()
        if len(state) == 0:
            last_port = 60000
        else:
            last_port = int(state)

        next_port = last_port + count
        tmp_state_path = state_path.with_suffix(".tmp")
        with open(tmp_state_path, "w") as tmp_state:
            tmp_state.write(str(next_port))
            tmp_state.flush()
            os.fdatasync(tmp_state.fileno())

        os.rename(tmp_state_path, state_path)
        fcntl.flock(state_file, fcntl.LOCK_UN)

    return range(last_port, next_port)
