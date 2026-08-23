from evidence.presentation import format_evidence_scope


def test_format_evidence_scope_for_docker_source_check() -> None:
    assert (
        format_evidence_scope(
            "source",
            {"runtime_user_verified": False},
        )
        == "Evidence scope: source-only (runtime_user_verified=False)"
    )


def test_format_evidence_scope_for_password_source_check() -> None:
    assert (
        format_evidence_scope(
            "source",
            {"runtime_auth_verified": False},
        )
        == "Evidence scope: source-only (runtime_auth_verified=False)"
    )


def test_format_evidence_scope_for_static_react_flow() -> None:
    assert (
        format_evidence_scope(
            "static_source_flow",
            {"browser_execution_verified": False},
        )
        == (
            "Evidence scope: static-source-flow "
            "(browser_execution_verified=False)"
        )
    )
