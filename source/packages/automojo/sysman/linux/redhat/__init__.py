# automojo.sysman.linux.redhat
#
# System management package for automating the maintenance of software
# configurations on Red Hat Linux systems.
#
# A coordinator in an external package drives maintenance activities by
# calling into the interfaces exposed by this package.  All system
# commands are executed through the automojo-interfaces command-runner
# abstractions so that execution can be mocked, audited, or run in
# dry-run mode without modification to the maintenance logic.
