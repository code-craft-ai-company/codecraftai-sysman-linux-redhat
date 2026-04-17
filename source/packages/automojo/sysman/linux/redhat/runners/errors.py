# automojo.sysman.linux.redhat.runners.errors
#
# Error types raised by command runner implementations.

import os

from automojo.sysman.linux.redhat.runners.models import SSHErrorType


class SSHConnectionError(Exception):
    """
        Raised when an SSH connection cannot be established after exhausting
        the configured retry attempts.

        :param host: Target host name or address.
        :param port: Target TCP port.
        :param error_type: Classification of the terminal failure.
        :param attempts: Number of connection attempts made.
        :param last_error: String representation of the last underlying exception.
    """

    def __init__(
        self,
        *,
        host: str,
        port: int,
        error_type: SSHErrorType,
        attempts: int,
        last_error: str,
    ) -> None:
        self.host = host
        self.port = port
        self.error_type = error_type
        self.attempts = attempts
        self.last_error = last_error

        err_msg_lines = [
            "Failed to establish SSH connection."
        ]
        err_msg_lines.append("    HOST: {}".format(host))
        err_msg_lines.append("    PORT: {}".format(port))
        err_msg_lines.append("    ERROR_TYPE: {}".format(error_type.value))
        err_msg_lines.append("    ATTEMPTS: {}".format(attempts))
        err_msg_lines.append("    LAST_ERROR: {}".format(last_error))
        err_msg = os.linesep.join(err_msg_lines)

        super().__init__(err_msg)
        return
