"""packc AST: dataclass nodes for the five block types + types + conditions."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Loc:
    line: int
    col: int

    def __str__(self) -> str:  # "12:5"
        return f"{self.line}:{self.col}"


# ---------------------------------------------------------------- types ---

@dataclass(frozen=True)
class PrimType:
    name: str  # string | int | float | bool
    loc: Loc | None = None


@dataclass(frozen=True)
class EnumType:
    members: tuple[str, ...]
    loc: Loc | None = None


@dataclass(frozen=True)
class ListType:
    elem: object  # Type
    loc: Loc | None = None


@dataclass(frozen=True)
class OptionalType:
    inner: object  # Type
    loc: Loc | None = None


@dataclass(frozen=True)
class RefType:
    name: str  # named schema reference
    loc: Loc | None = None


# --------------------------------------------------------------- blocks ---

@dataclass
class PackBlock:
    name: str
    version: str
    description: str
    author: str
    tools: list[str]
    loc: Loc
    present: dict = field(default_factory=dict)  # which fields were given


@dataclass
class SchemaField:
    name: str
    type: object  # Type
    default: object  # literal value or _MISSING
    has_default: bool
    loc: Loc


@dataclass
class SchemaBlock:
    name: str
    fields: list[SchemaField]
    loc: Loc


@dataclass
class SkillBlock:
    name: str
    description: str | None
    triggers: list[str] | None
    input: str | None      # schema ref name
    output: str | None     # schema ref name (optional)
    tool: str | None
    steps: list[str] | None
    loc: Loc


@dataclass
class FlowEdge:
    event: str
    target: str
    loc: Loc


@dataclass
class FlowBlock:
    name: str
    start: str
    edges: list[FlowEdge]
    loc: Loc


@dataclass
class Condition:
    field: str
    op: str  # > >= < <= == !=
    value: object  # literal
    loc: Loc


@dataclass
class Rule:
    name: str
    condition: Condition | None  # None = unconditional fallback
    tier: str
    loc: Loc


@dataclass
class EscalateBlock:
    context: str | None       # schema whose fields rules may reference
    defaults: list[tuple[str, str]]  # (action prefix, tier)
    rules: list[Rule]
    loc: Loc


@dataclass
class PackFile:
    pack: PackBlock | None
    schemas: list[SchemaBlock]
    skills: list[SkillBlock]
    flows: list[FlowBlock]
    escalate: EscalateBlock | None
    filename: str = "<string>"
