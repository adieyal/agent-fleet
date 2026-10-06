"""The decision language reads every line one way: compiled, guidance or off. Only a bad header fails."""
import pytest

from fleet.modules.canvas import (CodeInvalid, compile_code, compile_schedule, compile_view, defaults, describe,
                                  parse_page)

PARKED = '''zone "Parked"
  on enter:
    pause runs
    set budget 0
    notify you "Parked {item}"
  on exit:
    require replan if parked longer than 14 days
    resume runs
  agents:
    may propose entry
    may not move items out
    revisit weekly and say why each item is still parked'''


def kinds(compiled):
    return [(line.n, line.kind) for line in compiled.lines]


def test_the_parked_example_compiles_with_one_guidance_line():
    compiled = compile_code(PARKED)
    assert compiled.object == "zone" and compiled.name == "Parked"
    assert [line.op for line in compiled.ops("enter")] == ["pause", "budget", "notify"]
    assert [line.op for line in compiled.ops("exit")] == ["replan_after", "resume"]
    assert [line.op for line in compiled.ops("agents")] == ["may_propose", "no_exit"]
    guided = compiled.guidance()
    assert [line.text for line in guided] == ["revisit weekly and say why each item is still parked"]
    assert guided[0].marker == "~" and compiled.ops("enter")[0].marker == "✓"
    assert compiled.counts() == (7, 1)


def test_an_operation_outside_its_sections_is_guidance_that_says_why():
    compiled = compile_code("stage implement\n  exit when:\n    dispatch builder\n  agents:\n    revision submitted")
    builder, submitted = compiled.lines[2], compiled.lines[4]
    assert builder.kind == "guide" and builder.note == "only valid under on enter"
    assert submitted.kind == "guide" and submitted.note == "only valid under exit when"
    assert compiled.ops("enter") == [] and compiled.ops("when") == []


def test_operations_belong_to_their_objects():
    compiled = compile_code('stage plan\n  capacity:\n    limit 2 items\n  on enter:\n    add to context')
    assert compiled.lines[1].kind == "guide" and "no capacity section" in compiled.lines[1].note
    assert compiled.lines[4].kind == "guide" and "not valid in a stage" in compiled.lines[4].note


def test_a_line_outside_any_section_is_meaning():
    compiled = compile_code("stage review\n  check the diff carefully")
    assert compiled.lines[1].section == "meaning" and compiled.lines[1].kind == "guide"


def test_levels_turn_operations_into_guidance_or_off():
    guided = compile_code(PARKED, level="guidance")
    assert guided.ops("enter") == []
    assert len(guided.guidance()) == 8
    assert {line.marker for line in guided.lines if line.op} == {"~"}
    label = compile_code(PARKED, level="label")
    assert label.ops("enter") == [] and label.guidance() == []
    assert {line.marker for line in label.lines if line.section and line.kind != "section"} == {"·"}


def test_headers_are_required():
    with pytest.raises(CodeInvalid):
        compile_code("on enter:\n  pause runs")
    with pytest.raises(CodeInvalid):
        compile_code(PARKED, level="loud")


def test_quoted_arguments_and_numbers_are_captured():
    compiled = compile_code('stage test\n  exit when:\n    evidence "tests pass" on current revision\n  on fail:\n'
                            '    send back to implement')
    assert compiled.ops("when")[0].args == ("tests pass",)
    assert compiled.ops("fail")[0].args == ("implement",)
    assert describe(compiled.ops("when")[0]) == "evidence that tests pass on the current revision"


def test_the_epic_workflow_has_stages_inside_one_snippet():
    compiled = compile_code(defaults.EPIC_WORKFLOW)
    assert compiled.object == "epic" and compiled.stages == ("shape", "deliver", "accept")
    assert [line.op for line in compiled.ops("when", stage="deliver")] == ["children_done", "covered"]
    assert [line.op for line in compiled.ops("enter", stage="accept")] == ["ask"]


def test_the_schedule_reads_capacity_bands_order_and_constraints():
    compiled = compile_schedule(defaults.SCHEDULE + "\n    prefer claude for refactors")
    policy = compiled.options
    assert policy["capacity"] == {"claude": 2, "codex": 1}
    assert policy["bands"] == {"now": 2}
    assert policy["dependencies"] and policy["independent_testers"]
    assert policy["order"] == ["bands", "oldest"]
    assert compiled.lines[-1].kind == "guide"
    without = compile_schedule(defaults.SCHEDULE.replace("    dependencies\n", ""))
    assert not without.options["dependencies"]


def test_views_keep_unknown_options_as_guidance():
    compiled = compile_view('view swimlanes "Tasks by owner"\n  rows: owner\n  columns: colour\n  sort: newest first')
    assert compiled.title == "Tasks by owner" and compiled.options["rows"] == "owner"
    assert compiled.options["columns"] == "stage"
    notes = [line.note for line in compiled.lines if line.kind == "guide"]
    assert notes == ["columns takes stage, status", None]
    with pytest.raises(CodeInvalid):
        compile_view('view chart "Nope"')


def test_region_names_are_interpreted_into_code_you_adopt():
    assert compile_code(defaults.zone_code("Parked")).has("enter", "pause")
    assert compile_code(defaults.zone_code("Now")).has("capacity", "limit")
    assert compile_code(defaults.zone_code("Context for agents")).has("enter", "context_add")
    unknown = compile_code(defaults.zone_code("Ideas"))
    assert unknown.ops("enter") == [] and len(unknown.guidance()) == 1


def test_reading_pages_keep_unknown_directives_as_guidance():
    blocks = parse_page("# Title\nProse here\n::needs-you\n::burndown\n")
    assert [block["kind"] for block in blocks] == ["title", "text", "directive", "guide"]
    assert blocks[3]["directive"] == "burndown"
