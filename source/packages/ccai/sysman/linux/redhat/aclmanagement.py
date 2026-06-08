# ccai.sysman.linux.redhat.aclmanagement
#
# AclManager wraps POSIX ACL tooling (getfacl/setfacl), extended file
# attributes (lsattr/chattr), and file capabilities (getcap/setcap)
# through the ICommandRunner abstraction.

from dataclasses import dataclass, field
from typing import Optional

from ccai.sysman.linux.redhat.featurebase import (
    ActionStatus,
    BaseFeatureManager,
    FeatureActionResult,
    FeatureConfig,
)


# ----------------------------------------------------------------------
# Module-level constants
# ----------------------------------------------------------------------

_GETFACL = "getfacl"
_SETFACL = "setfacl"
_LSATTR = "lsattr"
_CHATTR = "chattr"
_GETCAP = "getcap"
_SETCAP = "setcap"
_STAT = "stat"

# Documented ext*-family attribute flag letters that we expose as
# typed booleans on :class:`FileAttributes`.  Anything outside this
# set still appears in ``raw_flags`` but does not have a typed
# accessor.
_FILE_ATTR_FLAG_MAP: dict = {
    "i": "immutable",
    "a": "append_only",
    "A": "no_atime",
    "d": "no_dump",
    "S": "sync",
    "t": "no_tail_merge",
    "u": "undelete",
}


AclActionStatus = ActionStatus


# ----------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------


@dataclass
class AclManagerConfig(FeatureConfig):
    """
        Configuration for :class:`AclManager`.  Inherits ``dry_run``
        and ``elevated`` from :class:`FeatureConfig`.

        :param getfacl_path: Path / name of the ``getfacl`` binary.
        :param setfacl_path: Path / name of the ``setfacl`` binary.
        :param lsattr_path: Path / name of the ``lsattr`` binary.
        :param chattr_path: Path / name of the ``chattr`` binary.
        :param getcap_path: Path / name of the ``getcap`` binary.
        :param setcap_path: Path / name of the ``setcap`` binary.
        :param stat_path: Path / name of the ``stat`` binary.
    """
    getfacl_path: str = _GETFACL
    setfacl_path: str = _SETFACL
    lsattr_path: str = _LSATTR
    chattr_path: str = _CHATTR
    getcap_path: str = _GETCAP
    setcap_path: str = _SETCAP
    stat_path: str = _STAT


# ----------------------------------------------------------------------
# Result and parsed-data dataclasses
# ----------------------------------------------------------------------


@dataclass
class AclActionResult(FeatureActionResult):
    """
        Outcome of an ACL-management action.

        :param path: The target path the action operated on.
    """
    path: Optional[str] = None


@dataclass
class AclEntry:
    """
        A single parsed POSIX ACL entry.

        :param tag: ``"user"``, ``"group"``, ``"mask"``, or ``"other"``.
        :param qualifier: User or group name when applicable; ``None``
                          for ``mask`` / ``other`` and for the base
                          owner / group rows where ``getfacl`` emits
                          no name.
        :param perms: Three-character POSIX rights token
                      (``"rwx"``, ``"r-x"``, ``"---"``, ...).
        :param is_default: ``True`` when the entry is a default ACL
                           (directory inheritance prototype).
    """
    tag: str
    qualifier: Optional[str]
    perms: str
    is_default: bool = False


@dataclass
class AclSet:
    """
        Snapshot of a path's POSIX ACL.

        :param path: The path the ACL was queried for.
        :param entries: Non-default ACL entries in source order.
        :param default_entries: Default ACL entries (directory only).
        :param has_mask: ``True`` when the parsed ACL contains a
                         ``mask::`` entry.
    """
    path: str
    entries: list = field(default_factory=list)
    default_entries: list = field(default_factory=list)
    has_mask: bool = False


@dataclass
class FileAttributes:
    """
        Parsed ext*-family extended attributes for a single path.

        :param path: The path the attributes were queried for.
        :param immutable: ``i`` flag.
        :param append_only: ``a`` flag.
        :param no_atime: ``A`` flag.
        :param no_dump: ``d`` flag.
        :param sync: ``S`` flag.
        :param no_tail_merge: ``t`` flag.
        :param undelete: ``u`` flag.
        :param raw_flags: The full ``lsattr -d`` flag column verbatim.
    """
    path: str
    immutable: bool = False
    append_only: bool = False
    no_atime: bool = False
    no_dump: bool = False
    sync: bool = False
    no_tail_merge: bool = False
    undelete: bool = False
    raw_flags: str = ""


@dataclass
class FileCapabilities:
    """
        Parsed ``getcap`` output for a single path.

        :param path: The path the capability set applies to.
        :param effective: Capabilities tagged with ``e``.
        :param permitted: Capabilities tagged with ``p``.
        :param inheritable: Capabilities tagged with ``i``.
        :param raw: The original ``getcap`` output line for the path.
    """
    path: str
    effective: list = field(default_factory=list)
    permitted: list = field(default_factory=list)
    inheritable: list = field(default_factory=list)
    raw: str = ""


