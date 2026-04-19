"""HotWire network-interface helpers.

Checkpoint 15: psutil-backed enumeration + scoring so the GUI picker
can rank likely-to-be-PLC-modem NICs at the top. See
:mod:`hotwire.net.interfaces` for the entry point.
"""

from .interfaces import NetInterface, list_interfaces

__all__ = ["NetInterface", "list_interfaces"]
