# src/filigree/templates.py
"""Workflow template system -- loading, caching, and validation.

Provides TemplateRegistry for managing per-type state machines, transition
enforcement, and field validation. Type templates define states, transitions,
and field schemas. Workflow packs bundle related types.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from dataclasses import replace as _dc_replace
from pathlib import Path
from typing import Any, Literal

# ---------------------------------------------------------------------------
# Logging (review B4)
# ---------------------------------------------------------------------------

logger = logging.getLogger(__name__)

# State/type names must match this pattern to be safe for use in SQL queries
# and filesystem paths. Validated at parse time (review B1, B5).
_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_VALID_CATEGORIES: frozenset[str] = frozenset({"open", "wip", "done"})

# ---------------------------------------------------------------------------
# Type aliases (WFT-NFR-015)
# ---------------------------------------------------------------------------

StateCategory = Literal["open", "wip", "done"]
EnforcementLevel = Literal["hard", "soft"]
FieldType = Literal["text", "enum", "number", "date", "list", "boolean"]

# ---------------------------------------------------------------------------
# Frozen dataclasses (WFT-NFR-014)
# ---------------------------------------------------------------------------
# Templates use frozen=True for immutability (configuration data).
# This differs intentionally from the mutable Issue dataclass (domain entity
# in core.py). Frozen dataclasses prevent accidental mutation after loading
# and enable safe caching in dict lookups.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StateDefinition:
    """A named state within a type's workflow, mapped to a universal category."""

    name: str
    category: StateCategory

    def __post_init__(self) -> None:
        if not _NAME_PATTERN.match(self.name):
            msg = f"Invalid state name '{self.name}': must match ^[a-z][a-z0-9_]{{0,63}}$"
            raise ValueError(msg)
        if self.category not in _VALID_CATEGORIES:
            allowed = sorted(_VALID_CATEGORIES)
            msg = f"Invalid category '{self.category}' for state '{self.name}': must be one of {allowed}"
            raise ValueError(msg)


@dataclass(frozen=True)
class TransitionDefinition:
    """A valid state transition with enforcement level and field requirements."""

    from_state: str
    to_state: str
    enforcement: EnforcementLevel
    requires_fields: tuple[str, ...] = ()


_VALID_FIELD_TYPES: frozenset[str] = frozenset({"text", "enum", "number", "date", "list", "boolean"})


@dataclass(frozen=True)
class FieldSchema:
    """Schema for a custom field on an issue type."""

    name: str
    type: FieldType
    description: str = ""
    options: tuple[str, ...] = ()
    default: Any = None
    required_at: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.type not in _VALID_FIELD_TYPES:
            allowed = sorted(_VALID_FIELD_TYPES)
            msg = f"Invalid field type '{self.type}' for field '{self.name}': must be one of {allowed}"
            raise ValueError(msg)


@dataclass(frozen=True)
class TypeTemplate:
    """Complete workflow definition for an issue type."""

    type: str
    display_name: str
    description: str
    pack: str
    states: tuple[StateDefinition, ...]
    initial_state: str
    transitions: tuple[TransitionDefinition, ...]
    fields_schema: tuple[FieldSchema, ...]
    suggested_children: tuple[str, ...] = ()
    suggested_labels: tuple[str, ...] = ()


@dataclass(frozen=True)
class WorkflowPack:
    """A bundle of related type templates with relationships and guidance."""

    pack: str
    version: str
    display_name: str
    description: str
    types: dict[str, TypeTemplate]
    requires_packs: tuple[str, ...]
    relationships: tuple[dict[str, Any], ...]
    cross_pack_relationships: tuple[dict[str, Any], ...]
    guide: dict[str, Any] | None


