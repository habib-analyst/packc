"""packc: the vertical skill-pack authoring language.

Declare skills once with typed schemas, flows, and escalation policies;
compile to SKILL.md + a working MCP server + conformance tests.
"""
__version__ = "0.1.0"

from .typecheck import check_file, check_text  # noqa: F401
