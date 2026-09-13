"""Authoritative command records accepted by Finished Cut Production."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

#: ADR-067 之後本模組只產長片；短片整條線走 `shortform-cut`。這裡不留第二個值，
#: 就沒有任何地方需要「檢查它是不是 long」——型別已經說完了。
Format = Literal["long"]
_COMMAND_ID_RE = re.compile(r"^(?:approved-cut|targeted-revision):[0-9a-f]{32}$")


class CommandRejectedError(ValueError):
    """A command ID did not resolve through an authoritative command store."""


@dataclass(frozen=True, slots=True)
class ApprovedCutCommand:
    """An approved-cut-store record, not a caller-supplied production payload."""

    command_id: str
    episode_id: str
    cut_id: str
    format: Format
    editorial_master_id: str
    winner_id: str
    tight_cut_id: str


@dataclass(frozen=True, slots=True)
class TargetedRevisionCommand:
    """A module-minted revision scoped to one event of exact current."""

    command_id: str
    current_plan_id: str
    episode_id: str
    cut_id: str
    format: Format
    event_id: str
    feedback: str


def _is_authoritative_command_id(value: str) -> bool:
    return bool(_COMMAND_ID_RE.fullmatch(value))


def _is_authoritative_approved_cut(command: ApprovedCutCommand, command_id: str) -> bool:
    identities = (
        command.episode_id,
        command.cut_id,
        command.editorial_master_id,
        command.winner_id,
        command.tight_cut_id,
    )
    return (
        command.command_id == command_id
        and command_id.startswith("approved-cut:")
        and command.format == "long"
        and all(_is_identity(value) for value in identities)
    )


def _is_identity(value: str) -> bool:
    return (
        isinstance(value, str)
        and value == value.strip()
        and 0 < len(value) <= 256
        and not any(character in value for character in "/\\{}[]\r\n\t")
    )
