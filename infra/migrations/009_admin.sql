-- 009_admin.sql — Admin governance layer: user management, dynamic role
-- permissions, and admin-only reopen of finalized records.
--
-- permission_catalog: the stable slugs (mirrors app/auth/permissions.py —
-- keep the two in sync when adding permissions).
CREATE TABLE IF NOT EXISTS permission_catalog (
    permission   TEXT PRIMARY KEY,
    description  TEXT NOT NULL
);

INSERT INTO permission_catalog (permission, description) VALUES
    ('documents:write',          'Register documents, pages, and intake manifests'),
    ('documents:read',           'Browse documents and pages'),
    ('extract:run',              'Run AI extraction (text or vision) on documents'),
    ('records:project',          'Project extraction runs into land records'),
    ('records:claim',            'Claim/release records for review'),
    ('records:decide',           'Approve, reject, correct fields, resolve anomalies'),
    ('records:certify',          'Certify records (officer signature)'),
    ('records:reopen',           'Reopen REJECTED records'),
    ('records:reopen_finalized', 'Reopen finalized records (VERIFIED / OFFICER_CERTIFIED) — admin authority'),
    ('rules:validate',           'Run the validation rule engine on records'),
    ('audit:read',               'Read the audit trail and verify the chain'),
    ('exports:create',           'Generate JSON/CSV exports'),
    ('exports:read',             'Download generated exports'),
    ('admin:users',              'Create users, reset passwords, change roles'),
    ('admin:permissions',        'Change role permissions'),
    ('admin:override',           'Force-release claims and override record locks')
ON CONFLICT (permission) DO NOTHING;

CREATE TABLE IF NOT EXISTS role_permissions (
    role        TEXT NOT NULL CHECK (role IN ('admin', 'operator', 'checker', 'certifier', 'auditor')),
    permission  TEXT NOT NULL REFERENCES permission_catalog(permission) ON DELETE CASCADE,
    allowed     BOOLEAN NOT NULL DEFAULT TRUE,
    updated_by  TEXT,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (role, permission)
);

-- First-run only: seed the DEFAULT grants (= the legacy static RBAC matrix).
-- Later runs leave existing rows alone (admin toggles are never clobbered).
INSERT INTO role_permissions (role, permission, allowed, updated_by)
SELECT v.role, v.permission, TRUE, 'migration:seed'
FROM (VALUES
    -- admin: everything (enforced in code too)
    ('admin', 'documents:write'), ('admin', 'documents:read'),
    ('admin', 'extract:run'),     ('admin', 'records:project'),
    ('admin', 'records:claim'),   ('admin', 'records:decide'),
    ('admin', 'records:certify'), ('admin', 'records:reopen'),
    ('admin', 'records:reopen_finalized'),
    ('admin', 'rules:validate'),  ('admin', 'audit:read'),
    ('admin', 'exports:create'),  ('admin', 'exports:read'),
    ('admin', 'admin:users'),     ('admin', 'admin:permissions'),
    ('admin', 'admin:override'),
    -- operator: intake + extraction + projection + validation + exports
    ('operator', 'documents:write'), ('operator', 'documents:read'),
    ('operator', 'extract:run'),     ('operator', 'records:project'),
    ('operator', 'rules:validate'),  ('operator', 'exports:create'),
    ('operator', 'exports:read'),
    -- checker: review workflow (+ extraction on the legacy route)
    ('checker', 'documents:read'),   ('checker', 'extract:run'),
    ('checker', 'records:claim'),    ('checker', 'records:decide'),
    ('checker', 'records:project'),  ('checker', 'records:reopen'),
    ('checker', 'rules:validate'),   ('checker', 'exports:create'),
    ('checker', 'exports:read'),     ('checker', 'audit:read'),
    -- certifier: checker + certify
    ('certifier', 'documents:read'), ('certifier', 'extract:run'),
    ('certifier', 'records:claim'),  ('certifier', 'records:decide'),
    ('certifier', 'records:project'), ('certifier', 'records:reopen'),
    ('certifier', 'records:certify'), ('certifier', 'rules:validate'),
    ('certifier', 'exports:create'), ('certifier', 'exports:read'),
    ('certifier', 'audit:read'),
    -- auditor: read-only oversight
    ('auditor', 'documents:read'), ('auditor', 'audit:read'), ('auditor', 'exports:read')
) AS v(role, permission)
WHERE NOT EXISTS (SELECT 1 FROM role_permissions);

-- Admin-only REOPEN of finalized records (VERIFIED / OFFICER_CERTIFIED) is
-- enforced in code — records/service.py REOPEN_ADMIN_ONLY_FROM.
