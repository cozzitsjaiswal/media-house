"""Provider-neutral authentication and tenant-authorization contracts.

This module deliberately does not implement login, token parsing, password handling, or
an identity-provider integration. A trusted authentication adapter must first produce a
``VerifiedIdentity``; only then can application code create a ``Principal``.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol

from .errors import AuthorizationError, ValidationError


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
    adapter will be responsible for validating credentials and calling the private factory.
    The test-only factory below is not an authentication implementation.
    """

    __slots__ = ("_actor_id", "_roles", "_permissions", "_authentication_source")

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
        _proof: object,
    ) -> "VerifiedIdentity":
        if _proof is not _TRUSTED_AUTHENTICATION_PROOF:
            raise AuthenticationBoundaryError("authentication evidence was not produced by a trusted verifier")
        identity = object.__new__(cls)
        object.__setattr__(identity, "_actor_id", _identifier(actor_id, field_name="actor_id"))
        object.__setattr__(identity, "_roles", _values(roles, field_name="roles"))
        object.__setattr__(identity, "_permissions", _values(permissions, field_name="permissions"))
        object.__setattr__(
            identity,
            "_authentication_source",
            _identifier(authentication_source, field_name="authentication_source"),
        )
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


_TRUSTED_AUTHENTICATION_PROOF = object()


class AuthenticationVerifier(Protocol):
    """Adapter contract for a real identity-provider/session verifier."""

    def verify(self, credential: object) -> VerifiedIdentity:
        """Validate an external credential and return verified identity evidence."""


class Principal:
    """Immutable authenticated actor created from verified identity evidence."""

    __slots__ = ("_actor_id", "_roles", "_permissions", "_authentication_source")

    def __init__(self, *args: object, **kwargs: object) -> None:
        del args, kwargs
        raise AuthenticationBoundaryError(
            "Principal must be created from VerifiedIdentity returned by an authentication verifier"
        )

    @classmethod
    def from_verified_identity(cls, identity: VerifiedIdentity) -> "Principal":
        if not isinstance(identity, VerifiedIdentity):
            raise AuthenticationBoundaryError("Principal requires verified authentication evidence")
        principal = object.__new__(cls)
        object.__setattr__(principal, "_actor_id", identity.actor_id)
        object.__setattr__(principal, "_roles", identity.roles)
        object.__setattr__(principal, "_permissions", identity.permissions)
        object.__setattr__(principal, "_authentication_source", identity.authentication_source)
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

    def has_permission(self, permission: Permission) -> bool:
        return permission in self._permissions

    def __repr__(self) -> str:
        return (
            f"Principal(actor_id={self.actor_id!r}, roles={sorted(self.roles)!r}, "
            f"permissions={sorted(self.permissions)!r}, "
            f"authentication_source={self.authentication_source!r})"
        )

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Principal):
            return NotImplemented
        return (
            self.actor_id,
            self.roles,
            self.permissions,
            self.authentication_source,
        ) == (
            other.actor_id,
            other.roles,
            other.permissions,
            other.authentication_source,
        )

    def __hash__(self) -> int:
        return hash((self.actor_id, self.roles, self.permissions, self.authentication_source))


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
        if membership is None or membership.status != "active":
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
