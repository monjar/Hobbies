class ClipforgeError(Exception):
    """Base class. Every pipeline failure is one of these and carries a reason."""


class ConfigError(ClipforgeError):
    pass


class PolicyDenied(ClipforgeError):
    pass


class IllegalTransition(ClipforgeError):
    pass


class CostCapExceeded(ClipforgeError):
    pass


class ProviderError(ClipforgeError):
    pass


class ValidationFailed(ClipforgeError):
    def __init__(self, errors: list[str]):
        self.errors = list(errors)
        super().__init__("; ".join(self.errors))


class SpoilerRejected(ClipforgeError):
    pass


class MediaError(ClipforgeError):
    pass
