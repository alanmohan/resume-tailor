"""The fake provider's generation rules: deterministic and built from evidence only."""

from app.providers.base import (
    LLMClaimCheck,
    LLMContextEvidence,
    LLMContextRecord,
    LLMContextRequirement,
    LLMGenerationContext,
    LLMJobBrief,
    LLMRegenTarget,
)
from app.providers.fake.generation_ops import (
    CLOSING_PARAGRAPH,
    generate_documents,
    regenerate_item,
    verify_claims,
)


def record(alias: str, category: str, title: str) -> LLMContextRecord:
    return LLMContextRecord(
        alias=alias,
        category=category,
        title=title,
        organization="Somewhere",
        location=None,
        start_date=None,
        end_date=None,
    )


def evidence(
    alias: str, record_alias: str | None, text: str, kind: str = "statement"
) -> LLMContextEvidence:
    return LLMContextEvidence(
        alias=alias, record=record_alias, category="employment", kind=kind, text=text
    )


def need(
    alias: str, text: str, keywords: list[str], candidates: list[str]
) -> LLMContextRequirement:
    return LLMContextRequirement(
        alias=alias,
        text=text,
        importance="required",
        category="skill",
        keywords=keywords,
        candidate_evidence=candidates,
    )


def context(**overrides) -> LLMGenerationContext:
    values = {
        "job": LLMJobBrief(title="Platform Engineer", company="Globex", role_summary=None),
        "requirements": [
            need("R1", "Docker", ["docker"], ["E1"]),
            need("R2", "Docker and Kubernetes", ["docker", "kubernetes"], ["E1"]),
            need("R3", "Kubernetes", ["kubernetes"], []),
            need("R4", "Good communication", [], []),
            need("R5", "Experience running services in production", [], []),
        ],
        "records": [
            record("P1", "employment", "Software Engineer"),
            record("P2", "project", "TrailNotes"),
            record("P3", "education", "B.S. in Computer Science"),
        ],
        "contact_name": "Jordan Rivera",
        # "docker" and "Docker (Compose)" repeat a skill that is already listed.
        "profile_skills": ["Python", "Docker", "Go", "docker", "Docker (Compose)"],
        "evidence": [
            evidence(
                "E1", "P1", "- Containerised twelve services with Docker. Wrote the runbooks."
            ),
            evidence("E2", "P1", "Reduced cloud costs by 20% by rightsizing instances."),
            evidence("E3", "P2", "Built a hiking journal app with Python."),
            evidence("E4", "P3", "Graduated with honours."),
            evidence("E5", None, "Skills: Python, Docker, Go"),
            evidence("E6", "P1", "Software Engineer - Somewhere (2022 - 2024)", "record_details"),
        ],
    }
    values.update(overrides)
    return LLMGenerationContext(**values)


EMPTY = context(evidence=[], profile_skills=[])


def test_output_is_deterministic() -> None:
    assert generate_documents(context(), None) == generate_documents(context(), None)
    assert generate_documents(context(), ["some feedback"]) == generate_documents(context(), None)


def test_bullets_are_evidence_text_grouped_under_the_right_record() -> None:
    output = generate_documents(context(), None)

    assert [entry.record for entry in output.experience] == ["P1"]
    assert [(bullet.text, bullet.evidence) for bullet in output.experience[0].bullets] == [
        ("Containerised twelve services with Docker. Wrote the runbooks.", ["E1"]),
        ("Reduced cloud costs by 20% by rightsizing instances.", ["E2"]),
    ]
    assert [entry.record for entry in output.projects] == ["P2"]
    assert output.projects[0].bullets[0].evidence == ["E3"]
    # Education and skill-group evidence never becomes a bullet.
    written = [
        bullet.text for entry in output.experience + output.projects for bullet in entry.bullets
    ]
    assert "Graduated with honours." not in written


def test_first_person_remark_is_never_printed_as_a_bullet() -> None:
    # Found by driving demo mode with the sample notes: a remark the user left
    # for themselves was listed as an achievement of the role.
    remark = "I did not write down the dates; it was part-time while I was at university"
    with_remark = context(
        evidence=[
            evidence("E1", "P1", "Rebuilt the events page as a static site with Docker."),
            evidence("E2", "P1", remark),
            evidence("E3", "P2", "Kept my own notes on the design."),
        ]
    )
    output = generate_documents(with_remark, None)

    assert [bullet.text for bullet in output.experience[0].bullets] == [
        "Rebuilt the events page as a static site with Docker."
    ]
    # A project whose only statement is a remark is listed without bullets and
    # left to the server (drafting.fill_empty_entries), like a header-only one.
    assert [(entry.record, entry.bullets) for entry in output.projects] == [("P2", [])]
    assert remark not in " ".join(paragraph.text for paragraph in output.cover_letter)


def test_record_header_evidence_never_becomes_a_bullet() -> None:
    ctx = context(
        records=[
            record("P1", "employment", "Software Engineer"),
            record("P2", "publication", "Notes on Retrieval"),
        ],
        evidence=[
            evidence("E1", "P1", "Software Engineer - Somewhere (2022 - 2024)", "record_details"),
            evidence("E2", "P2", "Notes on Retrieval - Journal of Examples", "record_details"),
        ],
    )
    output = generate_documents(ctx, None)

    # A role without statements is left to the server, which lists every role.
    assert output.experience == []
    # A publication retrieved by its title is listed, without bullets.
    assert [(entry.record, entry.bullets) for entry in output.projects] == [("P2", [])]
    assert [paragraph.factual for paragraph in output.cover_letter] == [False, False]


