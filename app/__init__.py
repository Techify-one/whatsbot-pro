"""Application service layer (Plano 23 · Wave 2).

The ``app.services`` package holds the cohesive service modules extracted from the
route layer under Branch by Abstraction: the route handlers in ``server/routes/``
delegate their domain logic here so the routes stay thin (HTTP wiring) and the
behavior lives in one testable place. Services receive their infrastructure
(``deps``, ``ws_manager``, ``agent_handler``, the outbound router, …) as explicit
parameters / a small context object and MUST NOT import ``server.app`` (that would
create an import cycle and pull in the whole web layer).
"""
