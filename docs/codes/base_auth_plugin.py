@dataclass(frozen=True)
class TokenPair:
    access_token: str
    refresh_token: str


@dataclass(frozen=True)
class TokenPayload:
    user_id: UUID
    organization_id: UUID | None
    is_staff: bool
    is_superadmin: bool
    jti: str
    token_type: str


class BaseAuthPlugin(abc.ABC):
    """Contrato que debe cumplir cualquier mecanismo de autenticacion."""

    @abc.abstractmethod
    def authenticate(self, email: str, password: str) -> TokenPair: ...

    @abc.abstractmethod
    def refresh(self, refresh_token: str) -> TokenPair: ...

    @abc.abstractmethod
    def revoke(self, refresh_token: str) -> None: ...

    @abc.abstractmethod
    def validate_access_token(self, token: str) -> TokenPayload: ...
