"""Applying a reviewed profile to the stored one (profile_service.apply_patch):
provenance of edited and added items, review flags, conflicts and versions."""

from typing import Any

import pytest

from app.errors import ValidationFailed
from app.schemas.common import utc_now
from app.schemas.documents import ProfileDoc
from app.schemas.profiles import (
    Conflict,
    ConflictValue,
    Contact,
    IndexProgress,
    ProfileBullet,
    ProfilePatchRequest,
    ProfileRecord,
    SourceRef,
)
from app.services.profile_service import apply_patch
from tests.factories import make_profile

REF = SourceRef(
    source_id="source-1", source_label="Resume", start=10, end=40, excerpt="Built Python pipelines"
)


def stored_profile(**overrides: Any) -> ProfileDoc:
    """A confirmed, indexed profile with one flagged bullet and one open conflict."""
    role = ProfileRecord(
        record_id="record-1",
        category="employment",
        title="Data Engineer",
        organization="Northwind Labs",
        start_date="Jan 2020",
        end_date="Mar 2021",
        bullets=[
            ProfileBullet(
                bullet_id="bullet-1",
                text="Built Python pipelines",
                source_ref=REF,
                provenance="extracted",
            ),
            ProfileBullet(
                bullet_id="bullet-2",
                text="Managed a team of 30",
                provenance="extracted",
                needs_review=True,
                review_reasons=["source span not found"],
            ),
        ],
        source_ref=REF,
        provenance="extracted",
        needs_review=True,
        review_reasons=["Unclear whether this was full time."],
    )
    skills = ProfileRecord(
        record_id="record-2",
        category="skill",
        title="Languages",
        skills=["Python", "SQL"],
        source_ref=REF,
        provenance="extracted",
    )
    conflict = Conflict(
        conflict_id="conflict-1",
        field="start_date",
        description="The sources give different start dates.",
        record_ids=["record-1"],
        values=[ConflictValue(value="Jan 2020"), ConflictValue(value="Feb 2020")],
    )
    values: dict[str, Any] = {
        "version": 4,
        "status": "confirmed",
        "index_state": "indexed",
        "indexed_version": 4,
        "index_progress": IndexProgress(total=5, embedded=5),
        "contact": Contact(name="Jordan Rivera", email="jordan@example.com"),
        "records": [role, skills],
        "conflicts": [conflict],
    }
    values.update(overrides)
    return make_profile("owner-1", **values)


def unchanged_body(profile: ProfileDoc) -> dict[str, Any]:
    """The PATCH body a client sends when it saves the profile as it is."""
    editable = {
        "record_id",
        "category",
        "title",
        "organization",
        "location",
        "start_date",
        "end_date",
        "summary",
        "skills",
    }
    return {
        "expected_version": profile.version,
        "contact": profile.contact.model_dump(),
        "records": [
            {
                **record.model_dump(include=editable),
                "bullets": [
                    {"bullet_id": bullet.bullet_id, "text": bullet.text}
                    for bullet in record.bullets
                ],
            }
            for record in profile.records
        ],
    }


def patch(profile: ProfileDoc, body: dict[str, Any]) -> ProfileDoc:
    return apply_patch(profile, ProfilePatchRequest.model_validate(body), utc_now())


def test_saving_an_unchanged_profile_changes_nothing() -> None:
    profile = stored_profile()
    assert patch(profile, unchanged_body(profile)) is profile


def test_any_change_bumps_the_version_and_requires_a_new_confirmation() -> None:
    profile = stored_profile()
    body = unchanged_body(profile)
    body["contact"]["phone"] = "555-0100"

    updated = patch(profile, body)

    assert updated.version == 5
    assert updated.status == "draft"
    assert updated.index_state == "not_indexed"
    assert updated.indexed_version is None
    assert updated.index_progress == IndexProgress()
    assert updated.contact.phone == "555-0100"
    assert updated.profile_id == profile.profile_id
    assert updated.created_at == profile.created_at
    assert updated.updated_at >= profile.updated_at
    # Nothing else was touched, so the records keep their provenance.
    assert updated.records == profile.records


def test_edited_bullet_becomes_user_edited_and_keeps_its_source_reference() -> None:
    profile = stored_profile()
    body = unchanged_body(profile)
    body["records"][0]["bullets"][0]["text"] = "Built Python and SQL pipelines"

    edited, untouched = patch(profile, body).records[0].bullets

    assert edited.bullet_id == "bullet-1"
    assert edited.text == "Built Python and SQL pipelines"
    assert edited.provenance == "user_edited"
    assert edited.source_ref == REF
    # The other bullet is unchanged: same provenance, still flagged.
    assert untouched == profile.records[0].bullets[1]


def test_editing_a_flagged_item_clears_its_review_flag() -> None:
    profile = stored_profile()
    body = unchanged_body(profile)
    body["records"][0]["bullets"][1]["text"] = "Managed a team of 3"
    body["records"][0]["title"] = "Senior Data Engineer"

    record = patch(profile, body).records[0]

    assert record.provenance == "user_edited"
    assert (record.needs_review, record.review_reasons) == (False, [])
    assert record.source_ref == REF
    assert record.bullets[1].provenance == "user_edited"
    assert (record.bullets[1].needs_review, record.bullets[1].review_reasons) == (False, [])
    # A bullet that was not edited stays extracted even though its record changed.
    assert record.bullets[0].provenance == "extracted"