@dataclass(frozen=True)
class TransitionResult:
    """Result of validating a specific state transition."""

    allowed: bool
    enforcement: EnforcementLevel | None
    missing_fields: tuple[str, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class TransitionOption:
    """A possible next state from the current state."""

    to: str
    category: StateCategory
    enforcement: EnforcementLevel | None
    requires_fields: tuple[str, ...]
    missing_fields: tuple[str, ...]
    ready: bool


@dataclass(frozen=True)
class ValidationResult:
    """Result of validating an issue against its template."""

    valid: bool
    warnings: tuple[str, ...]
    errors: tuple[str, ...]


# ---------------------------------------------------------------------------
# Custom exceptions (WFT-AR-010)
# ---------------------------------------------------------------------------


class TransitionNotAllowedError(ValueError):
    """Raised when a transition is not defined in the type's transition table."""

    def __init__(self, from_state: str, to_state: str, type_name: str) -> None:
        self.from_state = from_state
        self.to_state = to_state
        self.type_name = type_name
        super().__init__(
            f"Transition '{from_state}' -> '{to_state}' is not defined for type '{type_name}'. "
            f"Use get_valid_transitions() to see available transitions."
        )


class HardEnforcementError(ValueError):
    """Raised when a hard-enforced transition fails field validation."""

    def __init__(self, from_state: str, to_state: str, type_name: str, missing_fields: list[str]) -> None:
        self.from_state = from_state
        self.to_state = to_state
        self.type_name = type_name
        self.missing_fields = missing_fields
        fields_str = ", ".join(missing_fields)
        super().__init__(
            f"Cannot transition '{from_state}' -> '{to_state}' for type '{type_name}': "
            f"missing required fields: {fields_str}. "
            f"Populate these fields before transitioning, or call get_type_info('{type_name}') for field details."
        )


# ---------------------------------------------------------------------------
# TemplateRegistry (WFT-FR-001)
# ---------------------------------------------------------------------------


class TemplateRegistry:
    """Loads, caches, and queries workflow templates and packs.

    Templates are loaded once per instance and cached for the entire lifetime (WFT-NFR-005).
    The registry builds O(1) lookup caches for category mapping (WFT-SR-002) and
    transition validation (WFT-SR-003).
    """

    # Size limits for custom templates (review B5 -- prevent DoS via huge templates)
    MAX_STATES = 50
    MAX_TRANSITIONS = 200
    MAX_FIELDS = 50

    def __init__(self) -> None:
        self._types: dict[str, TypeTemplate] = {}
        self._packs: dict[str, WorkflowPack] = {}
        self._category_cache: dict[tuple[str, str], StateCategory] = {}
        self._transition_cache: dict[str, dict[tuple[str, str], TransitionDefinition]] = {}
        self._loaded = False

    # -- Parsing (from dict/JSON) -------------------------------------------

    @staticmethod
    def parse_type_template(raw: dict[str, Any]) -> TypeTemplate:
        """Parse a type template from a JSON-compatible dict.

        Args:
            raw: Dictionary with type template fields as defined in design Section 3.2.

        Returns:
            A frozen TypeTemplate instance.

        Raises:
            ValueError: If required fields are missing, invalid, or exceed size limits.
            KeyError: If required keys are missing from the dict.
        """
        # Validate type name format (review B1, B5)
        type_name = raw["type"]
        if not _NAME_PATTERN.match(type_name):
            msg = f"Invalid type name '{type_name}': must match ^[a-z][a-z0-9_]{{0,63}}$"
            raise ValueError(msg)

        # Defensive shape checks (must come before size limits to avoid TypeError on None)
        raw_states = raw.get("states")
        if not isinstance(raw_states, list):
            msg = f"Type '{type_name}': 'states' must be a list, got {type(raw_states).__name__}"
            raise ValueError(msg)
        for i, s in enumerate(raw_states):
            if not isinstance(s, dict) or "name" not in s or "category" not in s:
                msg = f"Type '{type_name}': state at index {i} must be a dict with 'name' and 'category'"
                raise ValueError(msg)

        # Defensive shape checks for transitions and fields_schema
        raw_transitions = raw.get("transitions", [])
        if raw_transitions is not None and not isinstance(raw_transitions, list):
            msg = f"Type '{type_name}': 'transitions' must be a list, got {type(raw_transitions).__name__}"
            raise ValueError(msg)
        if raw_transitions is None:
            raw_transitions = []
        for i, t in enumerate(raw_transitions):
            if not isinstance(t, dict):
                msg = f"Type '{type_name}': transition at index {i} must be a dict, got {type(t).__name__}"
                raise ValueError(msg)

        raw_fields = raw.get("fields_schema", [])
        if raw_fields is not None and not isinstance(raw_fields, list):
            msg = f"Type '{type_name}': 'fields_schema' must be a list, got {type(raw_fields).__name__}"
            raise ValueError(msg)
        if raw_fields is None:
            raw_fields = []
        for i, f in enumerate(raw_fields):
            if not isinstance(f, dict):
                msg = f"Type '{type_name}': field at index {i} must be a dict, got {type(f).__name__}"
                raise ValueError(msg)

        # Enforcement validation
        valid_enforcement = {"hard", "soft", "none"}
        for t in raw_transitions:
            enforcement_val = t.get("enforcement")
            if enforcement_val not in valid_enforcement:
                allowed = ", ".join(sorted(valid_enforcement))
                msg = (
                    f"Type '{type_name}': transition "
                    f"{t.get('from')}->{t.get('to')} "
                    f"has invalid enforcement '{enforcement_val}' "
                    f"(must be one of: {allowed})"
                )
                raise ValueError(msg)

        # Size limit checks (review B5 -- prevent DoS via huge templates)
        if len(raw_states) > TemplateRegistry.MAX_STATES:
            msg = f"Type '{type_name}' has {len(raw_states)} states (max {TemplateRegistry.MAX_STATES})"
            raise ValueError(msg)
        if len(raw_transitions) > TemplateRegistry.MAX_TRANSITIONS:
            msg = f"Type '{type_name}' has {len(raw_transitions)} transitions (max {TemplateRegistry.MAX_TRANSITIONS})"
            raise ValueError(msg)
        if len(raw_fields) > TemplateRegistry.MAX_FIELDS:
            msg = f"Type '{type_name}' has {len(raw_fields)} fields (max {TemplateRegistry.MAX_FIELDS})"
            raise ValueError(msg)

        logger.debug("Parsing template for type: %s", type_name)

        # StateDefinition.__post_init__ validates each state name format + category
        states = tuple(StateDefinition(name=s["name"], category=s["category"]) for s in raw_states)

        # Detect duplicate state names (filigree-eff214)
        seen_names: set[str] = set()
        for s in states:
            if s.name in seen_names:
                msg = f"Type '{type_name}': duplicate state name '{s.name}'"
                raise ValueError(msg)
            seen_names.add(s.name)

        transitions = tuple(
            TransitionDefinition(
                from_state=t["from"],
                to_state=t["to"],
                enforcement=t["enforcement"],
                requires_fields=tuple(t.get("requires_fields", [])),
            )
            for t in raw_transitions
        )
        fields_schema = tuple(
            FieldSchema(
                name=f["name"],
                type=f["type"],
                description=f.get("description", ""),
                options=tuple(f.get("options", [])),
                default=f.get("default"),
                required_at=tuple(f.get("required_at", [])),
            )
            for f in raw_fields
        )
        return TypeTemplate(
            type=type_name,
            display_name=raw["display_name"],
            description=raw.get("description", ""),
            pack=raw.get("pack", "custom"),
            states=states,
            initial_state=raw["initial_state"],
            transitions=transitions,
            fields_schema=fields_schema,
            suggested_children=tuple(raw.get("suggested_children", [])),
            suggested_labels=tuple(raw.get("suggested_labels", [])),
        )

    @staticmethod
    def validate_type_template(tpl: TypeTemplate) -> list[str]:
        """Validate a TypeTemplate for internal consistency (WFT-NFR-008).

        Returns:
            List of error messages. Empty list means valid.
        """
        errors: list[str] = []
        state_names = {s.name for s in tpl.states}

        # Detect duplicate state names (filigree-eff214)
        if len(state_names) != len(tpl.states):
            seen: set[str] = set()
            for st in tpl.states:
                if st.name in seen:
                    errors.append(f"duplicate state name '{st.name}'")
                seen.add(st.name)

        if tpl.initial_state not in state_names:
            errors.append(f"initial_state '{tpl.initial_state}' is not in states list")

        for t in tpl.transitions:
            if t.from_state not in state_names:
                errors.append(f"transition from_state '{t.from_state}' is not in states list")
            if t.to_state not in state_names:
                errors.append(f"transition to_state '{t.to_state}' is not in states list")

        field_names = {f.name for f in tpl.fields_schema}
        for t in tpl.transitions:
            for rf in t.requires_fields:
                if rf not in field_names:
                    errors.append(
                        f"transition {t.from_state}->{t.to_state} requires_fields '{rf}' not in fields_schema"
                    )

        for f in tpl.fields_schema:
            for ra in f.required_at:
                if ra not in state_names:
                    errors.append(f"field '{f.name}' required_at '{ra}' is not in states list")

        # Reachability: all states should be reachable from initial_state
        if tpl.initial_state in state_names:
            reachable: set[str] = set()
            queue = [tpl.initial_state]
            while queue:
                current = queue.pop(0)
                if current in reachable:
                    continue
                reachable.add(current)
                for t in tpl.transitions:
                    if t.from_state == current and t.to_state not in reachable:
                        queue.append(t.to_state)
            unreachable = state_names - reachable
            for s in sorted(unreachable):
                errors.append(f"state '{s}' is unreachable from initial_state '{tpl.initial_state}'")

        return errors

    @staticmethod
    def check_type_template_quality(tpl: TypeTemplate) -> list[str]:
        """Check a TypeTemplate for quality issues (non-blocking warnings).

        Returns:
            List of warning messages. These don't prevent registration.
        """
        warnings: list[str] = []
        state_names = {s.name for s in tpl.states}
        done_states = {s.name for s in tpl.states if s.category == "done"}
        from_states = {t.from_state for t in tpl.transitions}

        # Dead-end detection: non-done states should have at least one outgoing transition
        for s in sorted(state_names - done_states):
            if s not in from_states:
                cat = next(st.category for st in tpl.states if st.name == s)
                warnings.append(f"state '{s}' (category={cat}) has no outgoing transitions (dead end)")

        # Done-states with outgoing transitions: close_issue() treats these as
        # "already closed", so the outgoing transitions are only reachable via
        # update_issue(). Flag for design review.
        for s in sorted(done_states & from_states):
            targets = [t.to_state for t in tpl.transitions if t.from_state == s]
            warnings.append(
                f"done-category state '{s}' has outgoing transitions to {targets} — "
                f"close_issue() will reject issues in this state as 'already closed'"
            )

        return warnings

    # -- Registration (internal) --------------------------------------------

    def _register_type(self, tpl: TypeTemplate) -> None:
        """Register a type template and build caches."""
        logger.debug("Registering type: %s (pack=%s, %d states)", tpl.type, tpl.pack, len(tpl.states))
        self._types[tpl.type] = tpl

        # Build category cache -- O(1) lookup (WFT-SR-002)
        # Clear stale entries first (type may be overridden with different states)
        stale = [k for k in self._category_cache if k[0] == tpl.type]
        for k in stale:
            del self._category_cache[k]
        for state in tpl.states:
            self._category_cache[(tpl.type, state.name)] = state.category

        # Build transition cache -- O(1) lookup (WFT-SR-003)
        self._transition_cache[tpl.type] = {(t.from_state, t.to_state): t for t in tpl.transitions}

    def _register_pack(self, pack: WorkflowPack) -> None:
        """Register a workflow pack."""
        logger.debug("Registering pack: %s (version=%s, %d types)", pack.pack, pack.version, len(pack.types))
        self._packs[pack.pack] = pack

    # -- Queries ------------------------------------------------------------

    def get_type(self, type_name: str) -> TypeTemplate | None:
        """Get a type template by name."""
        return self._types.get(type_name)

    def get_pack(self, pack_name: str) -> WorkflowPack | None:
        """Get a pack by name."""
        return self._packs.get(pack_name)

    def list_types(self) -> list[TypeTemplate]:
        """All types from enabled packs."""
        return list(self._types.values())

    def list_packs(self) -> list[WorkflowPack]:
        """All enabled packs."""
        return list(self._packs.values())

    def get_initial_state(self, type_name: str) -> str:
        """Initial state for a type. Falls back to 'open' if no template (WFT-FR-007)."""
        tpl = self._types.get(type_name)
        if tpl is None:
            logger.warning("Unknown type '%s' -- falling back to initial state 'open'", type_name)
            return "open"
        return tpl.initial_state

    def get_category(self, type_name: str, state: str) -> StateCategory | None:
        """Map a (type, state) pair to its category via O(1) cache (WFT-SR-002)."""
        return self._category_cache.get((type_name, state))

    def get_valid_states(self, type_name: str) -> list[str] | None:
        """Return list of valid state names for a type, or None if type unknown."""
        tpl = self._types.get(type_name)
        if tpl is None:
            return None
        return [s.name for s in tpl.states]

    def get_first_state_of_category(self, type_name: str, category: StateCategory) -> str | None:
        """Return the first state of a given category (array order) (WFT-FR-010)."""
        tpl = self._types.get(type_name)
        if tpl is None:
            return None
        for state in tpl.states:
            if state.category == category:
                return state.name
        return None

    # -- Validation ---------------------------------------------------------

    @staticmethod
    def _is_field_populated(value: Any) -> bool:
        """Check if a field value is considered populated (WFT-FR-012).

        None, empty strings, and whitespace-only strings are unpopulated.
        """
        if value is None:
            return False
        return not (isinstance(value, str) and value.strip() == "")

    def validate_transition(
        self,
        type_name: str,
        from_state: str,
        to_state: str,
        fields: dict[str, Any],
    ) -> TransitionResult:
        """Validate a state transition (WFT-FR-011).

        Args:
            type_name: The issue type.
            from_state: Current state.
            to_state: Target state.
            fields: Current issue fields dict.

        Returns:
            TransitionResult indicating whether the transition is allowed,
            its enforcement level, any missing required fields, and warnings.
        """
        tpl = self._types.get(type_name)
        if tpl is None:
            # Fallback: unknown types allow all transitions (WFT-FR-016)
            return TransitionResult(allowed=True, enforcement=None, missing_fields=(), warnings=())

        transition_map = self._transition_cache.get(type_name, {})
        transition = transition_map.get((from_state, to_state))

        if transition is None:
            # Transition not in table: REJECTED for known types (WFT-FR-011)
            return TransitionResult(
                allowed=False,
                enforcement=None,
                missing_fields=(),
                warnings=(
                    f"Transition '{from_state}' -> '{to_state}' is not in the standard workflow for '{type_name}'. "
                    f"Use get_valid_transitions() to see recommended transitions.",
                ),
            )

        # Check required fields for this transition
        missing = tuple(f for f in transition.requires_fields if not self._is_field_populated(fields.get(f)))

        # Also check fields required_at the target state
        state_required = self.validate_fields_for_state(type_name, to_state, fields)
        all_missing = tuple(dict.fromkeys(list(missing) + state_required))  # dedupe, preserve order

        warnings: list[str] = []

        if transition.enforcement == "hard" and all_missing:
            return TransitionResult(
                allowed=False,
                enforcement="hard",
                missing_fields=all_missing,
                warnings=(),
            )

        if transition.enforcement == "soft" and all_missing:
            warnings.append(f"Missing recommended fields for '{to_state}': {', '.join(all_missing)}")

        return TransitionResult(
            allowed=True,
            enforcement=transition.enforcement,
            missing_fields=all_missing,
            warnings=tuple(warnings),
        )

    def get_valid_transitions(
        self,
        type_name: str,
        current_state: str,
        fields: dict[str, Any],
    ) -> list[TransitionOption]:
        """All valid transitions from current state with readiness info (WFT-FR-014).

        Args:
            type_name: The issue type.
            current_state: Current state.
            fields: Current issue fields dict.

        Returns:
            List of TransitionOption with readiness and field requirement info.
        """
        tpl = self._types.get(type_name)
        if tpl is None:
            return []

        options: list[TransitionOption] = []
        for t in tpl.transitions:
            if t.from_state != current_state:
                continue

            # Check missing fields for this transition
            missing_trans = [f for f in t.requires_fields if not self._is_field_populated(fields.get(f))]
            missing_state = self.validate_fields_for_state(type_name, t.to_state, fields)
            all_missing = list(dict.fromkeys(missing_trans + missing_state))

            target_category = self._category_cache.get((type_name, t.to_state), "open")
            # ready = True when all required fields are populated
            ready = len(all_missing) == 0

            options.append(
                TransitionOption(
                    to=t.to_state,
                    category=target_category,
                    enforcement=t.enforcement,
                    requires_fields=t.requires_fields,
                    missing_fields=tuple(all_missing),
                    ready=ready,
                )
            )

        return options

    def validate_fields_for_state(
        self,
        type_name: str,
        state: str,
        fields: dict[str, Any],
    ) -> list[str]:
        """Return fields required at this state but not yet populated (WFT-FR-012).

        Args:
            type_name: The issue type.
            state: The state to check requirements for.
            fields: Current issue fields dict.

        Returns:
            List of field names that are required but missing.
        """
        tpl = self._types.get(type_name)
        if tpl is None:
            return []

        missing: list[str] = []
        for f in tpl.fields_schema:
            if state in f.required_at and not self._is_field_populated(fields.get(f.name)):
                missing.append(f.name)
        return missing

    # -- Loading ------------------------------------------------------------

    def load(self, filigree_dir: Path, *, enabled_packs: list[str] | None = None) -> None:
        """Load templates from all three layers (WFT-FR-002 through WFT-FR-005).

        Layer 1: Built-in packs from templates_data.BUILT_IN_PACKS
        Layer 2: Installed packs from .filigree/packs/*.json
        Layer 3: Project-local overrides from .filigree/templates/*.json

        Idempotent: second call is a no-op.

        Args:
            filigree_dir: Path to the .filigree/ directory.
            enabled_packs: Optional override for enabled packs. If None, reads from config.
        """
        if self._loaded:
            return

        import json as _json

        from filigree.templates_data import BUILT_IN_PACKS

        _default_packs = ["core", "planning"]
        if enabled_packs is None:
            # Read enabled packs from config
            config_path = filigree_dir / "config.json"
            enabled_packs = _default_packs
            if config_path.exists():
                try:
                    config = _json.loads(config_path.read_text())
                    if not isinstance(config, dict):
                        raise ValueError("config.json must contain a JSON object")
                    enabled_packs = config.get("enabled_packs", _default_packs)
                except (ValueError, KeyError):
                    logger.warning("Could not read config.json — using default enabled_packs")

        # Validate enabled_packs is list[str] — strings would be split into chars
        if isinstance(enabled_packs, str):
            logger.warning("enabled_packs is a string ('%s'), wrapping in list", enabled_packs)
            enabled_packs = [enabled_packs]
        elif not isinstance(enabled_packs, list):
            logger.warning("enabled_packs has invalid type %s — using defaults", type(enabled_packs).__name__)
            enabled_packs = _default_packs
        else:
            enabled_packs = [p for p in enabled_packs if isinstance(p, str)]

        logger.info("Loading templates: enabled_packs=%s", enabled_packs)

        # Layer 1: Built-in packs
        for pack_name, pack_data in BUILT_IN_PACKS.items():
            if pack_name not in enabled_packs:
                logger.debug("Skipping disabled built-in pack: %s", pack_name)
                continue
            self._load_pack_data(pack_data)

        # Layer 2: Installed packs from .filigree/packs/*.json
        packs_dir = filigree_dir / "packs"
        if packs_dir.is_dir():
            for pack_file in sorted(packs_dir.glob("*.json")):
                try:
                    pack_data = _json.loads(pack_file.read_text())
                    pack_name = pack_data.get("pack", pack_file.stem)
                    if pack_name not in enabled_packs:
                        logger.debug("Skipping disabled installed pack: %s", pack_name)
                        continue
                    self._load_pack_data(pack_data)
                    logger.info("Loaded installed pack: %s from %s", pack_name, pack_file.name)
                except (ValueError, KeyError, TypeError, AttributeError) as exc:
                    logger.warning("Skipping invalid pack file %s: %s", pack_file.name, exc)

        # Layer 3: Project-local overrides from .filigree/templates/*.json
        templates_dir = filigree_dir / "templates"
        if templates_dir.is_dir():
            for tpl_file in sorted(templates_dir.glob("*.json")):
                try:
                    raw = _json.loads(tpl_file.read_text())
                    tpl = self.parse_type_template(raw)
                    errors = self.validate_type_template(tpl)
                    if errors:
                        logger.warning("Skipping invalid template %s: %s", tpl_file.name, errors)
                        continue
                    quality_warnings = self.check_type_template_quality(tpl)
                    for qw in quality_warnings:
                        logger.warning("Quality: %s (local override): %s", tpl.type, qw)
                    self._register_type(tpl)  # Overwrites built-in with same name
                    logger.info("Loaded project-local template override: %s", tpl.type)
                except (ValueError, KeyError, TypeError, AttributeError) as exc:
                    logger.warning("Skipping invalid template file %s: %s", tpl_file.name, exc)

        self._loaded = True
        logger.info("Template loading complete: %d types from %d packs", len(self._types), len(self._packs))

    def _load_pack_data(self, pack_data: dict[str, Any]) -> None:
        """Load a pack dict: register its types and the pack itself."""
        pack_name = pack_data["pack"]

        # Parse and register each type in the pack
        types_dict: dict[str, TypeTemplate] = {}
        for type_name, type_data in pack_data.get("types", {}).items():
            try:
                tpl = self.parse_type_template(type_data)
                # Ensure the type is tagged with the actual pack name,
                # not the default "custom" from missing pack field in type data
                if tpl.pack != pack_name:
                    tpl = _dc_replace(tpl, pack=pack_name)
                errors = self.validate_type_template(tpl)
                if errors:
                    logger.warning("Skipping invalid type %s in pack %s: %s", type_name, pack_name, errors)
                    continue
                quality_warnings = self.check_type_template_quality(tpl)
                for qw in quality_warnings:
                    logger.warning("Quality: %s/%s: %s", pack_name, type_name, qw)
                self._register_type(tpl)
                types_dict[type_name] = tpl
            except (ValueError, KeyError, TypeError, AttributeError) as exc:
                logger.warning("Skipping unparseable type %s in pack %s: %s", type_name, pack_name, exc)

        # Register the pack itself
        pack = WorkflowPack(
            pack=pack_name,
            version=pack_data.get("version", "1.0"),
            display_name=pack_data.get("display_name", pack_name),
            description=pack_data.get("description", ""),
            types=types_dict,
            requires_packs=tuple(pack_data.get("requires_packs", [])),
            relationships=tuple(pack_data.get("relationships", [])),
            cross_pack_relationships=tuple(pack_data.get("cross_pack_relationships", [])),
            guide=pack_data.get("guide"),
        )
        self._register_pack(pack)
        logger.debug("Registered pack: %s (%d types)", pack_name, len(types_dict))
