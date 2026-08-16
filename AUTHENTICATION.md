# Authentication and RBAC

The Developer Edition implements local bootstrap authentication from `.env`.

- `BOOTSTRAP_ADMIN_EMAIL` creates the first organization administrator.
- `BOOTSTRAP_ADMIN_PASSWORD` is stored only as an adaptive password hash.
- Passwords are stored as bcrypt hashes in PostgreSQL.
- Random browser session tokens are stored only as SHA-256 hashes and sent in `HttpOnly`, `SameSite=Strict` cookies.
- Implemented roles are `platform_admin`, `operator`, and `viewer`.
- Authentication events and existing control-plane mutations write PostgreSQL audit records.
- Native discovery agents authenticate with a dedicated `AGENT_TOKEN` bearer token or an operator session.
- Public signup is disabled unless a platform administrator enables local registration in Settings.

Authentication modes:

1. `local`: implemented for local password login and air-gapped deployments.
2. `oidc`: planned for standards-based providers, including Microsoft Entra ID.
3. `ldap`: planned as optional enterprise directory synchronization.

Authenticated viewers can read control-plane resources and run model inference. Operators can mutate workloads, discovery, pools, and workflows. Platform administrators can also change platform settings. The native host-agent import endpoint requires an operator session or dedicated bearer token; per-agent enrollment, token rotation, and revocation remain production milestones.
