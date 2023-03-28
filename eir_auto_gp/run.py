import argparse
from argparse import RawTextHelpFormatter
import shutil
from copy import copy
from pathlib import Path
from typing import Dict, Any, Sequence

import luigi
import pandas as pd

from eir_auto_gp.analysis.run_analysis import RunAnalysisWrapper
from eir_auto_gp.utils.utils import get_logger
from eir_auto_gp.preprocess.gwas_pre_selection import validate_geno_data_path

logger = get_logger(name=__name__)


def get_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(formatter_class=RawTextHelpFormatter)

    parser.add_argument(
        "--genotype_data_path",
        type=str,
        required=True,
        help="Root path to raw genotype data to be processed\n"
        "(e.g., containing my_data.bed, my_data.fam, my_data.bim).\n"
        "For this example, this parameter should be\n"
        "'/path/to/raw/genotype/data/'.\n"
        "Note that the file names are not included in this path,\n"
        "only the root folder. The file names are inferred, and\n"
        "*only one* set of files is expected.",
    )

    parser.add_argument(
        "--label_file_path",
        type=str,
        required=True,
        help="File path to label file with tabular inputs and labels to predict.",
    )

    parser.add_argument(
        "--global_output_folder",
        type=str,
        required=False,
        help="Common root folder to save data, feature selection and modelling results"
        " in.",
    )

    parser.add_argument(
        "--data_output_folder",
        type=str,
        required=False,
        help="Folder to save the processed data in.",
    )

    parser.add_argument(
        "--feature_selection_output_folder",
        type=str,
        required=False,
        help="Folder to save feature selection results in.",
    )

    parser.add_argument(
        "--modelling_output_folder",
        type=str,
        required=False,
        help="Folder to save modelling results in.",
    )

    parser.add_argument(
        "--analysis_output_folder",
        type=str,
        required=False,
        help="Folder to save analysis results in.",
    )

    parser.add_argument(
        "--output_name",
        type=str,
        default="genotype",
        help="Name used for dataset.",
    )

    parser.add_argument(
        "--pre_split_folder",
        type=str,
        required=False,
        help="If there is a pre-split folder, this will be used to\n"
        "split the data into train/val and test sets. If not,\n"
        "the data will be split randomly."
        "The folder should contain the following files:\n"
        "  - train.txt: List of sample IDs to use for training.\n"
        "  - test.txt: List of sample IDs to use for testing.\n"
        "If this option is not specified, the data will be split randomly"
        "into 90/10 train/test sets.",
    )

    parser.add_argument(
        "--feature_selection",
        default="gwas->dl",
        choices=["dl", "gwas", "gwas->dl", None],
        required=False,
        help="""\
    What kind of feature selection strategy to use for SNP selection:
      - If None, no feature selection is performed.
      - If 'dl', feature selection is performed using DL feature importance,
        and the top SNPs are selected iteratively using Bayesian optimization.
      - If 'gwas', feature selection is performed using GWAS p-values,
        as specified by the --gwas_p_value_threshold parameter.
      - If 'gwas->dl', feature selection is first performed using GWAS p-values,
        and then the top SNPs are selected iteratively using the DL importance method,
        but only on the SNPs under the GWAS threshold.
    """,
    )

    parser.add_argument(
        "--n_dl_feature_selection_folds",
        type=int,
        default=3,
        required=False,
        help="How many folds to run DL attribution calculation on genotype data\n"
        "before using results from attributions for feature selection.\n"
        "Applicable only if using 'dl' or 'gwas->dl' feature_selection options.",
    )

    parser.add_argument(
        "--gwas_p_value_threshold",
        type=float,
        required=False,
        default=1e-04,
        help="GWAS p-value threshold for filtering if using 'gwas' or 'gwas->dl'\n"
        "feature_selection options.",
    )

    parser.add_argument(
        "--folds",
        type=str,
        default="0-5",
        help="Training runs / folds to run, can be a single fold (e.g. 0),\n"
        "a range of folds (e.g. 0-5), or a comma-separated list of \n"
        "folds (e.g. 0,1,2,3,4,5).",
    )

    parser.add_argument(
        "--input_cat_columns",
        nargs="*",
        type=str,
        default=[],
        help="List of categorical columns to use as input.",
    )

    parser.add_argument(
        "--input_con_columns",
        nargs="*",
        type=str,
        default=[],
        help="List of continuous columns to use as input.",
    )

    parser.add_argument(
        "--output_cat_columns",
        nargs="*",
        type=str,
        default=[],
        help="List of categorical columns to use as output.",
    )

    parser.add_argument(
        "--output_con_columns",
        nargs="*",
        type=str,
        default=[],
        help="List of continuous columns to use as output.",
    )

    parser.add_argument(
        "--do_test",
        action="store_true",
        help="Whether to run test set prediction.",
    )

    return parser


def get_cl_args(parser: argparse.ArgumentParser) -> argparse.Namespace:
    cl_args = parser.parse_args()

    return cl_args


def validate_label_file(
    label_file_path: str,
    input_cat_columns: list[str],
    input_con_columns: list[str],
    output_cat_columns: list[str],
    output_con_columns: list[str],
) -> None:
    if not Path(label_file_path).exists():
        raise ValueError(
            f"Label file path {label_file_path} is invalid. "
            f"Expected to find {label_file_path}."
        )

    columns = pd.read_csv(label_file_path, nrows=1).columns
    if "ID" not in columns:
        raise ValueError(
            f"Label file path {label_file_path} is invalid. "
            f"Expected to find 'ID' column."
        )

    all_columns = set(
        input_cat_columns + input_con_columns + output_cat_columns + output_con_columns
    )
    missing_columns = all_columns - set(columns)
    if len(missing_columns) > 0:
        raise ValueError(
            f"Label file path {label_file_path} is invalid. "
            f"Expected to find columns {missing_columns}."
        )


