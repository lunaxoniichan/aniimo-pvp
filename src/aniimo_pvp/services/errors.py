class UserError(Exception):
    """A problem caused by the user's action; the message is shown to them privately."""


class NotAllowed(UserError):
    pass
