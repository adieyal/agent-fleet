"""Work command reference resolution and evidence specifications."""
from __future__ import annotations

from fleet.identifiers import resolve_prefix
from fleet.modules.work import EvidenceSpecification
from fleet.transport import FleetError


class WorkCommands:
    def __init__(self, work, workspace, references):
        self.work, self.workspace, self.references = work, workspace, references

    def execute(self, command: str, fields: dict):
        work = self.work
        fields = fields.copy()
        identity = fields.pop("id", None)
        if identity is not None:
            if command == "meet":
                identity = resolve_prefix(identity, [criterion.id for criteria in work.criteria_by_item().values()
                                                   for criterion in criteria], "criterion")
            else:
                identity = self.references.work(identity)
        for name in ("parent", "to_item"):
            if fields.get(name) is not None:
                fields[name] = self.references.work(fields[name])
        try:
            if command == "criterion_add":
                reference = fields.pop("evidence_reference")
                result = fields.pop("required_result")
                if result is not None and reference is None:
                    raise ValueError("required result needs an evidence reference")
                fields["specification"] = EvidenceSpecification(reference, result) if reference is not None else None
                item = work.add_criterion(identity, **fields)
            elif command == "meet":
                fields["evidence"] = tuple(fields["evidence"])
                item = work.meet(identity, **fields)
            elif command == "relate":
                item = work.relate(identity, fields.pop("to_item"), **fields)
            elif command == "add":
                fields['project'] = self.workspace().resolve_project(fields['project'])
                known = work.kinds(fields['project'])
                item = work.add(**fields)
            else:
                known = work.kinds(work.get(identity).project) if "kind" in fields else None
                item = getattr(work, command)(identity, **fields)
            return item, known if command in ("add", "set") else None
        except (ValueError, LookupError, OSError) as error:
            raise FleetError(str(error)) from error