def validate_targets(
    output_con_columns: list[str], output_cat_columns: list[str]
) -> None:
    if len(output_con_columns) == 0 and len(output_cat_columns) == 0:
        raise ValueError(
            "At least one output column must be specified as continuous or categorical."
        )

    if len(output_con_columns) + len(output_cat_columns) > 1:
        raise ValueError(
            "Currently only one target column per run is supported. Got "
            f"{output_con_columns} continuous and {output_cat_columns} "
            "categorical target columns."
        )


def validate_plink2_exists_in_path() -> None:
    if shutil.which("plink2") is None:
        raise ValueError(
            "plink2 is not installed or not in the path. "
            "Please install plink2 and try again."
        )


def run(cl_args: argparse.Namespace) -> None:
    validate_geno_data_path(geno_data_path=cl_args.genotype_data_path)
    validate_label_file(
        label_file_path=cl_args.label_file_path,
        input_cat_columns=cl_args.input_cat_columns,
        input_con_columns=cl_args.input_con_columns,
        output_cat_columns=cl_args.output_cat_columns,
        output_con_columns=cl_args.output_con_columns,
    )
    validate_targets(
        output_con_columns=cl_args.output_con_columns,
        output_cat_columns=cl_args.output_cat_columns,
    )
    validate_plink2_exists_in_path()

    cl_args = parse_output_folders(cl_args=cl_args)
    cl_args = _add_pre_split_folder_if_present(cl_args=cl_args)

    data_config = build_data_config(cl_args=cl_args)
    feature_selection_config = build_feature_selection_config(cl_args=cl_args)
    modelling_config = build_modelling_config(cl_args=cl_args)
    analysis_config = build_analysis_config(cl_args=cl_args)
    root_task = RunAnalysisWrapper(
        folds=cl_args.folds,
        data_config=data_config,
        feature_selection_config=feature_selection_config,
        modelling_config=modelling_config,
        analysis_config=analysis_config,
    )

    luigi.build(
        tasks=[root_task],
        workers=1,
        local_scheduler=True,
    )


def main():
    parser = get_argument_parser()
    cl_args = get_cl_args(parser=parser)

    run(cl_args=cl_args)


def parse_output_folders(cl_args: argparse.Namespace) -> argparse.Namespace:
    cl_args_copy = copy(cl_args)
    if cl_args_copy.global_output_folder:
        gof = cl_args_copy.global_output_folder.rstrip("/")
        cl_args_copy.data_output_folder = gof + "/data"
        cl_args_copy.feature_selection_output_folder = gof + "/feature_selection"
        cl_args_copy.modelling_output_folder = gof + "/modelling"
        cl_args_copy.analysis_output_folder = gof + "/analysis"
    else:
        if not cl_args_copy.data_output_folder:
            raise ValueError(
                "Missing data output folder. "
                "Either a global output folder or a "
                "data output folder must be provided."
            )
        if not cl_args_copy.feature_selection_output_folder:
            raise ValueError(
                "Missing feature selection output folder. "
                "Either a global output folder or a "
                "feature selection output folder must be provided."
            )
        if not cl_args_copy.modelling_output_folder:
            raise ValueError(
                "Missing modelling output folder. "
                "Either a global output folder or a "
                "modelling output folder must be provided."
            )
        if not cl_args_copy.analysis_output_folder:
            raise ValueError(
                "Missing analysis output folder. "
                "Either a global output folder or a "
                "analysis output folder must be provided."
            )

    return cl_args_copy


def _add_pre_split_folder_if_present(cl_args: argparse.Namespace) -> argparse.Namespace:
    cl_args_copy = copy(cl_args)
    genotype_path = Path(cl_args_copy.genotype_data_path)

    if (genotype_path / "ids").exists():
        cl_args_copy.pre_split_folder = str(genotype_path / "ids")
        logger.info(
            f"Found pre-split folder {cl_args_copy.pre_split_folder}. "
            f"in root genotype folder. Using those for train/test split."
        )

    return cl_args_copy


def build_data_config(cl_args: argparse.Namespace) -> Dict[str, Any]:
    data_keys = [
        "genotype_data_path",
        "label_file_path",
        "data_output_folder",
        "output_name",
        "pre_split_folder",
    ]

    base = extract_from_namespace(namespace=cl_args, keys=data_keys)
    base["output_format"] = "deeplake"

    return base


def build_feature_selection_config(cl_args: argparse.Namespace) -> Dict[str, Any]:
    feature_selection_keys = [
        "feature_selection_output_folder",
        "feature_selection",
        "n_dl_feature_selection_folds",
        "gwas_p_value_threshold",
    ]

    return extract_from_namespace(namespace=cl_args, keys=feature_selection_keys)


def build_modelling_config(cl_args: argparse.Namespace) -> Dict[str, Any]:
    modelling_keys = [
        "modelling_output_folder",
        "input_cat_columns",
        "input_con_columns",
        "output_cat_columns",
        "output_con_columns",
        "do_test",
    ]

    return extract_from_namespace(namespace=cl_args, keys=modelling_keys)


def build_analysis_config(cl_args: argparse.Namespace) -> Dict[str, Any]:
    analysis_keys = [
        "analysis_output_folder",
    ]

    return extract_from_namespace(namespace=cl_args, keys=analysis_keys)


def extract_from_namespace(
    namespace: argparse.Namespace, keys: Sequence[str]
) -> Dict[str, Any]:
    return {key: getattr(namespace, key) for key in keys}


if __name__ == "__main__":
    main()