# ----------------------------------------------------------------------
# Manager
# ----------------------------------------------------------------------


class AclManager(BaseFeatureManager):
    """
        Facade for POSIX ACL, ext-attr, and capability management
        through the command runner.
    """

    result_class = AclActionResult
    feature_name = "acl-management"

    # ------------------------------------------------------------------
    # POSIX ACL queries
    # ------------------------------------------------------------------

    def query_acl(
        self,
        path: str,
        *,
        recursive: bool = False,
    ) -> Optional[object]:
        """
            Query the POSIX ACL for ``path``.

            :param path: The absolute path to query.
            :param recursive: When True, walk the subtree and return a
                              ``dict[str, AclSet]`` keyed by path.

            :returns: An :class:`AclSet` (or ``None`` when the path is
                      missing) when ``recursive=False``; a
                      ``dict[str, AclSet]`` otherwise.
        """
        if recursive:
            mapping = self._enumerate_acls_via_runner(root=path)
            return mapping

        command = [
            self._cfg().getfacl_path,
            "--absolute-names",
            "--omit-header",
            path,
        ]
        run_result = self._runner.run(list(command), elevated=False)

        if run_result.exit_code != 0:
            rtnval: Optional[AclSet] = None
            return rtnval

        rtnval = _parse_getfacl_single(stdout=run_result.stdout, path=path)
        return rtnval

    def enumerate_acls(self, root: str) -> dict:
        """
            Enumerate ACLs across a subtree rooted at ``root``.

            :param root: The directory subtree to enumerate.

            :returns: A ``dict[str, AclSet]`` keyed by path.  Empty
                      dict when the runner returns non-zero.
        """
        rtnval = self._enumerate_acls_via_runner(root=root)
        return rtnval

    # ------------------------------------------------------------------
    # POSIX ACL writes
    # ------------------------------------------------------------------

    def set_acl(
        self,
        path: str,
        entries: list,
        *,
        recursive: bool = False,
    ) -> AclActionResult:
        """
            Add or modify the supplied ACL ``entries`` on ``path``.

            Returns SKIPPED when every requested entry is already
            present on ``path`` with matching perms.

            :param path: The target path.
            :param entries: A list of :class:`AclEntry`.
            :param recursive: When True, pass ``-R`` to ``setfacl``.

            :returns: The :class:`AclActionResult`.
        """
        if entries is None:
            err_msg = "set_acl() requires a non-None 'entries' list."
            raise ValueError(err_msg)
        if len(entries) == 0:
            err_msg = "set_acl() requires at least one ACL entry."
            raise ValueError(err_msg)

        spec_string = _entries_to_spec(entries=entries)
        command = self._build_setfacl_modify_command(
            spec_string=spec_string, path=path, recursive=recursive,
        )

        # Idempotency probe.  Done after building the command so that
        # the dry-run path can still surface the would-have-been argv.
        if not self._config.dry_run:
            current = self.query_acl(path=path)
            if current is not None and _entries_already_present(
                current=current, requested=entries,
            ):
                skipped = AclActionResult(
                    status=AclActionStatus.SKIPPED,
                    detail=spec_string,
                    command=list(command),
                    path=path,
                )
                return skipped

        rtnval = self._dispatch(
            command=command, detail=spec_string, path=path,
            operation="set_acl",
        )
        return rtnval

    def remove_acl_entries(
        self,
        path: str,
        entries: list,
        *,
        recursive: bool = False,
    ) -> AclActionResult:
        """
            Remove the supplied ACL ``entries`` from ``path`` using
            ``setfacl -x``.

            :param path: The target path.
            :param entries: A list of :class:`AclEntry`; the rights
                            token is ignored because ``-x`` accepts
                            only ``[d:]<tag>:<qualifier>``.
            :param recursive: When True, pass ``-R`` to ``setfacl``.

            :returns: The :class:`AclActionResult`.
        """
        if entries is None:
            err_msg = "remove_acl_entries() requires a non-None 'entries' list."
            raise ValueError(err_msg)
        if len(entries) == 0:
            err_msg = "remove_acl_entries() requires at least one ACL entry."
            raise ValueError(err_msg)

        spec_string = _entries_to_remove_spec(entries=entries)

        argv: list = [self._cfg().setfacl_path]
        if recursive:
            argv.append("-R")
        argv.append("-x")
        argv.append(spec_string)
        argv.append(path)

        rtnval = self._dispatch(
            command=argv, detail=spec_string, path=path,
            operation="remove_acl_entries",
        )
        return rtnval

    def clear_acl(
        self,
        path: str,
        *,
        recursive: bool = False,
    ) -> AclActionResult:
        """
            Remove all extended ACL entries on ``path`` (``setfacl -b``).
            Leaves only the base owner / group / other rows that come
            from ``chmod``.

            :param path: The target path.
            :param recursive: When True, pass ``-R`` to ``setfacl``.

            :returns: The :class:`AclActionResult`.
        """
        argv: list = [self._cfg().setfacl_path]
        if recursive:
            argv.append("-R")
        argv.append("-b")
        argv.append(path)

        rtnval = self._dispatch(
            command=argv, detail=None, path=path,
            operation="clear_acl",
        )
        return rtnval

    def clear_default_acl(self, path: str) -> AclActionResult:
        """
            Remove all default ACL entries on a directory
            (``setfacl -k``).  Verifies the path is a directory first;
            returns FAILED if not.

            :param path: The target directory.

            :returns: The :class:`AclActionResult`.
        """
        is_dir = self._is_directory(path=path)
        if not is_dir:
            failed = AclActionResult(
                status=AclActionStatus.FAILED,
                detail="path is not a directory",
                path=path,
                command=[self._cfg().setfacl_path, "-k", path],
            )
            return failed

        argv = [self._cfg().setfacl_path, "-k", path]
        rtnval = self._dispatch(
            command=argv, detail=None, path=path,
            operation="clear_default_acl",
        )
        return rtnval

    # ------------------------------------------------------------------
    # Extended attribute queries
    # ------------------------------------------------------------------

    def query_attrs(self, path: str) -> Optional[FileAttributes]:
        """
            Query the ext*-family extended attributes for ``path``.

            :param path: The target path.

            :returns: A :class:`FileAttributes` snapshot, or ``None``
                      when the runner returns non-zero.
        """
        argv = [self._cfg().lsattr_path, "-d", path]
        run_result = self._runner.run(list(argv), elevated=False)

        if run_result.exit_code != 0:
            rtnval: Optional[FileAttributes] = None
            return rtnval

        first_line = _first_non_empty_line(text=run_result.stdout)
        if first_line is None:
            rtnval = FileAttributes(path=path)
            return rtnval

        rtnval = _parse_lsattr_line(line=first_line, fallback_path=path)
        return rtnval

    def enumerate_attrs(self, root: str) -> dict:
        """
            Enumerate ext*-family attributes on a subtree.

            :param root: The directory subtree to walk.

            :returns: A ``dict[str, FileAttributes]`` keyed by path.
                      Empty dict when the runner returns non-zero.
        """
        argv = [self._cfg().lsattr_path, "-R", root]
        run_result = self._runner.run(list(argv), elevated=False)

        rtnval: dict = {}
        if run_result.exit_code != 0:
            return rtnval

        for raw_line in run_result.stdout.splitlines():
            stripped = raw_line.strip()
            if len(stripped) == 0:
                continue
            # Section headers from ``lsattr -R`` look like ``./dir:`` --
            # skip rows that look like that.
            if stripped.endswith(":") and " " not in stripped:
                continue

            parsed = _parse_lsattr_line(
                line=stripped, fallback_path=stripped,
            )
            if parsed is None:
                continue
            rtnval[parsed.path] = parsed
        return rtnval

    # ------------------------------------------------------------------
    # Extended attribute writes
    # ------------------------------------------------------------------

    def add_attrs(
        self,
        path: str,
        flags: str,
        *,
        recursive: bool = False,
    ) -> AclActionResult:
        """
            Add the requested ``flags`` to ``path`` via
            ``chattr [-R] +<flags>``.

            :param path: The target path.
            :param flags: Concatenated flag letters (``"ia"``,
                          ``"A"``, ...).
            :param recursive: When True, pass ``-R``.  ``-R`` against a
                              non-directory path returns FAILED rather
                              than silently ignoring the flag.

            :returns: The :class:`AclActionResult`.
        """
        guard_failure = self._guard_recursive_directory(
            path=path, recursive=recursive,
        )
        if guard_failure is not None:
            return guard_failure

        argv = self._build_chattr_command(
            op="+", flags=flags, path=path, recursive=recursive,
        )

        if not self._config.dry_run:
            current = self.query_attrs(path=path)
            if current is not None:
                missing = _flags_missing(current=current, requested=flags)
                if len(missing) == 0:
                    skipped = AclActionResult(
                        status=AclActionStatus.SKIPPED,
                        detail="+{}".format(flags),
                        path=path,
                        command=list(argv),
                    )
                    return skipped

        rtnval = self._dispatch(
            command=argv, detail="+{}".format(flags), path=path,
            operation="add_attrs",
        )
        return rtnval

    def remove_attrs(
        self,
        path: str,
        flags: str,
        *,
        recursive: bool = False,
    ) -> AclActionResult:
        """
            Remove the requested ``flags`` from ``path`` via
            ``chattr [-R] -<flags>``.

            :param path: The target path.
            :param flags: Concatenated flag letters.
            :param recursive: When True, pass ``-R``.

            :returns: The :class:`AclActionResult`.
        """
        guard_failure = self._guard_recursive_directory(
            path=path, recursive=recursive,
        )
        if guard_failure is not None:
            return guard_failure

        argv = self._build_chattr_command(
            op="-", flags=flags, path=path, recursive=recursive,
        )

        if not self._config.dry_run:
            current = self.query_attrs(path=path)
            if current is not None:
                still_set = _flags_still_set(current=current, requested=flags)
                if len(still_set) == 0:
                    skipped = AclActionResult(
                        status=AclActionStatus.SKIPPED,
                        detail="-{}".format(flags),
                        path=path,
                        command=list(argv),
                    )
                    return skipped

        rtnval = self._dispatch(
            command=argv, detail="-{}".format(flags), path=path,
            operation="remove_attrs",
        )
        return rtnval

    def set_attrs(
        self,
        path: str,
        flags: str,
        *,
        recursive: bool = False,
    ) -> AclActionResult:
        """
            Set the attribute mask on ``path`` to exactly ``flags`` via
            ``chattr [-R] =<flags>``.

            :param path: The target path.
            :param flags: Concatenated flag letters.  An empty string
                          clears all flags.
            :param recursive: When True, pass ``-R``.

            :returns: The :class:`AclActionResult`.
        """
        guard_failure = self._guard_recursive_directory(
            path=path, recursive=recursive,
        )
        if guard_failure is not None:
            return guard_failure

        argv = self._build_chattr_command(
            op="=", flags=flags, path=path, recursive=recursive,
        )

        if not self._config.dry_run:
            current = self.query_attrs(path=path)
            if current is not None:
                if _flags_match_exactly(current=current, requested=flags):
                    skipped = AclActionResult(
                        status=AclActionStatus.SKIPPED,
                        detail="={}".format(flags),
                        path=path,
                        command=list(argv),
                    )
                    return skipped

        rtnval = self._dispatch(
            command=argv, detail="={}".format(flags), path=path,
            operation="set_attrs",
        )
        return rtnval

    # ------------------------------------------------------------------
    # File capability queries
    # ------------------------------------------------------------------

    def query_caps(self, path: str) -> Optional[FileCapabilities]:
        """
            Query the file capabilities on ``path``.

            :param path: The target path.

            :returns: A :class:`FileCapabilities` snapshot.  Returns
                      ``None`` when the runner returns non-zero.  An
                      empty stdout (no capabilities set) yields a
                      :class:`FileCapabilities` with empty lists.
        """
        argv = [self._cfg().getcap_path, path]
        run_result = self._runner.run(list(argv), elevated=False)

        if run_result.exit_code != 0:
            rtnval: Optional[FileCapabilities] = None
            return rtnval

        first_line = _first_non_empty_line(text=run_result.stdout)
        if first_line is None:
            rtnval = FileCapabilities(path=path)
            return rtnval

        rtnval = _parse_getcap_line(line=first_line, fallback_path=path)
        return rtnval

    def enumerate_caps(self, root: str) -> list:
        """
            Enumerate file capabilities on a subtree.

            :param root: The directory subtree to walk.

            :returns: A ``list[FileCapabilities]``.  Empty list when
                      the runner returns non-zero or when the subtree
                      has no capabilities.
        """
        argv = [self._cfg().getcap_path, "-r", root]
        run_result = self._runner.run(list(argv), elevated=False)

        rtnval: list = []
        if run_result.exit_code != 0:
            return rtnval

        for raw_line in run_result.stdout.splitlines():
            stripped = raw_line.strip()
            if len(stripped) == 0:
                continue
            parsed = _parse_getcap_line(line=stripped, fallback_path=stripped)
            if parsed is None:
                continue
            rtnval.append(parsed)
        return rtnval

    # ------------------------------------------------------------------
    # File capability writes
    # ------------------------------------------------------------------

    def set_caps(
        self,
        path: str,
        caps: str,
    ) -> AclActionResult:
        """
            Apply a capability set to ``path`` via
            ``setcap '<spec>' <path>``.

            :param path: The target path.
            :param caps: The ``setcap`` capability specification, e.g.
                         ``"cap_net_bind_service=ep"``.

            :returns: The :class:`AclActionResult`.
        """
        if caps is None:
            err_msg = "set_caps() requires a non-None 'caps' specification."
            raise ValueError(err_msg)
        if len(caps) == 0:
            err_msg = "set_caps() requires a non-empty 'caps' specification."
            raise ValueError(err_msg)

        argv = [self._cfg().setcap_path, caps, path]

        if not self._config.dry_run:
            current = self.query_caps(path=path)
            if current is not None and _caps_match(
                current=current, requested=caps,
            ):
                skipped = AclActionResult(
                    status=AclActionStatus.SKIPPED,
                    detail=caps,
                    path=path,
                    command=list(argv),
                )
                return skipped

        rtnval = self._dispatch(
            command=argv, detail=caps, path=path,
            operation="set_caps",
        )
        return rtnval

    def clear_caps(self, path: str) -> AclActionResult:
        """
            Remove all file capabilities from ``path`` via
            ``setcap -r <path>``.

            :param path: The target path.

            :returns: The :class:`AclActionResult`.
        """
        argv = [self._cfg().setcap_path, "-r", path]
        rtnval = self._dispatch(
            command=argv, detail=None, path=path,
            operation="clear_caps",
        )
        return rtnval

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _cfg(self) -> AclManagerConfig:
        """
            Return the manager's :class:`AclManagerConfig`, falling
            back to a default-constructed instance when the manager
            was instantiated with the bare base config.
        """
        if isinstance(self._config, AclManagerConfig):
            rtnval = self._config
        else:
            rtnval = AclManagerConfig(
                dry_run=self._config.dry_run,
                elevated=self._config.elevated,
            )
        return rtnval

    def _is_directory(self, path: str) -> bool:
        """
            Probe whether ``path`` is a directory using
            ``stat -c %F <path>``.

            :param path: The path to probe.

            :returns: True when ``stat`` reports ``"directory"``.
        """
        argv = [self._cfg().stat_path, "-c", "%F", path]
        run_result = self._runner.run(list(argv), elevated=False)

        if run_result.exit_code != 0:
            rtnval = False
            return rtnval

        kind = run_result.stdout.strip()
        rtnval = (kind == "directory")
        return rtnval

    def _enumerate_acls_via_runner(self, *, root: str) -> dict:
        """
            Run ``getfacl -R --absolute-names --omit-header <root>``
            and parse the multi-block output into a dict keyed by path.
        """
        argv = [
            self._cfg().getfacl_path,
            "-R",
            "--absolute-names",
            "--omit-header",
            root,
        ]
        run_result = self._runner.run(list(argv), elevated=False)

        rtnval: dict = {}
        if run_result.exit_code != 0:
            return rtnval

        rtnval = _parse_getfacl_recursive(stdout=run_result.stdout)
        return rtnval

    def _build_setfacl_modify_command(
        self,
        *,
        spec_string: str,
        path: str,
        recursive: bool,
    ) -> list:
        """
            Build the ``setfacl -m`` argv with optional ``-R``.
        """
        rtnval: list = [self._cfg().setfacl_path]
        if recursive:
            rtnval.append("-R")
        rtnval.append("-m")
        rtnval.append(spec_string)
        rtnval.append(path)
        return rtnval

    def _build_chattr_command(
        self,
        *,
        op: str,
        flags: str,
        path: str,
        recursive: bool,
    ) -> list:
        """
            Build the ``chattr`` argv for ``+``, ``-``, or ``=`` ops.
        """
        rtnval: list = [self._cfg().chattr_path]
        if recursive:
            rtnval.append("-R")
        rtnval.append("{}{}".format(op, flags))
        rtnval.append(path)
        return rtnval

    def _guard_recursive_directory(
        self,
        *,
        path: str,
        recursive: bool,
    ) -> Optional[AclActionResult]:
        """
            Reject ``recursive=True`` against non-directory paths.
            Returns a FAILED result when the guard trips, ``None``
            otherwise.
        """
        if not recursive:
            rtnval: Optional[AclActionResult] = None
            return rtnval

        is_dir = self._is_directory(path=path)
        if is_dir:
            rtnval = None
            return rtnval

        failure = AclActionResult(
            status=AclActionStatus.FAILED,
            detail="recursive operation requested on non-directory path",
            path=path,
        )
        rtnval = failure
        return rtnval