def test_unchanged_flagged_item_stays_flagged() -> None:
    profile = stored_profile()
    body = unchanged_body(profile)
    body["contact"]["location"] = "Boston, MA"

    record = patch(profile, body).records[0]

    assert record.needs_review is True
    assert record.bullets[1].needs_review is True


def test_new_record_and_new_bullet_are_user_added() -> None:
    profile = stored_profile()
    body = unchanged_body(profile)
    body["records"][0]["bullets"].append({"bullet_id": None, "text": "Wrote the runbook"})
    body["records"].append(
        {
            "record_id": None,
            "category": "project",
            "title": "Bird Feeder Camera",
            "bullets": [{"text": "Trained a small classifier"}],
            "skills": ["OpenCV", "opencv", "Python"],
        }
    )

    updated = patch(profile, body)

    added_bullet = updated.records[0].bullets[2]
    assert added_bullet.provenance == "user_added"
    assert added_bullet.source_ref is None
    assert added_bullet.bullet_id not in {"bullet-1", "bullet-2"}
    # Adding a bullet does not turn the record itself into an edited one.
    assert updated.records[0].provenance == "extracted"

    project = updated.records[2]
    assert project.provenance == "user_added"
    assert project.source_ref is None
    assert project.record_id not in {"record-1", "record-2"}
    assert project.bullets[0].provenance == "user_added"
    assert project.skills == ["OpenCV", "Python"]


def test_item_added_by_the_user_stays_user_added_when_edited_again() -> None:
    profile = stored_profile()
    body = unchanged_body(profile)
    body["records"][0]["bullets"].append({"text": "Wrote the runbook"})
    first = patch(profile, body)

    body = unchanged_body(first)
    body["records"][0]["bullets"][2]["text"] = "Wrote the on-call runbook"
    second = patch(first, body)

    assert second.version == 6
    assert second.records[0].bullets[2].provenance == "user_added"


def test_removed_items_are_gone_and_skill_changes_mark_the_group_edited() -> None:
    profile = stored_profile()
    body = unchanged_body(profile)
    del body["records"][0]["bullets"][1]
    body["records"][1]["skills"] = ["Python", "SQL", "Rust"]

    role, skills = patch(profile, body).records

    assert [bullet.bullet_id for bullet in role.bullets] == ["bullet-1"]
    assert skills.skills == ["Python", "SQL", "Rust"]
    assert skills.provenance == "user_edited"


def test_dates_are_stored_exactly_as_the_user_typed_them() -> None:
    profile = stored_profile()
    body = unchanged_body(profile)
    body["records"][0]["start_date"] = " early 2020 "
    body["records"][0]["end_date"] = ""

    record = patch(profile, body).records[0]

    assert record.start_date == "early 2020"
    assert record.end_date is None


@pytest.mark.parametrize(
    ("mutate", "field"),
    [
        (lambda body: body["records"][0].update(record_id="record-9"), "records.0.record_id"),
        (lambda body: body["records"][1].update(record_id="record-1"), "records.1.record_id"),
        (
            lambda body: body["records"][0]["bullets"][0].update(bullet_id="bullet-9"),
            "records.0.bullets.0.bullet_id",
        ),
        (
            lambda body: body["records"][0]["bullets"][1].update(bullet_id="bullet-1"),
            "records.0.bullets.1.bullet_id",
        ),
        (
            lambda body: body.update(
                conflict_resolutions=[{"conflict_id": "conflict-9", "resolution": "dismissed"}]
            ),
            "conflict_resolutions.0.conflict_id",
        ),
    ],
)
def test_unknown_or_repeated_ids_are_rejected(mutate: Any, field: str) -> None:
    profile = stored_profile()
    body = unchanged_body(profile)
    mutate(body)

    with pytest.raises(ValidationFailed) as error:
        patch(profile, body)

    assert error.value.field_errors[0]["field"] == field


def test_conflict_is_resolved_only_by_an_explicit_decision() -> None:
    profile = stored_profile()
    body = unchanged_body(profile)
    body["contact"]["phone"] = "555-0100"
    assert patch(profile, body).conflicts[0].resolution == "unresolved"

    body["conflict_resolutions"] = [
        {"conflict_id": "conflict-1", "resolution": "resolved", "note": "The resume is right."}
    ]
    resolved = patch(profile, body).conflicts[0]

    assert resolved.resolution == "resolved"
    assert resolved.note == "The resume is right."
    assert resolved.values == profile.conflicts[0].values


def test_resolving_a_conflict_alone_is_a_change() -> None:
    profile = stored_profile()
    body = unchanged_body(profile)
    body["conflict_resolutions"] = [{"conflict_id": "conflict-1", "resolution": "dismissed"}]

    updated = patch(profile, body)

    assert updated.version == 5
    assert updated.conflicts[0].resolution == "dismissed"


def test_deleting_the_record_of_a_conflict_does_not_resolve_it() -> None:
    profile = stored_profile()
    body = unchanged_body(profile)
    del body["records"][0]

    conflict = patch(profile, body).conflicts[0]

    assert conflict.record_ids == []
    assert conflict.resolution == "unresolved"
