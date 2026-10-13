"""Helpers for tests that drive the AEP runtime without the full Env."""
from governance.aep_executors import default_registry
from governance.aep_runtime import AEPRuntime
from governance.auth_registry import AuthorizationRecord, create_authorization_registry
from governance.consumption import create_consumption_ledger
from governance.envelope import ExecutionEnvelope, SCHEMA_VERSION, authority_digest


class Standalone:
    """issuer + view + ledger + handle + runtime, wired like production."""

    def __init__(self, table, registry=None, runtime_cls=AEPRuntime):
        self.issuer, self.view = create_authorization_registry()
        self.ledger, self.handle = create_consumption_ledger()
        self.registry = registry if registry is not None else default_registry()
        self.runtime = runtime_cls(self.registry, table, self.view, self.handle)


def record_for(auth_id="ev-0", intent="i", action="fs.read", opcode="FS_READ", args=(),
               binding=None, resource=None, identity=None, **kw):
    return AuthorizationRecord(
        authorization_id=auth_id, intent_id=intent, action=action, canonical_args=args,
        resource_binding_id=binding, opcode=opcode, resource=resource, resource_identity=identity, **kw
    )


def envelope_for(record, execution_id="x-1", **overrides):
    fields = dict(
        schema_version=SCHEMA_VERSION,
        execution_id=execution_id,
        authorization_id=record.authorization_id,
        intent_id=record.intent_id,
        opcode=record.action,
        authorized_args=tuple(record.canonical_args),
        resource_binding_id=record.resource_binding_id,
        authorization_digest=authority_digest(
            record.authorization_id, record.intent_id, record.action,
            record.canonical_args, record.resource_binding_id,
        ),
    )
    fields.update(overrides)
    return ExecutionEnvelope(**fields)
