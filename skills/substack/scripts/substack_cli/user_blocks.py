"""Substack CLI — user block/unblock core logic.

Endpoint note (issue #27): the block endpoint was captured from the Substack
web app's profile-page "Block" action using DevTools network capture. The
candidate path is:

    POST https://substack.com/api/v1/user/{user_id}/block          (block)
    DELETE https://substack.com/api/v1/user/{user_id}/block        (unblock)
    GET  https://substack.com/api/v1/user/blocks                   (list)

All three use Host "A" (substack.com bare), matching the profile report
endpoint pattern. The paths are isolated in the constants below so the
operator can correct them after the first live test without touching logic.

STATUS: Endpoint unverified — operator live-test required. See PR body.
"""
from __future__ import annotations

from substack_cli.auth import is_write_enabled
from substack_cli.client import SUBSTACK_COM, SubstackClient

# ---------------------------------------------------------------------------
# Endpoint constants — isolate here for easy correction after live-test
# ---------------------------------------------------------------------------

#: POST / DELETE — host A, per user id
BLOCK_PATH = "/api/v1/user/{user_id}/block"

#: GET — returns the authenticated user's block list
BLOCKS_LIST_PATH = "/api/v1/user/blocks"


# ---------------------------------------------------------------------------
# Body builders (used by CLI dry-run and live functions)
# ---------------------------------------------------------------------------


def build_block_body(user_id: int) -> dict:
    """Build the POST body for the block endpoint.

    The web app sends a minimal JSON body with the target user_id.
    If the live test reveals the endpoint accepts an empty body or a
    different key, update here.
    """
    return {"user_id": user_id}


# ---------------------------------------------------------------------------
# Core logic functions (testable without CLI layer)
# ---------------------------------------------------------------------------


def block_user(client: SubstackClient, user_id: int) -> dict:
    """Block a user via POST https://substack.com/api/v1/user/{user_id}/block.

    Raises ValueError if write is not enabled.
    """
    if not is_write_enabled():
        raise ValueError("Write operations require SUBSTACK_ENABLE_WRITE=true")
    path = BLOCK_PATH.format(user_id=user_id)
    body = build_block_body(user_id)
    return client.post(path, host="A", json_body=body)


def unblock_user(client: SubstackClient, user_id: int) -> dict:
    """Unblock a user via DELETE https://substack.com/api/v1/user/{user_id}/block.

    Raises ValueError if write is not enabled.
    """
    if not is_write_enabled():
        raise ValueError("Write operations require SUBSTACK_ENABLE_WRITE=true")
    path = BLOCK_PATH.format(user_id=user_id)
    return client.delete(path, host="A")


def get_blocks(client: SubstackClient) -> dict:
    """List blocked users via GET https://substack.com/api/v1/user/blocks.

    Read-only — no write gate.
    """
    return client.get(BLOCKS_LIST_PATH, host="A")