# ----------------------------------------------------------------------
# Module-level helpers: spec rendering
# ----------------------------------------------------------------------


def _entry_to_spec(*, entry: AclEntry) -> str:
    """
        Render a single :class:`AclEntry` into a ``setfacl -m`` token.

        Examples (no default)::

            AclEntry("user", "alice", "rwx") -> "u:alice:rwx"
            AclEntry("group", "wheel", "r-x") -> "g:wheel:r-x"
            AclEntry("mask", None, "rwx")   -> "m::rwx"
            AclEntry("other", None, "r--")  -> "o::r--"

        With ``is_default=True`` the result is prefixed with ``"d:"``.

        :param entry: The :class:`AclEntry`.

        :returns: The rendered spec token.
        :raises: ValueError on unknown ``tag``.
    """
    letter = _tag_to_letter(tag=entry.tag)

    if entry.qualifier is None:
        qualifier_token = ""
    else:
        qualifier_token = entry.qualifier

    body = "{}:{}:{}".format(letter, qualifier_token, entry.perms)

    if entry.is_default:
        rtnval = "d:" + body
    else:
        rtnval = body
    return rtnval


def _entry_to_remove_token(*, entry: AclEntry) -> str:
    """
        Render an :class:`AclEntry` into a ``setfacl -x`` token.
        ``-x`` accepts only ``[d:]<tag>:<qualifier>`` (no perms).

        :param entry: The :class:`AclEntry` whose principal is to be
                      removed.

        :returns: The rendered token.
        :raises: ValueError on unknown ``tag``.
    """
    letter = _tag_to_letter(tag=entry.tag)

    if entry.qualifier is None:
        qualifier_token = ""
    else:
        qualifier_token = entry.qualifier

    body = "{}:{}".format(letter, qualifier_token)

    if entry.is_default:
        rtnval = "d:" + body
    else:
        rtnval = body
    return rtnval


