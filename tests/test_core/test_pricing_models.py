import pytest

from core import pricing_models as pm


def test_black76_european_runtime_aliases_black76():
    spec = pm.get_model_spec("black76_european")
    assert spec.exercise_style == "european"
    assert spec.price_dynamics == "lognormal"
    assert pm.price_runtime_model("black76_european") == "black76"
    assert pm.greek_runtime_model("black76_european") == "black76"


def test_cli_supported_models_are_implemented_only():
    assert pm.implemented_greek_model_names() == (
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
    )


def test_plain_bs_does_not_consume_dividend_yield():
    assert pm.model_uses_dividend_yield("bs") is False
    assert pm.model_uses_dividend_yield("bsm") is True


def test_registry_enables_baw_price_and_numerical_greeks():
    spec = pm.get_model_spec("black76_baw")
    assert spec.exercise_style == "american"
    assert spec.parity_check_mode == "american_bounds"
    assert pm.price_runtime_model("black76_baw") == "black76_baw"
    assert pm.greek_runtime_model("black76_baw") == "black76_baw"
    assert pm.default_greek_method("black76_baw") == "numerical_bump"


def test_negative_domain_price_engines_are_runtime_registered():
    assert pm.price_runtime_model("bachelier") == "bachelier"
    assert pm.price_runtime_model("normal") == "bachelier"
    assert pm.price_runtime_model("black76_shifted") == "black76_shifted"


def test_unknown_model_error_lists_supported_models():
    with pytest.raises(ValueError, match="Supported implemented models: black76"):
        pm.get_model_spec("nope")


def test_lognormal_domain_validator_distinguishes_nonpositive_underlying():
    out = pm.validate_pricing_domain("black76", -37.63, 70.0, 0.5, 0.05, 0.3, "C")
    assert not out.valid
    assert out.reason == "lognormal_underlying_nonpositive"


def test_auto_pricing_targets_implemented_american_futures_baw():
    out = pm.resolve_pricing_model(
        "auto",
        product_family="futures_options",
        option_underlying_type="future",
        exercise_style="american",
    )

    assert out.selected_model == "black76_baw"
    assert out.pricing_model_target == "black76_baw"
    assert out.pricing_model_runtime_status == "implemented"
    assert out.pricing_model_contract_reason == "policy_default_contract_match"


def test_diagnostic_auto_uses_baw_now_that_runtime_is_available():
    out = pm.resolve_pricing_model(
        "auto",
        product_family="futures_options",
        option_underlying_type="future",
        exercise_style="american",
        run_trust_level="diagnostic",
        allow_model_approximation=True,
    )

    assert out.selected_model == "black76_baw"
    assert out.pricing_model_target == "black76_baw"
    assert out.pricing_model_source == "policy_default"
    assert out.is_model_approximation is False
    assert out.pricing_model_contract_match is True


def test_explicit_european_model_on_american_contract_requires_approximation_flag():
    bad = pm.resolve_pricing_model(
        "black76_european",
        product_family="futures_options",
        option_underlying_type="future",
        exercise_style="american",
    )
    official = pm.resolve_pricing_model(
        "black76_european",
        product_family="futures_options",
        option_underlying_type="future",
        exercise_style="american",
        allow_model_approximation=True,
    )
    ok = pm.resolve_pricing_model(
        "black76_european",
        product_family="futures_options",
        option_underlying_type="future",
        exercise_style="american",
        run_trust_level="diagnostic",
        allow_model_approximation=True,
    )

    assert bad.pricing_model_contract_match is False
    assert bad.is_model_approximation is False
    assert official.pricing_model_contract_match is False
    assert official.is_model_approximation is False
    assert ok.pricing_model_contract_match is False
    assert ok.is_model_approximation is True


def test_crr_reference_engine_is_runtime_registered():
    spec = pm.get_model_spec("crr_binomial")
    assert spec.maturity == "production_reference"
    assert pm.price_runtime_model("binomial") == "crr_binomial"
    assert pm.greek_runtime_model("crr_binomial") == "crr_binomial"
