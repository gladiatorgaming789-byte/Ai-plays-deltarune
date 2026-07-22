from deltarune_agent.build_status import BuildStatus, DEVELOPMENT_BRANCH


def test_current_development_branch_is_safe_when_remote_matches():
    status = BuildStatus(
        branch=DEVELOPMENT_BRANCH,
        revision="run11",
        ahead=0,
        behind=0,
        remote_checked=True,
    )

    assert status.safe_for_testing is True
    assert status.label == "DEVELOPMENT • up to date"


def test_wrong_branch_is_prominently_labeled():
    status = BuildStatus(
        branch="main",
        revision="run11",
        ahead=0,
        behind=5,
        remote_checked=True,
    )

    assert status.safe_for_testing is False
    assert status.label == "WRONG BRANCH: main"


def test_outdated_development_branch_is_not_safe():
    status = BuildStatus(
        branch=DEVELOPMENT_BRANCH,
        revision="run10",
        ahead=0,
        behind=3,
        remote_checked=True,
    )

    assert status.outdated is True
    assert status.safe_for_testing is False
    assert "OUTDATED" in status.label
    assert "3 commit(s) behind" in status.label


def test_unverified_remote_is_not_treated_as_current():
    status = BuildStatus(
        branch=DEVELOPMENT_BRANCH,
        revision="run11",
        ahead=None,
        behind=None,
        remote_checked=False,
        detail="offline",
    )

    assert status.safe_for_testing is False
    assert status.label == "DEVELOPMENT • update unverified"