def _entries_to_spec(*, entries: list) -> str:
    """
        Render a list of :class:`AclEntry` into a single comma-joined
        ``setfacl -m`` argument.

        :param entries: The list of entries.
    """
    rendered: list = []
    for entry in entries:
        rendered.append(_entry_to_spec(entry=entry))

    rtnval = ",".join(rendered)
    return rtnval


def _entries_to_remove_spec(*, entries: list) -> str:
    """
        Render a list of :class:`AclEntry` into a single comma-joined
        ``setfacl -x`` argument.

        :param entries: The list of entries.
    """
    rendered: list = []
    for entry in entries:
        rendered.append(_entry_to_remove_token(entry=entry))

    rtnval = ",".join(rendered)
    return rtnval


def _tag_to_letter(*, tag: str) -> str:
    """
        Translate a POSIX ACL tag to its single-letter ``setfacl``
        prefix.

        :param tag: One of ``"user"``, ``"group"``, ``"mask"``,
                    ``"other"``.
    """
    if tag == "user":
        rtnval = "u"
        return rtnval
    if tag == "group":
        rtnval = "g"
        return rtnval
    if tag == "mask":
        rtnval = "m"
        return rtnval
    if tag == "other":
        rtnval = "o"
        return rtnval

    err_msg = "Unknown ACL tag: '{}'".format(tag)
    raise ValueError(err_msg)


