"""Pricing model registry and domain checks.

The registry separates the model name selected by a user from the runtime
implementation that currently prices/Greek-calculates it. New engines should
enter here first so CLI, adapters, exports, and math paths share one contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite


@dataclass(frozen=True)
class PricingModelSpec:
    name: str
    aliases: tuple[str, ...]
    family: str
    exercise_style: str
    price_dynamics: str
    supports_negative_underlying: bool
    requires_shift: bool
    supports_closed_form_price: bool
    supports_closed_form_greeks: bool
    default_greek_method: str
    parity_check_mode: str
    speed_tier: str
    maturity: str
    approximation: str = "none"
    runtime_model: str | None = None
    uses_dividend_yield: bool = False
    implemented_price: bool = False
    implemented_greeks: bool = False


@dataclass(frozen=True)
class PricingDomainResult:
    valid: bool
    reason: str | None = None


@dataclass(frozen=True)
class PricingModelResolution:
    selected_model: str
    pricing_model_target: str
    pricing_model_source: str
    pricing_model_runtime_status: str
    pricing_model_contract_match: bool
    pricing_model_contract_reason: str
    contract_exercise_style: str | None
    selected_model_exercise_style: str | None
    is_model_approximation: bool


DEFAULT_PRICING_MODEL_POLICY = {
    "futures_options": {
        "european": {
            "default": "black76_european",
        },
        "american": {
            "default": "black76_baw",
            "temporary_fallback": "black76_european",
            "fallback_label": "european_approximation_for_american_contract",
        },
    },
    "equity_options": {
        "european": {
            "default": "bsm",
        },
        "american": {
            "default": "bsm_baw",
            "temporary_fallback": "bsm",
            "fallback_label": "european_approximation_for_american_contract",
        },
    },
}


_MODEL_ORDER = (
    "black76",
    "black76_european",
    "bs",
    "bsm",
    "bachelier",
    "black76_shifted",
    "black76_baw",
    "black76_shifted_baw",
    "bsm_baw",
    "crr_binomial",
    "trinomial",
    "finite_difference",
)


_REGISTRY: dict[str, PricingModelSpec] = {
    "black76": PricingModelSpec(
        name="black76",
        aliases=(),
        family="futures_options",
        exercise_style="european",
        price_dynamics="lognormal",
        supports_negative_underlying=False,
        requires_shift=False,
        supports_closed_form_price=True,
        supports_closed_form_greeks=True,
        default_greek_method="closed_form",
        parity_check_mode="equality",
        speed_tier="fast",
        maturity="production",
        runtime_model="black76",
        implemented_price=True,
        implemented_greeks=True,
    ),
    "black76_european": PricingModelSpec(
        name="black76_european",
        aliases=("black76_eur",),
        family="futures_options",
        exercise_style="european",
        price_dynamics="lognormal",
        supports_negative_underlying=False,
        requires_shift=False,
        supports_closed_form_price=True,
        supports_closed_form_greeks=True,
        default_greek_method="closed_form",
        parity_check_mode="equality",
        speed_tier="fast",
        maturity="production",
        runtime_model="black76",
        implemented_price=True,
        implemented_greeks=True,
    ),
    "bs": PricingModelSpec(
        name="bs",
        aliases=(),
        family="equity_options",
        exercise_style="european",
        price_dynamics="lognormal",
        supports_negative_underlying=False,
        requires_shift=False,
        supports_closed_form_price=True,
        supports_closed_form_greeks=True,
        default_greek_method="closed_form",
        parity_check_mode="equality",
        speed_tier="fast",
        maturity="legacy",
        runtime_model="bs",
        uses_dividend_yield=True,
        implemented_price=True,
        implemented_greeks=True,
    ),
    "bsm": PricingModelSpec(
        name="bsm",
        aliases=("black_scholes_merton",),
        family="equity_options",
        exercise_style="european",
        price_dynamics="lognormal",
        supports_negative_underlying=False,
        requires_shift=False,
        supports_closed_form_price=True,
        supports_closed_form_greeks=True,
        default_greek_method="closed_form",
        parity_check_mode="equality",
        speed_tier="fast",
        maturity="production",
        runtime_model="bsm",
        uses_dividend_yield=True,
        implemented_price=True,
        implemented_greeks=True,
    ),
    "bachelier": PricingModelSpec(
        name="bachelier",
        aliases=("normal",),
        family="generic_options",
        exercise_style="european",
        price_dynamics="normal",
        supports_negative_underlying=True,
        requires_shift=False,
        supports_closed_form_price=True,
        supports_closed_form_greeks=False,
        default_greek_method="closed_form",
        parity_check_mode="equality",
        speed_tier="fast",
        maturity="planned",
    ),
    "black76_shifted": PricingModelSpec(
        name="black76_shifted",
        aliases=("shifted_black76",),
        family="futures_options",
        exercise_style="european",
        price_dynamics="shifted_lognormal",
        supports_negative_underlying=True,
        requires_shift=True,
        supports_closed_form_price=True,
        supports_closed_form_greeks=False,
        default_greek_method="closed_form",
        parity_check_mode="equality",
        speed_tier="fast",
        maturity="planned",
    ),
    "black76_baw": PricingModelSpec(
        name="black76_baw",
        aliases=(),
        family="futures_options",
        exercise_style="american",
        price_dynamics="lognormal",
        supports_negative_underlying=False,
        requires_shift=False,
        supports_closed_form_price=False,
        supports_closed_form_greeks=False,
        default_greek_method="numerical_bump",
        parity_check_mode="american_bounds",
        speed_tier="medium",
        maturity="planned",
        approximation="barone_adesi_whaley",
    ),
    "black76_shifted_baw": PricingModelSpec(
        name="black76_shifted_baw",
        aliases=("shifted_black76_baw",),
        family="futures_options",
        exercise_style="american",
        price_dynamics="shifted_lognormal",
        supports_negative_underlying=True,
        requires_shift=True,
        supports_closed_form_price=False,
        supports_closed_form_greeks=False,
        default_greek_method="numerical_bump",
        parity_check_mode="american_bounds",
        speed_tier="medium",
        maturity="planned",
        approximation="barone_adesi_whaley",
    ),
    "bsm_baw": PricingModelSpec(
        name="bsm_baw",
        aliases=(),
        family="equity_options",
        exercise_style="american",
        price_dynamics="lognormal",
        supports_negative_underlying=False,
        requires_shift=False,
        supports_closed_form_price=False,
        supports_closed_form_greeks=False,
        default_greek_method="numerical_bump",
        parity_check_mode="american_bounds",
        speed_tier="medium",
        maturity="planned",
        approximation="barone_adesi_whaley",
    ),
    "crr_binomial": PricingModelSpec(
        name="crr_binomial",
        aliases=("binomial",),
        family="generic_options",
        exercise_style="american_or_european",
        price_dynamics="tree",
        supports_negative_underlying=False,
        requires_shift=False,
        supports_closed_form_price=False,
        supports_closed_form_greeks=False,
        default_greek_method="numerical_bump",
        parity_check_mode="disabled",
        speed_tier="slow",
        maturity="planned_reference",
        approximation="crr_tree",
    ),
    "trinomial": PricingModelSpec(
        name="trinomial",
        aliases=("trinomial_tree",),
        family="generic_options",
        exercise_style="american_or_european",
        price_dynamics="tree",
        supports_negative_underlying=False,
        requires_shift=False,
        supports_closed_form_price=False,
        supports_closed_form_greeks=False,
        default_greek_method="numerical_bump",
        parity_check_mode="disabled",
        speed_tier="slow",
        maturity="planned_reference",
        approximation="trinomial_tree",
    ),
    "finite_difference": PricingModelSpec(
        name="finite_difference",
        aliases=("pde",),
        family="generic_options",
        exercise_style="american_or_european",
        price_dynamics="finite_difference",
        supports_negative_underlying=False,
        requires_shift=False,
        supports_closed_form_price=False,
        supports_closed_form_greeks=False,
        default_greek_method="numerical_bump",
        parity_check_mode="disabled",
        speed_tier="slow",
        maturity="planned_reference",
        approximation="pde",
    ),
}

_ALIASES: dict[str, str] = {
    alias: name
    for name, spec in _REGISTRY.items()
    for alias in spec.aliases
}


def _clean_name(model: str) -> str:
    return str(model).strip().lower().replace("-", "_")


def canonical_model_name(model: str) -> str:
    cleaned = _clean_name(model)
    return _ALIASES.get(cleaned, cleaned)


def unknown_model_message(model: str) -> str:
    supported = ", ".join(implemented_greek_model_names())
    planned = ", ".join(
        name for name in _MODEL_ORDER if name in _REGISTRY and name not in implemented_greek_model_names()
    )
    suffix = f"; planned/not implemented yet: {planned}" if planned else ""
    return f"Unknown pricing model: {model}. Supported implemented models: {supported}{suffix}"


def get_model_spec(model: str) -> PricingModelSpec:
    name = canonical_model_name(model)
    try:
        return _REGISTRY[name]
    except KeyError as exc:
        raise ValueError(unknown_model_message(model)) from exc


def supported_model_names(*, implemented_only: bool = False, include_aliases: bool = False) -> tuple[str, ...]:
    names: list[str] = []
    for name in _MODEL_ORDER:
        spec = _REGISTRY.get(name)
        if spec is None:
            continue
        if implemented_only and not (spec.implemented_price or spec.implemented_greeks):
            continue
        names.append(name)
        if include_aliases:
            names.extend(spec.aliases)
    return tuple(names)


def implemented_price_model_names() -> tuple[str, ...]:
    return tuple(
        name for name in _MODEL_ORDER
        if name in _REGISTRY and _REGISTRY[name].implemented_price
    )


def implemented_greek_model_names() -> tuple[str, ...]:
    return tuple(
        name for name in _MODEL_ORDER
        if name in _REGISTRY and _REGISTRY[name].implemented_greeks
    )


def price_runtime_model(model: str) -> str:
    spec = get_model_spec(model)
    if not spec.implemented_price:
        raise NotImplementedError(
            f"Pricing model is registered but not implemented: {spec.name}"
        )
    return spec.runtime_model or spec.name


def greek_runtime_model(model: str) -> str:
    spec = get_model_spec(model)
    if not spec.implemented_greeks:
        raise NotImplementedError(
            f"Greek model is registered but not implemented: {spec.name}"
        )
    return spec.runtime_model or spec.name


def model_uses_dividend_yield(model: str) -> bool:
    return get_model_spec(model).uses_dividend_yield


def parity_check_mode(model: str) -> str:
    return get_model_spec(model).parity_check_mode


def resolve_pricing_model(
    requested_model: str | None,
    *,
    product_family: str | None,
    option_underlying_type: str | None,
    exercise_style: str | None,
    run_trust_level: str = "official",
    allow_model_approximation: bool = False,
    policy: dict | None = None,
) -> PricingModelResolution:
    """Resolve a user/model-policy request into one canonical runtime model.

    ``auto`` uses product identity first. If the policy target is planned but
    unimplemented, official runs keep that target and report
    ``pricing_model_not_implemented`` so callers can fail closed. Diagnostic
    runs may use a temporary fallback only when approximation is explicit.
    """
    requested = "auto" if requested_model in (None, "") else canonical_model_name(str(requested_model))
    contract_style = _clean_optional(exercise_style)
    family = _clean_optional(product_family)
    underlying_type = _clean_optional(option_underlying_type)
    policy = policy or DEFAULT_PRICING_MODEL_POLICY

    if requested == "auto":
        entry = _policy_entry(policy, family, underlying_type, contract_style)
        if entry is None:
            return PricingModelResolution(
                selected_model="auto",
                pricing_model_target="unknown",
                pricing_model_source="auto",
                pricing_model_runtime_status="not_resolved",
                pricing_model_contract_match=False,
                pricing_model_contract_reason="pricing_model_policy_not_found",
                contract_exercise_style=contract_style,
                selected_model_exercise_style=None,
                is_model_approximation=False,
            )
        target = canonical_model_name(entry["default"])
        target_spec = get_model_spec(target)
        if _model_runtime_implemented(target_spec):
            return PricingModelResolution(
                selected_model=target,
                pricing_model_target=target,
                pricing_model_source="policy_default",
                pricing_model_runtime_status="implemented",
                pricing_model_contract_match=True,
                pricing_model_contract_reason="policy_default_contract_match",
                contract_exercise_style=contract_style,
                selected_model_exercise_style=target_spec.exercise_style,
                is_model_approximation=False,
            )

        diagnostic = str(run_trust_level).strip().lower() == "diagnostic"
        fallback = entry.get("temporary_fallback")
        if diagnostic and allow_model_approximation and fallback:
            selected = canonical_model_name(fallback)
            selected_spec = get_model_spec(selected)
            return PricingModelResolution(
                selected_model=selected,
                pricing_model_target=target,
                pricing_model_source="temporary_fallback",
                pricing_model_runtime_status=(
                    "implemented" if _model_runtime_implemented(selected_spec) else "not_implemented"
                ),
                pricing_model_contract_match=False,
                pricing_model_contract_reason=entry.get(
                    "fallback_label", "temporary_model_approximation"
                ),
                contract_exercise_style=contract_style,
                selected_model_exercise_style=selected_spec.exercise_style,
                is_model_approximation=True,
            )

        return PricingModelResolution(
            selected_model=target,
            pricing_model_target=target,
            pricing_model_source="policy_default",
            pricing_model_runtime_status="not_implemented",
            pricing_model_contract_match=True,
            pricing_model_contract_reason="pricing_model_not_implemented",
            contract_exercise_style=contract_style,
            selected_model_exercise_style=target_spec.exercise_style,
            is_model_approximation=False,
        )

    spec = get_model_spec(requested)
    runtime_status = "implemented" if _model_runtime_implemented(spec) else "not_implemented"
    family_ok = spec.family in {family, "generic_options"} or family is None
    style_ok = (
        contract_style is None
        or spec.exercise_style == contract_style
        or spec.exercise_style == "american_or_european"
    )
    approx = False
    reason = "explicit_model_contract_match"
    match = bool(family_ok and style_ok)
    if not family_ok:
        reason = "pricing_model_family_mismatch"
    elif not style_ok:
        if allow_model_approximation and spec.exercise_style == "european" and contract_style == "american":
            approx = True
            reason = "european_approximation_for_american_contract"
        else:
            reason = "pricing_model_exercise_style_mismatch"
    if runtime_status != "implemented":
        reason = "pricing_model_not_implemented"
    return PricingModelResolution(
        selected_model=spec.name,
        pricing_model_target=spec.name,
        pricing_model_source="explicit",
        pricing_model_runtime_status=runtime_status,
        pricing_model_contract_match=match,
        pricing_model_contract_reason=reason,
        contract_exercise_style=contract_style,
        selected_model_exercise_style=spec.exercise_style,
        is_model_approximation=approx,
    )


def _policy_entry(
    policy: dict,
    family: str | None,
    underlying_type: str | None,
    exercise_style: str | None,
) -> dict | None:
    if family == "futures_options" and underlying_type == "future":
        return ((policy.get("futures_options") or {}).get(exercise_style or ""))
    if family == "equity_options" and underlying_type in {"spot", "equity", "index"}:
        return ((policy.get("equity_options") or {}).get(exercise_style or ""))
    return None


def _model_runtime_implemented(spec: PricingModelSpec) -> bool:
    return bool(spec.implemented_price and spec.implemented_greeks)


def _clean_optional(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    return text or None


def _finite_float(value) -> tuple[bool, float]:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return False, float("nan")
    return isfinite(out), out


def normalize_right(right) -> str | None:
    value = str(right).strip().upper()
    return value if value in {"C", "P"} else None


def validate_pricing_domain(
    model: str,
    S_or_F,
    K,
    T,
    r,
    sigma,
    right,
    *,
    shift: float | None = None,
) -> PricingDomainResult:
    """Validate the active pricing domain for a scalar row."""
    spec = get_model_spec(model)
    right_norm = normalize_right(right)
    if right_norm is None:
        return PricingDomainResult(False, "invalid_right")

    s_ok, s = _finite_float(S_or_F)
    if not s_ok:
        return PricingDomainResult(False, "missing_underlying")
    k_ok, strike = _finite_float(K)
    if not k_ok:
        return PricingDomainResult(False, "missing_strike")
    t_ok, t = _finite_float(T)
    if not t_ok or t <= 0:
        return PricingDomainResult(False, "nonpositive_t")
    r_ok, _ = _finite_float(r)
    if not r_ok:
        return PricingDomainResult(False, "missing_rate")
    sig_ok, vol = _finite_float(sigma)
    if not sig_ok or vol <= 0:
        return PricingDomainResult(False, "nonpositive_sigma")

    if spec.price_dynamics == "lognormal":
        if s <= 0:
            return PricingDomainResult(False, "lognormal_underlying_nonpositive")
        if strike <= 0:
            return PricingDomainResult(False, "lognormal_strike_nonpositive")
        return PricingDomainResult(True)

    if spec.price_dynamics == "shifted_lognormal":
        shift_ok, shift_value = _finite_float(shift)
        if not shift_ok:
            return PricingDomainResult(False, "missing_shift")
        if s + shift_value <= 0:
            return PricingDomainResult(False, "shifted_underlying_nonpositive")
        if strike + shift_value <= 0:
            return PricingDomainResult(False, "shifted_strike_nonpositive")
        return PricingDomainResult(True)

    if spec.price_dynamics == "normal":
        return PricingDomainResult(True)

    return PricingDomainResult(False, "unsupported_price_dynamics")
