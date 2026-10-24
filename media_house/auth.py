"""Provider-neutral authentication and tenant-authorization contracts.

This module deliberately does not implement login, token parsing, password handling, or
an identity-provider integration. A trusted authentication adapter must first produce a
``VerifiedIdentity``; only then can application code create a ``Principal``.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from typing import Protocol

from .errors import AuthenticationError, AuthorizationError, ValidationError
from .timeutil import parse_utc_timestamp


Permission = str


def _identifier(value: str, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValidationError(f"{field_name} must be a non-empty string")
    result = value.strip()
    if not result or any(ord(character) < 32 for character in result):
        raise ValidationError(f"{field_name} must be a non-empty printable string")
    return result


def _values(values: Iterable[str], *, field_name: str) -> frozenset[str]:
    if isinstance(values, (str, bytes)):
        raise ValidationError(f"{field_name} must be a collection of strings")
    return frozenset(_identifier(value, field_name=field_name) for value in values)


class AuthenticationBoundaryError(ValidationError):
    """A caller attempted to bypass the trusted authentication boundary."""


class VerifiedIdentity:
    """Identity evidence created only by a trusted authentication verifier.

    The constructor is intentionally unavailable to request handlers. A real provider
    adapter is responsible for validating the credential, signature, issuer, audience,
    and time claims before calling the private factory. The factory is an explicit
    composition seam for adapters and synthetic tests; it is not token verification.
    """

    __slots__ = (
        "_actor_id",
        "_roles",
        "_permissions",
        "_authentication_source",
        "_issuer",
        "_audience",
        "_expires_at",
        "_not_before",
        "_verified",
    )

    def __init__(self, *args: object, **kwargs: object) -> None:
        del args, kwargs
        raise AuthenticationBoundaryError(
            "VerifiedIdentity must be created by a trusted authentication verifier"
        )

    @classmethod
    def _from_trusted_verifier(
        cls,
        *,
        actor_id: str,
        roles: Iterable[str],
        permissions: Iterable[str],
        authentication_source: str,
        issuer: str,
        audience: str,
        expires_at: str,
        not_before: str | None = None,
        verified: bool = True,
        _proof: object,
    ) -> "VerifiedIdentity":
        if _proof is not _TRUSTED_AUTHENTICATION_PROOF:
            raise AuthenticationBoundaryError("authentication evidence was not produced by a trusted verifier")
        if verified is not True:
            raise AuthenticationBoundaryError("authentication evidence must be marked verified")
        identity = object.__new__(cls)
        object.__setattr__(identity, "_actor_id", _identifier(actor_id, field_name="actor_id"))
        object.__setattr__(identity, "_roles", _values(roles, field_name="roles"))
        object.__setattr__(identity, "_permissions", _values(permissions, field_name="permissions"))
        object.__setattr__(
            identity,
            "_authentication_source",
            _identifier(authentication_source, field_name="authentication_source"),
        )
        object.__setattr__(identity, "_issuer", _identifier(issuer, field_name="issuer"))
        object.__setattr__(identity, "_audience", _identifier(audience, field_name="audience"))
        object.__setattr__(identity, "_expires_at", _timestamp(expires_at, field_name="expires_at"))
        object.__setattr__(
            identity,
            "_not_before",
            None if not_before is None else _timestamp(not_before, field_name="not_before"),
        )
        object.__setattr__(identity, "_verified", True)
        return identity

    def __setattr__(self, name: str, value: object) -> None:
        del value
        raise AttributeError(f"{type(self).__name__} is immutable")

    @property
    def actor_id(self) -> str:
        return self._actor_id

    @property
    def roles(self) -> frozenset[str]:
        return self._roles

    @property
    def permissions(self) -> frozenset[str]:
        return self._permissions

    @property
    def authentication_source(self) -> str:
        return self._authentication_source

    @property
    def issuer(self) -> str:
        return self._issuer

    @property
    def audience(self) -> str:
        return self._audience

    @property
    def expires_at(self) -> str:
        return self._expires_at

    @property
    def not_before(self) -> str | None:
        return self._not_before

    @property
    def verified(self) -> bool:
        return self._verified


def _timestamp(value: str, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValidationError(f"{field_name} must be an ISO date or datetime")
    try:
        return parse_utc_timestamp(value, field_name=field_name)
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc


def _utc_datetime_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_datetime(value: str, *, field_name: str) -> datetime:
    normalized = _timestamp(value, field_name=field_name)
    return datetime.fromisoformat(normalized.replace("Z", "+00:00"))


_TRUSTED_AUTHENTICATION_PROOF = object()


class AuthenticationVerifier(Protocol):
    """Adapter contract for a real identity-provider/session verifier.

    Implementations must verify the credential before returning. They must not copy
    actor IDs, roles, permissions, issuer, audience, or timestamps from an unverified
    request body or unsigned token payload.
    """

    def verify(self, credential: object) -> VerifiedIdentity:
        """Validate an external credential and return verified identity evidence."""


class Principal:
    """Immutable authenticated actor created from verified identity evidence."""

    __slots__ = (
        "_actor_id",
        "_roles",
        "_permissions",
        "_authentication_source",
        "_issuer",
        "_audience",
        "_expires_at",
        "_not_before",
    )

    def __init__(self, *args: object, **kwargs: object) -> None:
        del args, kwargs
        raise AuthenticationBoundaryError(
            "Principal must be created from VerifiedIdentity returned by an authentication verifier"
        )

    @classmethod
    def _from_verified_identity(cls, identity: VerifiedIdentity, *, _proof: object) -> "Principal":
        if _proof is not _TRUSTED_AUTHENTICATION_PROOF or not isinstance(identity, VerifiedIdentity):
            raise AuthenticationBoundaryError("Principal requires verified authentication evidence")
        if not identity.verified:
            raise AuthenticationBoundaryError("Principal requires verified authentication evidence")
        principal = object.__new__(cls)
        object.__setattr__(principal, "_actor_id", identity.actor_id)
        object.__setattr__(principal, "_roles", identity.roles)
        object.__setattr__(principal, "_permissions", identity.permissions)
        object.__setattr__(principal, "_authentication_source", identity.authentication_source)
        object.__setattr__(principal, "_issuer", identity.issuer)
        object.__setattr__(principal, "_audience", identity.audience)
        object.__setattr__(principal, "_expires_at", identity.expires_at)
        object.__setattr__(principal, "_not_before", identity.not_before)
        return principal

    def __setattr__(self, name: str, value: object) -> None:
        del value
        raise AttributeError(f"{type(self).__name__} is immutable")

    @property
    def actor_id(self) -> str:
        return self._actor_id

    @property
    def roles(self) -> frozenset[str]:
        return self._roles

    @property
    def permissions(self) -> frozenset[str]:
        return self._permissions

    @property
    def authentication_source(self) -> str:
        return self._authentication_source

    @property
    def issuer(self) -> str:
        return self._issuer

    @property
    def audience(self) -> str:
        return self._audience

    @property
    def expires_at(self) -> str:
        return self._expires_at

    @property
    def not_before(self) -> str | None:
        return self._not_before

    def has_permission(self, permission: Permission) -> bool:
        return permission in self._permissions

    def __repr__(self) -> str:
        return (
            f"Principal(actor_id={self.actor_id!r}, roles={sorted(self.roles)!r}, "
            f"permissions={sorted(self.permissions)!r}, "
            f"authentication_source={self.authentication_source!r}, "
            f"issuer={self.issuer!r}, audience={self.audience!r}, "
            f"expires_at={self.expires_at!r})"
        )

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Principal):
            return NotImplemented
        return (
            self.actor_id,
            self.roles,
            self.permissions,
            self.authentication_source,
            self.issuer,
            self.audience,
            self.expires_at,
            self.not_before,
        ) == (
            other.actor_id,
            other.roles,
            other.permissions,
            other.authentication_source,
            other.issuer,
            other.audience,
            other.expires_at,
            other.not_before,
        )

    def __hash__(self) -> int:
        return hash(
            (
                self.actor_id,
                self.roles,
                self.permissions,
                self.authentication_source,
                self.issuer,
                self.audience,
                self.expires_at,
                self.not_before,
            )
        )


class MembershipDirectory(Protocol):
    """Read-only boundary used to resolve active principal membership."""

    def membership_for(self, *, actor_id: str, tenant_id: str) -> "Membership | None":
        """Return active membership evidence, or ``None`` without revealing why."""


class PermissionPolicy(Protocol):
    """Policy boundary for effective tenant permissions."""

    def effective_permissions(self, *, principal: Principal, membership: "Membership") -> frozenset[str]:
        """Return the intersection of trusted principal and tenant membership permissions."""


class DefaultPermissionPolicy:
    """Least-privilege policy that denies on identity or membership mismatch."""

    def effective_permissions(self, *, principal: Principal, membership: "Membership") -> frozenset[str]:
        if membership.status != "active" or membership.principal_id != principal.actor_id:
            return frozenset()
        return frozenset(principal.permissions.intersection(membership.permissions))


class Membership:
    """Validated tenant membership evidence returned by a membership directory."""

    __slots__ = ("membership_id", "tenant_id", "principal_id", "role", "permissions", "status")

    def __init__(
        self,
        *,
        membership_id: str,
        tenant_id: str,
        principal_id: str,
        role: str,
        permissions: Iterable[str],
        status: str = "active",
    ) -> None:
        object.__setattr__(self, "membership_id", _identifier(membership_id, field_name="membership_id"))
        object.__setattr__(self, "tenant_id", _identifier(tenant_id, field_name="tenant_id"))
        object.__setattr__(self, "principal_id", _identifier(principal_id, field_name="principal_id"))
        object.__setattr__(self, "role", _identifier(role, field_name="role"))
        object.__setattr__(self, "permissions", _values(permissions, field_name="permissions"))
        object.__setattr__(self, "status", _identifier(status, field_name="status"))

    def __setattr__(self, name: str, value: object) -> None:
        del value
        raise AttributeError(f"{type(self).__name__} is immutable")


class TenantContext:
    """Explicit, validated tenant scope for one operation."""

    __slots__ = ("_tenant_id", "_principal", "_membership", "_permissions")

    def __init__(self, *args: object, **kwargs: object) -> None:
        del args, kwargs
        raise AuthorizationError("TenantContext must be resolved from an active membership")

    @classmethod
    def resolve(
        cls,
        principal: Principal,
        tenant_id: str,
        directory: MembershipDirectory,
        *,
        permission_policy: PermissionPolicy | None = None,
        required_permission: Permission | None = None,
    ) -> "TenantContext":
        if not isinstance(principal, Principal):
            raise AuthorizationError("a validated Principal is required")
        requested_tenant = _identifier(tenant_id, field_name="tenant_id")
        membership = directory.membership_for(actor_id=principal.actor_id, tenant_id=requested_tenant)
        if not isinstance(membership, Membership) or membership.status != "active":
            raise AuthorizationError("principal is not an active member of the requested tenant")
        if membership.tenant_id != requested_tenant or membership.principal_id != principal.actor_id:
            raise AuthorizationError("tenant membership evidence does not match the request")
        policy = permission_policy or DefaultPermissionPolicy()
        permissions = policy.effective_permissions(principal=principal, membership=membership)
        context = object.__new__(cls)
        object.__setattr__(context, "_tenant_id", requested_tenant)
        object.__setattr__(context, "_principal", principal)
        object.__setattr__(context, "_membership", membership)
        object.__setattr__(context, "_permissions", permissions)
        if required_permission is not None:
            context.require(required_permission)
        return context

    def __setattr__(self, name: str, value: object) -> None:
        del value
        raise AttributeError(f"{type(self).__name__} is immutable")

    @property
    def tenant_id(self) -> str:
        return self._tenant_id

    @property
    def principal(self) -> Principal:
        return self._principal

    @property
    def actor_id(self) -> str:
        return self._principal.actor_id

    @property
    def membership_id(self) -> str:
        return self._membership.membership_id

    @property
    def role(self) -> str:
        return self._membership.role

    @property
    def permissions(self) -> frozenset[str]:
        return self._permissions

    def require(self, permission: Permission) -> None:
        required = _identifier(permission, field_name="permission")
        if required not in self._permissions:
            raise AuthorizationError(f"permission denied: {required}")


class AuthenticatedRequestContext:
    """Immutable request scope returned after credential and tenant checks."""

    __slots__ = ("_principal", "_tenant_context")

    def __init__(self, *args: object, **kwargs: object) -> None:
        del args, kwargs
        raise AuthenticationBoundaryError(
            "AuthenticatedRequestContext must be created by AuthenticationBoundary"
        )

    @classmethod
    def _from_boundary(
        cls,
        principal: Principal,
        tenant_context: TenantContext,
        *,
        _proof: object,
    ) -> "AuthenticatedRequestContext":
        if _proof is not _TRUSTED_AUTHENTICATION_PROOF:
            raise AuthenticationBoundaryError("request context was not created by the authentication boundary")
        if not isinstance(principal, Principal) or not isinstance(tenant_context, TenantContext):
            raise AuthenticationBoundaryError("request context requires validated identity and tenant evidence")
        context = object.__new__(cls)
        object.__setattr__(context, "_principal", principal)
        object.__setattr__(context, "_tenant_context", tenant_context)
        return context

    def __setattr__(self, name: str, value: object) -> None:
        del value
        raise AttributeError(f"{type(self).__name__} is immutable")

    @property
    def principal(self) -> Principal:
        return self._principal

    @property
    def tenant_context(self) -> TenantContext:
        return self._tenant_context

    @property
    def actor_id(self) -> str:
        return self._principal.actor_id

    @property
    def tenant_id(self) -> str:
        return self._tenant_context.tenant_id

    @property
    def membership_id(self) -> str:
        return self._tenant_context.membership_id

    @property
    def role(self) -> str:
        return self._tenant_context.role

    @property
    def permissions(self) -> frozenset[str]:
        return self._tenant_context.permissions

    def require(self, permission: Permission) -> None:
        self._tenant_context.require(permission)


class AuthenticationBoundary:
    """Trusted composition seam from a credential to an authenticated request context.

    The caller supplies only an opaque credential and an explicit tenant selection. The
    verifier supplies identity evidence; the membership directory supplies tenant
    membership; and the tenant context computes effective permissions. Request-shaped
    actor IDs, roles, and permission lists are never consulted by this boundary.

    Construct this object in the application's trusted composition root. It is an
    integration seam, not an OIDC/JWT/session implementation.
    """

    def __init__(
        self,
        verifier: AuthenticationVerifier,
        membership_directory: MembershipDirectory,
        *,
        expected_issuer: str,
        expected_audience: str,
        clock: Callable[[], datetime] | None = None,
        permission_policy: PermissionPolicy | None = None,
    ) -> None:
        if not callable(getattr(verifier, "verify", None)):
            raise ValidationError("verifier must implement verify(credential)")
        if not callable(getattr(membership_directory, "membership_for", None)):
            raise ValidationError("membership_directory must implement membership_for")
        self._verifier = verifier
        self._membership_directory = membership_directory
        self._expected_issuer = _identifier(expected_issuer, field_name="expected_issuer")
        self._expected_audience = _identifier(expected_audience, field_name="expected_audience")
        self._clock = clock or _utc_datetime_now
        self._permission_policy = permission_policy

    def authenticate(
        self,
        credential: object,
        *,
        tenant_id: str,
        required_permission: Permission | None = None,
    ) -> AuthenticatedRequestContext:
        """Verify the credential, then resolve the explicitly selected tenant."""

        if credential is None or (isinstance(credential, str) and not credential.strip()):
            raise AuthenticationError("credential is required")
        try:
            identity = self._verifier.verify(credential)
        except AuthenticationError:
            raise
        except Exception as exc:
            # Do not expose verifier details or echo a credential into an error path.
            raise AuthenticationError("credential verification failed") from exc
        if not isinstance(identity, VerifiedIdentity) or not identity.verified:
            raise AuthenticationError("verifier returned unverified identity evidence")

        self._validate_identity(identity)
        principal = Principal._from_verified_identity(identity, _proof=_TRUSTED_AUTHENTICATION_PROOF)
        try:
            tenant_context = TenantContext.resolve(
                principal,
                tenant_id,
                self._membership_directory,
                permission_policy=self._permission_policy,
                required_permission=required_permission,
            )
        except ValidationError as exc:
            raise AuthenticationError("tenant selection is malformed") from exc
        return AuthenticatedRequestContext._from_boundary(
            principal,
            tenant_context,
            _proof=_TRUSTED_AUTHENTICATION_PROOF,
        )

    def _validate_identity(self, identity: VerifiedIdentity) -> None:
        if identity.issuer != self._expected_issuer or identity.audience != self._expected_audience:
            raise AuthenticationError("identity assertion is outside the configured issuer or audience")
        try:
            now = self._clock()
            if not isinstance(now, datetime):
                raise TypeError("clock must return datetime")
            if now.tzinfo is None:
                now = now.replace(tzinfo=timezone.utc)
            else:
                now = now.astimezone(timezone.utc)
            expires_at = _parse_datetime(identity.expires_at, field_name="expires_at")
            not_before = (
                None if identity.not_before is None else _parse_datetime(identity.not_before, field_name="not_before")
            )
        except (TypeError, ValueError, ValidationError) as exc:
            raise AuthenticationError("identity assertion has invalid time claims") from exc
        if expires_at <= now:
            raise AuthenticationError("identity assertion is expired")
        if not_before is not None and now < not_before:
            raise AuthenticationError("identity assertion is not active yet")