def _letter_to_tag(*, letter: str) -> Optional[str]:
    """
        Inverse of :func:`_tag_to_letter`.  Accepts the long-form word
        emitted by ``getfacl`` (``"user"``, ``"group"``, ``"mask"``,
        ``"other"``).  Returns ``None`` for unrecognised inputs so
        callers can skip the row.

        :param letter: The token from the parsed ``getfacl`` row.
    """
    if letter == "user":
        rtnval: Optional[str] = "user"
        return rtnval
    if letter == "group":
        rtnval = "group"
        return rtnval
    if letter == "mask":
        rtnval = "mask"
        return rtnval
    if letter == "other":
        rtnval = "other"
        return rtnval

    rtnval = None
    return rtnval


# ----------------------------------------------------------------------
# Module-level helpers: getfacl parsing
# ----------------------------------------------------------------------


def _parse_getfacl_single(*, stdout: str, path: str) -> AclSet:
    """
        Parse the output of a single-path ``getfacl --omit-header``
        call into an :class:`AclSet`.

        :param stdout: The captured stdout.
        :param path: The path the read was for.

        :returns: The parsed :class:`AclSet`.
    """
    info = AclSet(path=path)
    for raw_line in stdout.splitlines():
        stripped = raw_line.strip()
        if len(stripped) == 0:
            continue
        if stripped.startswith("#"):
            continue

        entry = _parse_getfacl_entry_line(line=stripped)
        if entry is None:
            continue

        if entry.is_default:
            info.default_entries.append(entry)
        else:
            info.entries.append(entry)

        if entry.tag == "mask":
            info.has_mask = True

    rtnval = info
    return rtnval


