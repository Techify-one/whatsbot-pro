"""WhatsApp (GOWA) channel plugin (plano 13 Fase 2).

Bundled + active by default + uninstallable. The plugin OWNS the GOWA subprocess
and the status/QR/avatar polling loops; disabling it tears them down and the core
keeps running with zero or the remaining channels.
"""
