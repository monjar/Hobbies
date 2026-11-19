import pytest

from clipforge.errors import PolicyDenied
from clipforge.ingest.policy import RULE_LOCAL_INBOX, check_local, check_url


def test_file_in_inbox_is_allowed(settings, inbox_clip):
    decision = check_local(inbox_clip, settings)
    assert decision.allowed and decision.rule == RULE_LOCAL_INBOX


def test_file_outside_inbox_is_denied(settings, fixture_clip):
    decision = check_local(fixture_clip, settings)
    assert not decision.allowed and decision.rule is None
    with pytest.raises(PolicyDenied):
        decision.require()


def test_symlink_out_of_inbox_is_denied(settings, fixture_clip):
    link = settings.inbox / "link.mp4"
    link.symlink_to(fixture_clip)
    assert not check_local(link, settings).allowed


def test_path_traversal_is_denied(settings, fixture_clip, root):
    outside = root / "elsewhere.mp4"
    outside.write_bytes(b"x")
    assert not check_local(settings.inbox / ".." / "elsewhere.mp4", settings).allowed


def test_urls_are_denied_by_default(settings):
    assert not check_url("https://www.youtube.com/watch?v=abc", settings).allowed


def test_denied_source_writes_nothing(settings, db, make_pipeline, fixture_clip):
    with pytest.raises(PolicyDenied):
        make_pipeline().make(fixture_clip, "detail-spotting", live=False)
    assert db.one("SELECT COUNT(*) FROM jobs")[0] == 0
    assert db.one("SELECT COUNT(*) FROM sources")[0] == 0
    assert not settings.work.exists() and not settings.out.exists()
