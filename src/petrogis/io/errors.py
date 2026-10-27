"""Exceptions raised by readers. Messages are written to be shown to the user as-is."""


class DataReadError(Exception):
    """A data source could not be read. The message explains why in user terms."""


class MissingCRSError(DataReadError):
    """The source has no CRS and none was supplied. The UI should ask the user for one."""
