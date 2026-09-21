from domain.permission_catalog import CORE_GROUP_ORDER, group_for


TEAM_PERMISSIONS = {
    "team.manage",
    "conversation.team.assign",
    "conversation.team.assign_any",
    "conversation.team.read_any",
}


def test_team_permissions_have_a_dedicated_group():
    assert {key: group_for(key) for key in TEAM_PERMISSIONS} == {
        key: ("core", "Times") for key in TEAM_PERMISSIONS
    }


def test_team_group_is_shown_after_conversations():
    conversations_index = CORE_GROUP_ORDER.index("Atendimentos e conversas")
    assert CORE_GROUP_ORDER[conversations_index + 1] == "Times"
