# WhatsBot — shared launcher helpers (POSIX).
#
# Single source of truth for the OS-agnostic pieces duplicated across the
# native dev launchers (linux_start.sh, macos_start.command). Sourced — never
# executed directly. Keep it POSIX-sh clean (no bashisms) so it works under the
# /bin/bash of both Linux and macOS.
#
# NOTE: windows_start.bat cannot source a shell file. When changing the values
# below (especially the reload-dir set), mirror the change in windows_start.bat by hand —
# search for "scripts/_common.sh" there. (The Windows GOWA is the tracked bin/gowa.exe.)

# Version of the GOWA binary the launchers download. Single source of truth: the tracked
# GOWA_VERSION file at the repo root (also read by the Dockerfile and by the gowa plugin's
# updater). Overridable via the GOWA_VERSION env var. Both launchers cd to the repo root
# before sourcing this file.
GOWA_VERSION="${GOWA_VERSION:-$(cat ./GOWA_VERSION 2>/dev/null || echo 9.6.0)}"

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
