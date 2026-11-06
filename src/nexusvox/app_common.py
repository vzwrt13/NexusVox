"""What the parts of `NexusVoxApp` share."""

from __future__ import annotations

import logging

# All parts log under this one name, so a log line does not depend on which module a method sits in.
logger = logging.getLogger("nexusvox.app")

# A polish chord held shorter than this is a tap (nothing was said); longer means an instruction that a
# missing server would lose, so the selection is left alone.
QUICK_TAP_S = 0.6
