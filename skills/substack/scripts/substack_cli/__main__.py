"""Allow `python -m substack_cli` invocation."""
from substack_cli import config as _config  # noqa: F401
from substack_cli import read as _read      # noqa: F401
from substack_cli import publish as _publish  # noqa: F401
from substack_cli import manage as _manage    # noqa: F401
from substack_cli import notes as _notes      # noqa: F401
from substack_cli import chat as _chat        # noqa: F401

from substack_cli.app import main

main()