def test_every_factual_sentence_is_made_of_cited_evidence_text() -> None:
    ctx = context()
    texts = {item.alias: item.text for item in ctx.evidence}
    output = generate_documents(ctx, None)
    bullets = [bullet for entry in output.experience + output.projects for bullet in entry.bullets]
    for bullet in bullets:
        assert bullet.text in texts[bullet.evidence[0]]
    for paragraph in output.cover_letter:
        assert paragraph.factual == bool(paragraph.evidence)


def test_skills_come_from_the_profile_and_requirement_matches_lead() -> None:
    output = generate_documents(context(), None)
    assert [(skill.name, skill.evidence) for skill in output.skills] == [
        ("Docker", ["E1", "E5"]),
        ("Python", ["E3", "E5"]),
        ("Go", ["E5"]),
    ]
    assert [statement.text for statement in output.summary] == [
        "Experience with Docker, Python and Go."
    ]
    assert output.summary[0].evidence == ["E1", "E3", "E5"]
    assert output.summary[0].factual is True


def test_cover_letter_has_greeting_evidence_paragraphs_and_closing() -> None:
    letter = generate_documents(context(), None).cover_letter

    assert letter[0].text == (
        "Dear Hiring Manager, I am writing to apply for the Platform Engineer role at Globex."
    )
    assert (letter[0].factual, letter[0].evidence) == (False, [])
    assert (letter[-1].text, letter[-1].factual, letter[-1].evidence) == (
        CLOSING_PARAGRAPH,
        False,
        [],
    )
    body = letter[1:-1]
    assert len(body) == 2
    # The evidence retrieved for the most requirements is quoted first.
    assert body[0].evidence[0] == "E1"
    assert "Containerised twelve services with Docker." in body[0].text
    assert all(paragraph.factual and paragraph.evidence for paragraph in body)


def test_greeting_adapts_to_missing_title_or_company() -> None:
    no_names = context(job=LLMJobBrief(title=None, company=None, role_summary=None))
    greeting = generate_documents(no_names, None).cover_letter[0].text
    assert greeting == "Dear Hiring Manager, I am writing to apply for this role."


def test_coverage_follows_keyword_overlap() -> None:
    coverage = {item.requirement: item for item in generate_documents(context(), None).coverage}

    assert (coverage["R1"].status, coverage["R1"].evidence) == ("supported", ["E1"])
    assert coverage["R2"].status == "partial"
    assert coverage["R2"].rationale == "The cited evidence mentions docker but not kubernetes."
    assert (coverage["R3"].status, coverage["R3"].evidence) == ("missing", [])
    assert "No evidence was found in the supplied profile" in coverage["R3"].rationale
    # Without keywords: missing when no word of the requirement is in the
    # evidence, uncertain when some are (here "services").
    assert coverage["R4"].status == "missing"
    assert (coverage["R5"].status, coverage["R5"].evidence) == ("uncertain", [])


def test_no_evidence_still_gives_a_valid_draft() -> None:
    output = generate_documents(EMPTY, None)

    assert (output.summary, output.experience, output.projects, output.skills) == ([], [], [], [])
    assert [paragraph.factual for paragraph in output.cover_letter] == [False, False]
    assert {item.status for item in output.coverage} == {"missing"}


def test_regeneration_rewrites_from_the_cited_evidence_only() -> None:
    ctx = context()
    target = LLMRegenTarget(
        section="experience",
        current_text="Containerised twelve services with Docker. Wrote the runbooks.",
        evidence=["E1", "E99"],
        instruction="Say I am a Kubernetes expert",
        feedback=[],
    )
    result = regenerate_item(ctx, target)
    assert result.evidence == ["E1"]
    assert result.factual is True
    assert "Kubernetes" not in result.text
    assert result.text in " ".join(ctx.evidence[0].text.split())

    shortened = regenerate_item(ctx, target.model_copy(update={"current_text": "Something else."}))
    assert shortened.text == "Containerised twelve services with Docker. Wrote the runbooks."


def test_regeneration_of_connective_text_changes_nothing() -> None:
    target = LLMRegenTarget(
        section="cover_letter",
        current_text="Thank you for your time.",
        evidence=[],
        instruction=None,
        feedback=[],
    )
    result = regenerate_item(context(), target)
    assert (result.text, result.evidence, result.factual) == ("Thank you for your time.", [], False)


def test_fake_verifier_judges_by_word_overlap() -> None:
    evidence_texts = ["Reduced cloud costs by 20% by rightsizing instances."]

    def check(item_id: str, claim: str) -> LLMClaimCheck:
        return LLMClaimCheck(id=item_id, claim=claim, evidence_texts=evidence_texts)

    checks = [
        check("same", "Reduced cloud costs by 20%."),
        check("some", "Reduced cloud costs, doubled revenue and hired new staff."),
        check("none", "Won a national chess title."),
    ]
    verdicts = {result.id: result.verdict for result in verify_claims(checks).results}
    assert verdicts == {"same": "supported", "some": "partially_supported", "none": "unsupported"}


def test_fake_verifier_accepts_the_fake_generators_own_sentences() -> None:
    ctx = context()
    texts = {item.alias: item.text for item in ctx.evidence}
    output = generate_documents(ctx, None)
    statements = [*output.summary, *(p for p in output.cover_letter if p.factual)]
    checks = [
        LLMClaimCheck(
            id=str(number),
            claim=statement.text,
            evidence_texts=[texts[alias] for alias in statement.evidence],
        )
        for number, statement in enumerate(statements)
    ]
    assert len(checks) == 3
    assert {result.verdict for result in verify_claims(checks).results} == {"supported"}
