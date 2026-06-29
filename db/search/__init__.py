"""Query/search builders extracted from the contact/conversation repos
(plano 23 Fase E2).

Keeps the heavy SELECT assembly and the contact text-search logic out of the
thin repo facades. No observable behavior change — these modules produce the
same rows/shapes the repos used to build inline.
"""

from db.search.contact_search import (
    build_list_contacts_query,
    contact_ids_matching_message,
    fold,
    match_snippet,
)

__all__ = [
    "build_list_contacts_query",
    "contact_ids_matching_message",
    "fold",
    "match_snippet",
]
