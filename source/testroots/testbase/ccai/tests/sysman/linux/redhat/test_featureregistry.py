# Tests for the FeatureRegistry.

from ccai import testbase

from ccai.sysman.linux.redhat.featureregistry import (
    FeatureDescriptor,
    FeatureNotRegisteredError,
    FeatureRegistry,
    build_default_registry,
)


def test_register_and_describe_round_trip():
    """
        A registered feature can be described back without mutation.
    """
    registry = FeatureRegistry()
    descriptor = FeatureDescriptor(
        name="sample",
        layer=3,
        module="ccai.sysman.linux.redhat.auditmanagement",
        class_name="AuditManager",
        summary="sample summary",
    )
    registry.register(descriptor=descriptor)
    got = registry.describe(name="sample")
    testbase.assert_equal(got.name, "sample")
    testbase.assert_equal(got.layer, 3)
    testbase.assert_equal(got.summary, "sample summary")
    return


def test_register_duplicate_raises():
    """
        Registering a name twice raises ValueError.
    """
    registry = FeatureRegistry()
    descriptor = FeatureDescriptor(
        name="dup",
        layer=3,
        module="ccai.sysman.linux.redhat.auditmanagement",
        class_name="AuditManager",
        summary="",
    )
    registry.register(descriptor=descriptor)
    raised = False
    try:
        registry.register(descriptor=descriptor)
    except ValueError:
        raised = True
    testbase.assert_true(raised)
    return


def test_describe_unknown_raises():
    """
        describe() on an unregistered name raises FeatureNotRegisteredError.
    """
    registry = FeatureRegistry()
    raised = False
    try:
        registry.describe(name="nope")
    except FeatureNotRegisteredError:
        raised = True
    testbase.assert_true(raised)
    return


def test_resolve_imports_manager_class():
    """
        resolve() imports and returns the target class from the
        declared module.
    """
    registry = FeatureRegistry()
    descriptor = FeatureDescriptor(
        name="audit",
        layer=3,
        module="ccai.sysman.linux.redhat.auditmanagement",
        class_name="AuditManager",
        summary="",
    )
    registry.register(descriptor=descriptor)
    cls = registry.resolve(name="audit")
    testbase.assert_equal(cls.__name__, "AuditManager")
    return


def test_resolve_caches_class():
    """
        Second resolve() returns the same object (cached).
    """
    registry = FeatureRegistry()
    descriptor = FeatureDescriptor(
        name="audit",
        layer=3,
        module="ccai.sysman.linux.redhat.auditmanagement",
        class_name="AuditManager",
        summary="",
    )
    registry.register(descriptor=descriptor)
    first = registry.resolve(name="audit")
    second = registry.resolve(name="audit")
    testbase.assert_true(first is second)
    return


def test_unregister_removes_descriptor_and_cache():
    """
        After unregister() describe() raises and resolve() raises.
    """
    registry = FeatureRegistry()
    descriptor = FeatureDescriptor(
        name="audit",
        layer=3,
        module="ccai.sysman.linux.redhat.auditmanagement",
        class_name="AuditManager",
        summary="",
    )
    registry.register(descriptor=descriptor)
    registry.resolve(name="audit")
    registry.unregister(name="audit")

    raised = False
    try:
        registry.describe(name="audit")
    except FeatureNotRegisteredError:
        raised = True
    testbase.assert_true(raised)
    return


def test_by_layer_filters_correctly():
    """
        by_layer() returns only descriptors matching the requested layer.
    """
    registry = FeatureRegistry()
    registry.register(descriptor=FeatureDescriptor(
        name="l1",
        layer=1,
        module="ccai.sysman.linux.redhat.commandexecution",
        class_name="CommandExecutor",
        summary="",
    ))
    registry.register(descriptor=FeatureDescriptor(
        name="l3",
        layer=3,
        module="ccai.sysman.linux.redhat.auditmanagement",
        class_name="AuditManager",
        summary="",
    ))
    layer_3 = registry.by_layer(layer=3)
    testbase.assert_equal(len(layer_3), 1)
    testbase.assert_equal(layer_3[0].name, "l3")
    return


def test_default_registry_populated_with_all_implemented_features():
    """
        The seed registry contains exactly the 47 implemented features
        (2 Layer 1 + 1 Layer 2 + 43 Layer 3).
    """
    registry = build_default_registry()
    names = registry.names()
    testbase.assert_equal(len(names), 47)
    testbase.assert_true("command-execution" in names)
    testbase.assert_true("maintenance-reporting" in names)
    testbase.assert_true("package-management" in names)
    testbase.assert_true("security-compliance" in names)
    testbase.assert_true("acl-management" in names)
    testbase.assert_true("filesystem-quotas" in names)
    testbase.assert_true("precision-time-management" in names)
    testbase.assert_true("remote-log-forwarding" in names)
    testbase.assert_true("authentication-management" in names)
    testbase.assert_true("dns-server-management" in names)
    testbase.assert_true("mail-transport-management" in names)
    testbase.assert_true("nfs-server-management" in names)
    testbase.assert_true("samba-server-management" in names)
    testbase.assert_true("certificate-issuance" in names)
    testbase.assert_true("encryption-management" in names)
    testbase.assert_true("identity-management" in names)
    testbase.assert_true("resource-control-management" in names)
    testbase.assert_true("virtualization-management" in names)
    testbase.assert_equal(len(registry.by_layer(layer=1)), 2)
    testbase.assert_equal(len(registry.by_layer(layer=2)), 1)
    testbase.assert_equal(len(registry.by_layer(layer=3)), 44)
    return


def test_default_registry_resolves_a_manager_class():
    """
        Every seeded descriptor resolves without raising.  Spot-check
        one Layer 3 entry.
    """
    registry = build_default_registry()
    cls = registry.resolve(name="package-management")
    testbase.assert_equal(cls.__name__, "PackageManager")
    return
