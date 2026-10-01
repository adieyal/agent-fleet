from fleet.modules.records.domain import in_force


def test_continues_a_numbered_section_before_the_next_heading():
    body = "# Charter\n\n## Decisions in force (2026-10-01)\n\n1. One.\n2. Two.\n\n## Open questions\n\n- Q\n"
    assert in_force(body, "New.") == (
        "# Charter\n\n## Decisions in force (2026-10-01)\n\n1. One.\n2. Two.\n3. New.\n\n## Open questions\n\n- Q\n")


def test_bullets_a_section_without_numbers_and_keeps_subheadings_inside_it():
    body = "## Decisions in force\n\n- One.\n\n### Detail\n\nText.\n"
    assert in_force(body, "New.") == "## Decisions in force\n\n- One.\n\n### Detail\n\nText.\n- New.\n"


def test_fills_an_empty_section_and_adds_a_missing_one_at_the_end():
    assert in_force("## Decisions in force\n## Next\n", "New.") == "## Decisions in force\n\n- New.\n\n## Next\n"
    assert in_force("# Charter\n\nGoal.\n\n", "New.") == "# Charter\n\nGoal.\n\n## Decisions in force\n\n- New.\n"