def _parse_getfacl_recursive(*, stdout: str) -> dict:
    """
        Parse multi-block ``getfacl -R --omit-header`` output into a
        dict keyed by path.

        Each block begins with a ``# file: <path>`` header (we keep
        the header even though ``--omit-header`` strips the
        owner/group lines, because the file marker itself is needed
        to key the dict).  When a block has no header line we silently
        drop it.

        :param stdout: The captured stdout.

        :returns: A ``dict[str, AclSet]``.
    """
    rtnval: dict = {}

    current_path: Optional[str] = None
    current_set: Optional[AclSet] = None

    for raw_line in stdout.splitlines():
        stripped = raw_line.strip()

        if len(stripped) == 0:
            if current_path is not None and current_set is not None:
                rtnval[current_path] = current_set
                current_path = None
                current_set = None
            continue

        if stripped.startswith("# file:"):
            if current_path is not None and current_set is not None:
                rtnval[current_path] = current_set
            file_value = stripped[len("# file:"):].strip()
            current_path = file_value
            current_set = AclSet(path=file_value)
            continue

        if stripped.startswith("#"):
            continue

        if current_set is None:
            continue

        entry = _parse_getfacl_entry_line(line=stripped)
        if entry is None:
            continue

        if entry.is_default:
            current_set.default_entries.append(entry)
        else:
            current_set.entries.append(entry)

        if entry.tag == "mask":
            current_set.has_mask = True

    if current_path is not None and current_set is not None:
        rtnval[current_path] = current_set
    return rtnval


def _parse_getfacl_entry_line(*, line: str) -> Optional[AclEntry]:
    """
        Parse a single ``getfacl`` entry row into an
        :class:`AclEntry`.  Trailing ``#effective:<rights>`` comments
        (mask-clamped effective rights) are stripped before parsing.

        :param line: The non-comment, non-empty entry row.

        :returns: The parsed entry, or ``None`` for unrecognised
                  rows.
    """
    hash_index = line.find("#")
    if hash_index >= 0:
        body = line[:hash_index].strip()
    else:
        body = line.strip()

    if len(body) == 0:
        rtnval: Optional[AclEntry] = None
        return rtnval

    if body.startswith("default:"):
        is_default = True
        body = body[len("default:"):]
    else:
        is_default = False

    parts = body.split(":")
    if len(parts) != 3:
        rtnval = None
        return rtnval

    tag_word = parts[0]
    qualifier_token = parts[1]
    perms_token = parts[2]

    tag = _letter_to_tag(letter=tag_word)
    if tag is None:
        rtnval = None
        return rtnval

    if len(qualifier_token) == 0:
        qualifier_value: Optional[str] = None
    else:
        qualifier_value = qualifier_token

    rtnval = AclEntry(
        tag=tag,
        qualifier=qualifier_value,
        perms=perms_token,
        is_default=is_default,
    )
    return rtnval


