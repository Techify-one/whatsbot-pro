"""Service modules (Plano 23 · Wave 2).

* :mod:`app.services.messaging_service` — the OUTBOUND pipeline (reply send,
  media send, transcription delivery, tool-call broadcast, the typing-aware
  batch orchestrator) plus the shared ``broadcast_and_emit`` helper.
* :mod:`app.services.message_ingest_service` — the INBOUND ingest funnel
  (dedup, channel-aware contact resolution, echo suppression, the
  ``filter.message.before_save`` hook, the ``message.received``/``message.saved``
  emits).
"""
