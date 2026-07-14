"""Website widget channel plugin (plano 46 · sub-plano 05).

An embeddable live-chat box for any website — the *inverse* of the other
channels: inbound is a public endpoint the browser POSTs to; outbound
``send_text`` *pushes to the browser over a per-visitor WebSocket* instead of
calling an external API. ~90% reuses the existing pipeline (descriptor, inbound
funnel, per-channel AI, human handoff); the ~10% new is the per-visitor WS hub +
static SDK + public routes + session identity.
"""