def _entries_already_present(
    *,
    current: AclSet,
    requested: list,
) -> bool:
    """
        Return True when every requested :class:`AclEntry` is already
        present on the parsed :class:`AclSet` with matching perms.

        :param current: The parsed live ACL.
        :param requested: The desired entries.
    """
    rtnval = True
    for want in requested:
        if want.is_default:
            haystack = current.default_entries
        else:
            haystack = current.entries

        found = False
        for have in haystack:
            if have.tag != want.tag:
                continue
            if have.qualifier != want.qualifier:
                continue
            if have.perms != want.perms:
                continue
            found = True
            break

        if not found:
            rtnval = False
            break
    return rtnval


# ----------------------------------------------------------------------
# Module-level helpers: lsattr parsing
# ----------------------------------------------------------------------


def _parse_lsattr_line(
    *,
    line: str,
    fallback_path: str,
) -> Optional[FileAttributes]:
    """
        Parse a single ``lsattr -d`` row into a :class:`FileAttributes`.

        ``lsattr -d`` rows look like::

            ----i-------------- /etc/resolv.conf
            -----a------------- /var/log/messages

        The flag column is whitespace-separated from the path.

        :param line: The non-empty stripped line.
        :param fallback_path: The path to assign when the line carries
                              only the flag column (rare, but seen for
                              ``lsattr`` builds that print no path).

        :returns: The parsed :class:`FileAttributes`, or ``None`` when
                  the line is not parseable as an lsattr row.
    """
    parts = line.split(None, 1)
    if len(parts) == 0:
        rtnval: Optional[FileAttributes] = None
        return rtnval

    flag_column = parts[0]
    if len(parts) >= 2:
        path_value = parts[1].strip()
    else:
        path_value = fallback_path

    info = FileAttributes(path=path_value, raw_flags=flag_column)
    for letter, attr_name in _FILE_ATTR_FLAG_MAP.items():
        if letter in flag_column:
            setattr(info, attr_name, True)

    rtnval = info
    return rtnval


def _first_non_empty_line(*, text: str) -> Optional[str]:
    """
        Return the first non-empty stripped line of ``text``, or
        ``None`` when none exists.

        :param text: The captured runner output.
    """
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if len(stripped) > 0:
            rtnval: Optional[str] = stripped
            return rtnval

    rtnval = None
    return rtnval


def _flags_missing(*, current: FileAttributes, requested: str) -> list:
    """
        Return the subset of ``requested`` flags not already set on
        ``current``.

        :param current: The parsed :class:`FileAttributes`.
        :param requested: The requested concatenated flag letters.
    """
    rtnval: list = []
    raw = current.raw_flags
    for letter in requested:
        if letter not in raw:
            rtnval.append(letter)
    return rtnval


def _flags_still_set(*, current: FileAttributes, requested: str) -> list:
    """
        Return the subset of ``requested`` flags that are currently
        set on ``current`` and would therefore actually be removed.

        :param current: The parsed :class:`FileAttributes`.
        :param requested: The requested concatenated flag letters.
    """
    rtnval: list = []
    raw = current.raw_flags
    for letter in requested:
        if letter in raw:
            rtnval.append(letter)
    return rtnval


def _flags_match_exactly(
    *,
    current: FileAttributes,
    requested: str,
) -> bool:
    """
        Return True when the flags currently set on ``current``
        exactly match the requested mask (used by ``set_attrs``
        which performs ``chattr =<flags>``).

        :param current: The parsed :class:`FileAttributes`.
        :param requested: The requested concatenated flag letters.
    """
    requested_set: set = set(requested)

    current_set: set = set()
    raw = current.raw_flags
    for letter in _FILE_ATTR_FLAG_MAP.keys():
        if letter in raw:
            current_set.add(letter)

    rtnval = (current_set == requested_set)
    return rtnval


# ----------------------------------------------------------------------
# Module-level helpers: getcap parsing
# ----------------------------------------------------------------------


