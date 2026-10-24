# Authentication and Tenant Security Checklist

This checklist distinguishes code that exists in the local foundation from controls that
still require a real identity provider, service boundary, deployment, or operational process.
It is not a production-readiness certification.

## Implemented and locally tested

- [x] An explicit `AuthenticationVerifier` interface separates credential verification from
      tenant membership and action authorization.
- [x] `AuthenticationBoundary` accepts an opaque credential and explicit tenant selection;
      it does not accept actor IDs, roles, or permission lists as proof.
- [x] Verifier output is type-checked and must be marked verified.
- [x] Issuer, audience, expiry, and not-before claims are checked against configured values.
- [x] Missing credentials and verifier failures fail closed without echoing credential data.
- [x] Unknown, inactive, mismatched, or missing tenant membership fails closed.
- [x] Effective permissions are the intersection of verifier evidence and active membership
      permissions; missing permissions deny the operation.
- [x] Tenant-scoped prospect reads, updates, ownership detach, and CSV export are protected
      by an explicit `TenantContext`.
- [x] Cross-tenant object access uses a non-enumerating not-found result and records denied
      audit events.
- [x] Tenant and membership provisioning requires explicit administrative permissions.
- [x] Provisioning rejects self-grant, unknown roles, duplicate memberships, empty permission
      sets, and permission expansion beyond the administrator's effective permissions.
- [x] Provisioning audit metadata records the authenticated administrator, source tenant,
      target tenant, affected identity, action, result, and status.
- [x] Successful provisioning and its audit write are atomic; an audit failure rolls back the
      business mutation.
- [x] Harvey CSV import is an optional reviewed-file adapter. It does not import Harvey
      credentials or run Harvey's network/sending clients.

## Not implemented or not verified

- [ ] OIDC, OAuth, JWT, SAML, passkey, or session-cookie verification.
- [ ] Signature/key discovery, key rotation, revocation, nonce/state binding, or provider
      clock-skew policy.
- [ ] Password storage, password reset, login, account recovery, or MFA.
- [ ] Durable user lifecycle, invitations, tenant deletion, membership revocation, or
      offboarding workflows.
- [ ] Web/API middleware that obtains an opaque credential and constructs the request context.
- [ ] Database-level row-level security or protection against a caller that can directly open
      the SQLite file or obtain the raw connection.
- [ ] Tenant migration/backfill for meetings, requirements, drafts, orders, payments, files,
      jobs, webhooks, exports, or other legacy records.
- [ ] Authenticated file/object-storage download policy.
- [ ] Encrypted secrets, encrypted backups, restore drills, retention, deletion, incident
      response, audit retention, and production monitoring.
- [ ] Production deployment, multi-process concurrency, hosted database isolation, or a real
      external identity provider.

## Required release evidence before shared-client use

1. A named provider and documented verifier implementation with negative tests for bad
   signatures, wrong issuer/audience, expired tokens, replay, revocation, and clock skew.
2. A service/API composition root that creates `AuthenticationBoundary` once and never accepts
   caller-supplied actor or permission fields as authority.
3. A complete tenant ownership migration with backup, reconciliation, rollback evidence, and
   same-tenant foreign-key/RLS controls.
4. Cross-tenant tests for every resource and delivery path, including files, background jobs,
   webhooks, exports, and admin actions.
5. Operational evidence for secrets, MFA, backups/restores, offboarding, retention, and audit
   review.

Until those items exist, the code remains a local reviewable foundation and the existing CLI
remains local-only.
