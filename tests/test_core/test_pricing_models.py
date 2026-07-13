import pytest

from core import pricing_models as pm


def test_black76_european_runtime_aliases_black76():
    spec = pm.get_model_spec("black76_european")
    assert spec.exercise_style == "european"
    assert spec.price_dynamics == "lognormal"
    assert pm.price_runtime_model("black76_european") == "black76"
    assert pm.greek_runtime_model("black76_european") == "black76"


def test_cli_supported_models_are_implemented_only():
    assert pm.implemented_greek_model_names() == ("black76", "black76_european", "bs", "bsm")
    assert "black76_baw" not in pm.implemented_greek_model_names()
    assert "bachelier" not in pm.implemented_greek_model_names()


def test_registry_keeps_planned_american_metadata_without_enabling_runtime():
    spec = pm.get_model_spec("black76_baw")
    assert spec.exercise_style == "american"
    assert spec.parity_check_mode == "american_bounds"
    with pytest.raises(NotImplementedError, match="not implemented"):
        pm.price_runtime_model("black76_baw")


def test_unknown_model_error_lists_supported_models():
    with pytest.raises(ValueError, match="Supported implemented models: black76"):
        pm.get_model_spec("nope")


def test_lognormal_domain_validator_distinguishes_nonpositive_underlying():
    out = pm.validate_pricing_domain("black76", -37.63, 70.0, 0.5, 0.05, 0.3, "C")
    assert not out.valid
    assert out.reason == "lognormal_underlying_nonpositive"


def test_auto_pricing_targets_american_futures_baw_but_marks_unimplemented():
    out = pm.resolve_pricing_model(
        "auto",
        product_family="futures_options",
        option_underlying_type="future",
        exercise_style="american",
    )

    assert out.selected_model == "black76_baw"
    assert out.pricing_model_target == "black76_baw"
    assert out.pricing_model_runtime_status == "not_implemented"
    assert out.pricing_model_contract_reason == "pricing_model_not_implemented"


def test_diagnostic_auto_can_use_explicit_temporary_fallback():
    out = pm.resolve_pricing_model(
        "auto",
        product_family="futures_options",
        option_underlying_type="future",
        exercise_style="american",
        run_trust_level="diagnostic",
        allow_model_approximation=True,
    )

    assert out.selected_model == "black76_european"
    assert out.pricing_model_target == "black76_baw"
    assert out.pricing_model_source == "temporary_fallback"
    assert out.is_model_approximation is True
    assert out.pricing_model_contract_match is False


def test_explicit_european_model_on_american_contract_requires_approximation_flag():
    bad = pm.resolve_pricing_model(
        "black76_european",
        product_family="futures_options",
        option_underlying_type="future",
        exercise_style="american",
    )
    ok = pm.resolve_pricing_model(
        "black76_european",
        product_family="futures_options",
        option_underlying_type="future",
        exercise_style="american",
        allow_model_approximation=True,
    )

    assert bad.pricing_model_contract_match is False
    assert bad.is_model_approximation is False
    assert ok.pricing_model_contract_match is False
    assert ok.is_model_approximation is True