def _parse_getcap_line(
    *,
    line: str,
    fallback_path: str,
) -> Optional[FileCapabilities]:
    """
        Parse a single ``getcap`` row into a :class:`FileCapabilities`.

        ``getcap`` output looks like::

            /usr/bin/ping cap_net_raw=ep
            /usr/bin/foo  cap_net_bind_service,cap_net_admin=eip

        The path is followed by whitespace and a single capability set
        of the form ``<caps>=<flags>``.  Multiple ``cap=flags`` blocks
        are space-separated when present.

        :param line: The non-empty stripped row.
        :param fallback_path: Path to assign if the row contains only
                              a capability set (rare).

        :returns: A :class:`FileCapabilities`, or ``None`` when the
                  row cannot be parsed.
    """
    parts = line.split(None, 1)
    if len(parts) < 2:
        # Could be just a path with no caps -- treat as empty.
        info = FileCapabilities(path=line, raw=line)
        rtnval: Optional[FileCapabilities] = info
        return rtnval

    path_value = parts[0]
    cap_blob = parts[1].strip()

    info = FileCapabilities(path=path_value, raw=line)

    # Multiple ``cap=flags`` blocks are separated by spaces.
    blocks: list = []
    for chunk in cap_blob.split():
        if "=" in chunk:
            blocks.append(chunk)

    for block in blocks:
        eq_index = block.rfind("=")
        if eq_index < 0:
            continue
        cap_part = block[:eq_index]
        flag_part = block[eq_index + 1:]

        cap_names: list = []
        for name in cap_part.split(","):
            cleaned = name.strip()
            if len(cleaned) == 0:
                continue
            cap_names.append(cleaned)

        if "e" in flag_part:
            for name in cap_names:
                info.effective.append(name)
        if "p" in flag_part:
            for name in cap_names:
                info.permitted.append(name)
        if "i" in flag_part:
            for name in cap_names:
                info.inheritable.append(name)

    if path_value == "":
        info.path = fallback_path

    rtnval = info
    return rtnval


def _caps_match(*, current: FileCapabilities, requested: str) -> bool:
    """
        Return True when the parsed live capability set matches
        ``requested`` after canonical normalisation.

        Canonicalisation: each ``cap=flags`` block is sorted by
        capability name and the flag string is sorted alphabetically.
        Block ordering within the spec is also sorted.  This makes
        ``cap_net_raw=ep`` equal to ``cap_net_raw=pe`` and is robust
        to caller / kernel ordering differences.

        :param current: The parsed :class:`FileCapabilities`.
        :param requested: The requested ``setcap`` capability spec.
    """
    canonical_requested = _canonicalise_cap_spec(spec=requested)
    canonical_current = _capabilities_to_canonical(info=current)
    rtnval = (canonical_requested == canonical_current)
    return rtnval


def _canonicalise_cap_spec(*, spec: str) -> str:
    """
        Normalise a ``setcap`` spec string into a canonical form for
        comparison.

        :param spec: The raw spec string, e.g.
                     ``"cap_net_raw=ep"`` or
                     ``"cap_net_admin,cap_net_raw=eip"``.
    """
    blocks: list = []
    for raw_block in spec.split():
        cleaned = raw_block.strip()
        if len(cleaned) == 0:
            continue
        if "=" not in cleaned:
            continue
        eq_index = cleaned.rfind("=")
        cap_part = cleaned[:eq_index]
        flag_part = cleaned[eq_index + 1:]

        cap_names: list = []
        for name in cap_part.split(","):
            stripped_name = name.strip()
            if len(stripped_name) == 0:
                continue
            cap_names.append(stripped_name)
        cap_names_sorted = sorted(cap_names)

        flag_letters = sorted(set(flag_part))
        flag_canonical = "".join(flag_letters)

        block_canonical = "{}={}".format(
            ",".join(cap_names_sorted), flag_canonical,
        )
        blocks.append(block_canonical)

    blocks_sorted = sorted(blocks)
    rtnval = " ".join(blocks_sorted)
    return rtnval


def _capabilities_to_canonical(*, info: FileCapabilities) -> str:
    """
        Render a :class:`FileCapabilities` snapshot back into the same
        canonical form produced by :func:`_canonicalise_cap_spec` so
        that direct comparison is possible.

        We bucket capabilities by the set of flags they carry, then
        emit one ``<caps>=<flags>`` block per bucket.

        :param info: The parsed :class:`FileCapabilities`.
    """
    # Build a {cap_name: set_of_flag_letters} map.
    cap_to_flags: dict = {}

    for name in info.effective:
        if name not in cap_to_flags:
            cap_to_flags[name] = set()
        cap_to_flags[name].add("e")
    for name in info.permitted:
        if name not in cap_to_flags:
            cap_to_flags[name] = set()
        cap_to_flags[name].add("p")
    for name in info.inheritable:
        if name not in cap_to_flags:
            cap_to_flags[name] = set()
        cap_to_flags[name].add("i")

    # Reverse the map: {flag_letters_canonical: [cap_name, ...]}.
    flags_to_caps: dict = {}
    for cap_name, flag_set in cap_to_flags.items():
        flag_canonical = "".join(sorted(flag_set))
        if flag_canonical not in flags_to_caps:
            flags_to_caps[flag_canonical] = []
        flags_to_caps[flag_canonical].append(cap_name)

    blocks: list = []
    for flag_canonical, cap_names in flags_to_caps.items():
        cap_names_sorted = sorted(cap_names)
        block = "{}={}".format(
            ",".join(cap_names_sorted), flag_canonical,
        )
        blocks.append(block)

    blocks_sorted = sorted(blocks)
    rtnval = " ".join(blocks_sorted)
    return rtnval
