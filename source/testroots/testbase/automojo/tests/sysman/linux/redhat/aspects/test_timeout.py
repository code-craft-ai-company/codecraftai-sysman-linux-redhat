# Tests for the TimeoutAspect.

from ccai import testbase

from ccai.sysman.linux.redhat.aspects.timeout import (
    TimeoutAspect,
    TimeoutAspectConfig,
)
from ccai.sysman.linux.redhat.runners.models import ExecutionContext


def _context_without_timeout() -> ExecutionContext:
    """
        Build an execution context whose timeout has not yet been set.
    """
    context = ExecutionContext(
        args=["x"],
        elevated=False,
        cwd=None,
        env=None,
        timeout=None,
        transport="local",
        dry_run=False,
    )
    return context


def _context_with_timeout(timeout: float) -> ExecutionContext:
    """
        Build an execution context with a specific caller-supplied timeout.
    """
    context = ExecutionContext(
        args=["x"],
        elevated=False,
        cwd=None,
        env=None,
        timeout=timeout,
        transport="local",
        dry_run=False,
    )
    return context


def test_sets_default_when_timeout_missing():
    """
        TimeoutAspect should populate the default timeout when none is set.
    """
    aspect = TimeoutAspect(TimeoutAspectConfig(default_timeout=30.0))
    context = _context_without_timeout()
    aspect.before_execute(context)
    testbase.assert_equal(context.timeout, 30.0)
    return


def test_preserves_user_timeout_with_override():
    """
        When override is allowed, a caller-supplied timeout must be preserved.
    """
    aspect = TimeoutAspect(TimeoutAspectConfig(
        default_timeout=30.0, allow_override=True,
    ))
    context = _context_with_timeout(timeout=5.0)
    aspect.before_execute(context)
    testbase.assert_equal(context.timeout, 5.0)
    return


def test_overrides_user_timeout_when_disallowed():
    """
        When override is disallowed, the default timeout must replace the caller's value.
    """
    aspect = TimeoutAspect(TimeoutAspectConfig(
        default_timeout=30.0, allow_override=False,
    ))
    context = _context_with_timeout(timeout=5.0)
    aspect.before_execute(context)
    testbase.assert_equal(context.timeout, 30.0)
    return
