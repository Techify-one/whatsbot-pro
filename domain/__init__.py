"""Minimal domain package (plano 23 §2.1).

Holds only what needs a neutral home to break a real import cycle — currently
the permission catalog (``db.repositories.rbac_repo`` needs it without importing
``server``). It is NOT a layer of domain entities.
"""
