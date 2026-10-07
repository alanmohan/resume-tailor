"""Reading and editing the profile draft (GET and PATCH /api/profile).

An edit never touches the original source text. The request carries the whole
reviewed profile; it is compared with the stored one item by item:

- an item whose text changed becomes ``user_edited`` (the user's own
  statement); its original source reference is kept for reference only;
- an item without an ID is new and becomes ``user_added``;
- an untouched item keeps its provenance, source reference and review flag.

Any change increases the version and puts the profile back into draft, so it
has to be confirmed and indexed again before documents can be generated.
"""

from datetime import datetime

from app.errors import NotFound, ValidationFailed, VersionConflict
from app.repositories import Repositories
from app.schemas.common import Provenance, new_id, utc_now
from app.schemas.documents import ProfileDoc
from app.schemas.profiles import (
    Conflict,
    ConflictResolutionInput,
    Contact,
    IndexProgress,
    Profile,
    ProfileBullet,
    ProfileBulletInput,
    ProfilePatchRequest,
    ProfileRecord,
    ProfileRecordInput,
)
from app.security import SessionContext
from app.services.ingestion import unique_texts

# The record fields a user can edit; a change to any of them makes the record user_edited.
RECORD_FIELDS = (
    "category",
    "title",
    "organization",
    "location",
    "start_date",
    "end_date",
    "summary",
    "skills",
)


async def load_profile(repos: Repositories, owner_id: str) -> ProfileDoc:
    """The owner's profile, or 404 when the session has none yet."""
    profile = await repos.profiles.get_for_owner(owner_id)
    if profile is None:
        raise NotFound("No profile has been created in this session yet.")
    return profile


def version_conflict(current_version: int) -> VersionConflict:
    return VersionConflict(details={"current_version": current_version})


def _after_edit(provenance: Provenance) -> Provenance:
    """An extracted item becomes user_edited; an item the user added stays theirs."""
    return "user_added" if provenance == "user_added" else "user_edited"


def _review_bullet(stored: ProfileBullet | None, text: str) -> ProfileBullet:
    if stored is None:
        return ProfileBullet(bullet_id=new_id(), text=text, provenance="user_added")
    if stored.text == text:
        return stored
    # The user rewrote it, so it is their statement now and needs no further review.
    return stored.model_copy(
        update={
            "text": text,
            "provenance": _after_edit(stored.provenance),
            "needs_review": False,
            "review_reasons": [],
        }
    )


def _review_bullets(
    stored: list[ProfileBullet], incoming: list[ProfileBulletInput], field: str
) -> list[ProfileBullet]:
    by_id = {bullet.bullet_id: bullet for bullet in stored}
    used: set[str] = set()
    bullets = []
    for position, bullet in enumerate(incoming):
        bullet_id = bullet.bullet_id
        if bullet_id is not None and (bullet_id not in by_id or bullet_id in used):
            raise ValidationFailed.for_field(
                f"{field}.{position}.bullet_id",
                "Unknown or repeated bullet_id. Send null for a statement you added.",
            )
        if bullet_id is not None:
            used.add(bullet_id)
        bullets.append(_review_bullet(by_id.get(bullet_id) if bullet_id else None, bullet.text))
    return bullets


def _review_record(
    stored: ProfileRecord | None, incoming: ProfileRecordInput, field: str
) -> ProfileRecord:
    fields = {name: getattr(incoming, name) for name in RECORD_FIELDS}
    fields["skills"] = unique_texts(incoming.skills)
    if stored is None:
        return ProfileRecord(
            record_id=new_id(),
            **fields,
            bullets=_review_bullets([], incoming.bullets, f"{field}.bullets"),
            provenance="user_added",
        )
    update = {"bullets": _review_bullets(stored.bullets, incoming.bullets, f"{field}.bullets")}
    if any(getattr(stored, name) != fields[name] for name in RECORD_FIELDS):
        update |= fields | {
            "provenance": _after_edit(stored.provenance),
            "needs_review": False,
            "review_reasons": [],
        }
    return stored.model_copy(update=update)


def _review_records(
    stored: list[ProfileRecord], incoming: list[ProfileRecordInput]
) -> list[ProfileRecord]:
    by_id = {record.record_id: record for record in stored}
    used: set[str] = set()
    records = []
    for position, record in enumerate(incoming):
        record_id = record.record_id
        if record_id is not None and (record_id not in by_id or record_id in used):
            raise ValidationFailed.for_field(
                f"records.{position}.record_id",
                "Unknown or repeated record_id. Send null for a record you added.",
            )
        if record_id is not None:
            used.add(record_id)
        stored_record = by_id.get(record_id) if record_id else None
        records.append(_review_record(stored_record, record, f"records.{position}"))
    return records


def _resolve_conflicts(
    stored: list[Conflict], resolutions: list[ConflictResolutionInput], record_ids: set[str]
) -> list[Conflict]:
    """Apply the user's decisions. A conflict is never resolved implicitly:
    deleting the record it is about only removes that record's ID from it."""
    known = {conflict.conflict_id for conflict in stored}
    decisions: dict[str, ConflictResolutionInput] = {}
    for position, decision in enumerate(resolutions):
        if decision.conflict_id not in known:
            raise ValidationFailed.for_field(
                f"conflict_resolutions.{position}.conflict_id", "Unknown conflict_id."
            )
        decisions[decision.conflict_id] = decision

    conflicts = []
    for conflict in stored:
        update: dict[str, object] = {
            "record_ids": [rid for rid in conflict.record_ids if rid in record_ids]
        }
        decision = decisions.get(conflict.conflict_id)
        if decision is not None:
            update |= {"resolution": decision.resolution, "note": decision.note}
        conflicts.append(conflict.model_copy(update=update))
    return conflicts


def apply_patch(stored: ProfileDoc, body: ProfilePatchRequest, now: datetime) -> ProfileDoc:
    """The profile after the user's review. Returns ``stored`` itself when the
    request changes nothing, so saving an unchanged form does not invalidate
    the index or mark existing drafts stale."""
    records = _review_records(stored.records, body.records)
    contact = Contact(**body.contact.model_dump())
    conflicts = _resolve_conflicts(
        stored.conflicts, body.conflict_resolutions, {record.record_id for record in records}
    )
    if (contact, records, conflicts) == (stored.contact, stored.records, stored.conflicts):
        return stored
    return stored.model_copy(
        update={
            "version": stored.version + 1,
            "status": "draft",
            "index_state": "not_indexed",
            "indexed_version": None,
            "index_progress": IndexProgress(),
            "index_error": None,
            "contact": contact,
            "records": records,
            "conflicts": conflicts,
            "updated_at": now,
        }
    )


async def get_profile(*, session: SessionContext, repos: Repositories) -> Profile:
    profile = await load_profile(repos, session.owner_id)
    return profile.to_api(await repos.sources.list_for_owner(session.owner_id))


async def patch_profile(
    body: ProfilePatchRequest, *, session: SessionContext, repos: Repositories
) -> Profile:
    """PATCH /api/profile: save the reviewed profile with optimistic locking."""
    stored = await load_profile(repos, session.owner_id)
    if stored.version != body.expected_version:
        raise version_conflict(stored.version)
    updated = apply_patch(stored, body, utc_now())
    if updated is not stored:
        # Conditional on the version read above, so a concurrent edit (or a
        # "Clear my data" that removed the profile) makes this write a no-op.
        saved = await repos.profiles.replace(session.owner_id, updated, stored.version)
        if saved is None:
            raise VersionConflict()
        updated = saved
    return updated.to_api(await repos.sources.list_for_owner(session.owner_id))
