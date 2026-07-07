# WhatsBot — shared launcher helpers (POSIX).
#
# Single source of truth for the OS-agnostic pieces duplicated across the
# native dev launchers (linux_start.sh, macos_start.command). Sourced — never
# executed directly. Keep it POSIX-sh clean (no bashisms) so it works under the
# /bin/bash of both Linux and macOS.
#
# NOTE: windows_start.bat cannot source a shell file. When changing the values
# below (especially GOWA_VERSION or the reload-dir set), mirror the change in
# windows_start.bat by hand — search for "scripts/_common.sh" there.

# Version of the GOWA binary that matches the client in gowa/client.py and the
# Dockerfile (ARG GOWA_VERSION). Overridable via the GOWA_VERSION env var.
GOWA_VERSION="${GOWA_VERSION:-8.8.0}"

# Directories uvicorn watches for hot-reload (core + plugins). uvicorn validates
# each --reload-dir before booting, so storages/plugins must exist (it is created
# at runtime by create_app on a fresh install — callers mkdir -p it).
WHATSBOT_RELOAD_DIRS="server agent app ai_engine config gowa channels db plugins storages/plugins"

# Emit the --reload-dir flag list for the uvicorn invocation. Usage:
#   ./venv/bin/python -m uvicorn server.dev:app --reload $(whatsbot_reload_flags) ...
whatsbot_reload_flags() {
    for _d in $WHATSBOT_RELOAD_DIRS; do
        printf -- '--reload-dir %s ' "$_d"
    done
}
