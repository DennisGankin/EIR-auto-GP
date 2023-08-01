from pathlib import Path
from typing import Callable

import pandas as pd

from eir_auto_gp.run import get_argument_parser, run, store_experiment_config
from eir_auto_gp.post_analysis import run_post_analysis


def _get_test_modelling_cl_command(folder_path: Path, target_type: str) -> str:
    base = (
        f"--genotype_data_path  {folder_path}/ "
        f"--label_file_path {folder_path}/phenotype.csv  "
        "--global_output_folder runs/simulated_test "
        f"--output_{target_type}_columns phenotype "
        "--folds 2 "
        "--do_test "
        "--feature_selection dl "
        "--n_dl_feature_selection_setup_folds 1"
    )

    return base


def test_post_analysis_classification(
    simulate_genetic_data_to_bed: Callable[[int, int, str], Path],
    tmp_path: Path,
) -> None:
    simulated_path = simulate_genetic_data_to_bed(10000, 12, "binary")

    command = _get_test_modelling_cl_command(
        folder_path=simulated_path, target_type="cat"
    )

    parser = get_argument_parser()
    cl_args = parser.parse_args(command.split())
    cl_args.global_output_folder = str(tmp_path)
    store_experiment_config(cl_args=cl_args)
    run(cl_args=cl_args)

    post_analysis_parser = run_post_analysis.get_argument_parser()
    post_command = f"--run_dir {tmp_path} --top_n_snps 128"
    cl_args_post = post_analysis_parser.parse_args(post_command.split())
    post_analysis_object = run_post_analysis.build_post_analysis_object(
        cl_args=cl_args_post
    )
    run_post_analysis.run_complexity_analysis(post_analysis_object=post_analysis_object)
    run_post_analysis.run_effect_analysis(post_analysis_object=post_analysis_object)

    post_analysis_folder = tmp_path / "analysis" / "post_analysis"
    _check_post_analysis_results_wrapper(
        post_analysis_folder=post_analysis_folder,
        include_tabular=False,
    )


def test_post_analysis_regression(
    simulate_genetic_data_to_bed: Callable[[int, int, str], Path],
    tmp_path: Path,
) -> None:
    simulated_path = simulate_genetic_data_to_bed(10000, 12, "continuous")

    command = _get_test_modelling_cl_command(
        folder_path=simulated_path, target_type="con"
    )

    parser = get_argument_parser()
    cl_args = parser.parse_args(command.split())
    cl_args.global_output_folder = str(tmp_path)
    store_experiment_config(cl_args=cl_args)
    run(cl_args=cl_args)

    post_analysis_parser = run_post_analysis.get_argument_parser()
    post_command = f"--run_dir {tmp_path} --top_n_snps 128"
    cl_args_post = post_analysis_parser.parse_args(post_command.split())
    post_analysis_object = run_post_analysis.build_post_analysis_object(
        cl_args=cl_args_post
    )
    run_post_analysis.run_complexity_analysis(post_analysis_object=post_analysis_object)
    run_post_analysis.run_effect_analysis(post_analysis_object=post_analysis_object)

    post_analysis_folder = tmp_path / "analysis" / "post_analysis"
    _check_post_analysis_results_wrapper(
        post_analysis_folder=post_analysis_folder,
        include_tabular=False,
    )


def _check_post_analysis_results_wrapper(
    post_analysis_folder: Path, include_tabular: bool
) -> None:
    _check_complexity_analysis_results(
        complexity_folder=post_analysis_folder / "complexity",
        include_tabular=include_tabular,
    )


def _check_complexity_analysis_results(
    complexity_folder: Path,
    include_tabular: bool,
) -> None:
    expected_runs = 8 if include_tabular else 4
    df = pd.read_csv(complexity_folder / "all_results.csv")

    assert len(df) == expected_runs

    _check_xgboost_better_than_linear(df=df)
    _check_one_hot_better_in_linear(df=df)

    predictions_folder = complexity_folder / "predictions"
    assert len(list(predictions_folder.glob("numerical/*.csv"))) == expected_runs
    assert len(list(predictions_folder.glob("raw/*.csv"))) == expected_runs


def _check_xgboost_better_than_linear(df: pd.DataFrame) -> None:
    avg_perf_xgboost = df[df["model_type"] == "xgboost"]["average_performance"].mean()
    avg_perf_linear = df[df["model_type"] == "linear"]["average_performance"].mean()
    assert avg_perf_xgboost > avg_perf_linear


def _check_one_hot_better_in_linear(df: pd.DataFrame) -> None:
    df_linear = df[df["model_type"] == "linear"]

    df_linear_one_hot = df_linear[df_linear["one_hot_encode"]]

    df_linear_no_one_hot = df_linear[~df_linear["one_hot_encode"]]

    avg_perf_linear_one_hot = df_linear_one_hot["average_performance"].mean()
    avg_perf_linear_no_one_hot = df_linear_no_one_hot["average_performance"].mean()

    assert avg_perf_linear_one_hot > avg_perf_linear_no_one_hot


def _check_effect_analysis_results(effects_folder: Path) -> None:
    df_allele_effects = pd.read_csv(effects_folder / "allele_effects.csv")
    _check_allele_effects(df_allele_effects=df_allele_effects)

    df_interaction_effects = pd.read_csv(effects_folder / "interaction_effects.csv")
    _check_interaction_effects(df_interactions=df_interaction_effects)


def _check_allele_effects(df_allele_effects: pd.DataFrame) -> None:
    df_allele_effects["SNP"] = df_allele_effects["allele"].str.split(" ").str[0]
    _check_basic_snps_significant_p_values(df=df_allele_effects)
    _check_linear_coefficients(df=df_allele_effects)
    _check_dominant_coefficients(df=df_allele_effects)
    _check_recessive_coefficients(df=df_allele_effects)


def _check_basic_snps_significant_p_values(df: pd.DataFrame) -> None:
    for snp in range(1, 7):
        df_snp = df[df["SNP"] == f"snp{snp}"]
        assert (df_snp["p_value"] < 5e-8).sum() >= 2


def _check_linear_coefficients(df: pd.DataFrame) -> None:
    for snp in [1, 2]:
        df_snp = df[df["SNP"] == f"snp{snp}"]
        diffs = df_snp["Coefficient"].diff().dropna()
        assert diffs.abs().diff().abs().max() < 0.25


def _check_dominant_coefficients(df: pd.DataFrame) -> None:
    df_snp = df[df["SNP"] == "snp3"].sort_values(by="allele")
    assert abs(df_snp.iloc[1, 1] - df_snp.iloc[2, 1]) < 0.25


def _check_recessive_coefficients(df: pd.DataFrame) -> None:
    df_snp = df[df["SNP"] == "snp4"].sort_values(by="allele")
    assert abs(df_snp.iloc[0, 1] - df_snp.iloc[1, 1]) < 0.25


def _check_interaction_effects(df_interactions: pd.DataFrame) -> None:
    df_interaction_terms = df_interactions[df_interactions["allele"].str.contains(":")]
    max_interaction_coefficient = df_interaction_terms.loc[
        df_interaction_terms["Coefficient"].idxmax(),
        "KEY",
    ]
    assert max_interaction_coefficient == "snp5:snp6"
