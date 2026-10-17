"""Operating-system account of the current process. (S1-04)

getpass.getuser() is deliberately not used: it reads LOGNAME / USER / USERNAME first, so anyone
could become an administrator by setting a variable. The login comes from the OS instead:
the process user ID on POSIX, GetUserNameW (os.getlogin) on Windows.
"""

from __future__ import annotations

import os
import sys

from hunter.application.errors import ErrorCode, HunterError


def os_login() -> str:
    try:
        if sys.platform == "win32":
            return os.getlogin().casefold()  # Windows logins are case-insensitive
        import pwd

        return pwd.getpwuid(os.getuid()).pw_name
    except (OSError, KeyError) as exc:
        raise HunterError(
            ErrorCode.UNAUTHENTICATED,
            f"не удалось определить учётную запись ОС ({type(exc).__name__})",
        ) from None


class OsIdentity:
    def current_identity(self) -> str:
        return f"os:{os_login()}"
