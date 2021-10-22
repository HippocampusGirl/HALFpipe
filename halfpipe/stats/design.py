# -*- coding: utf-8 -*-
# emacs: -*- mode: python; py-indent-offset: 4; indent-tabs-mode: nil -*-
# vi: set ft=python sts=4 ts=4 sw=4 et:

from typing import Dict, List, Tuple

from itertools import product
from collections import OrderedDict
from pathlib import Path

import pandas as pd
from pandas.api.types import is_numeric_dtype
import numpy as np
from patsy.desc import ModelDesc, Term  # separate imports as to not confuse type checker
from patsy.highlevel import dmatrix
from patsy.user_util import LookupFactor

from ..io import loadspreadsheet
from ..utils import logger


def _check_multicollinearity(matrix):
    # taken from C-PAC

    logger.info("Checking for multicollinearity in the model..")

    _, s, _ = np.linalg.svd(matrix)
    max_singular = np.max(s)
    min_singular = np.min(s)

    rank = np.linalg.matrix_rank(matrix)  # type: ignore

    logger.info(
        f"max_singular={max_singular} min_singular={min_singular} "
        f"rank={rank}"
    )

    if min_singular == 0:
        logger.warning(
            "Detected multicollinearity in the computed group-level analysis model."
            + "Please double-check your model design."
        )


def _prepare_data_frame(
    spreadsheet: Path, variabledicts: List[Dict], subjects: List[str]
) -> pd.DataFrame:
    rawdataframe: pd.DataFrame = loadspreadsheet(spreadsheet, dtype=object)

    id_column = None
    for variabledict in variabledicts:
        if variabledict["type"] == "id":
            id_column = variabledict["name"]
            break

    assert id_column is not None, "Missing id column, cannot specify model"

    rawdataframe[id_column] = pd.Series(rawdataframe[id_column], dtype=str)  # type: ignore
    if all(str(id).startswith("sub-") for id in rawdataframe[id_column]):  # for bids
        rawdataframe[id_column] = [
            str(id).replace("sub-", "") for id in rawdataframe[id_column]
        ]
    rawdataframe = rawdataframe.set_index(id_column)

    continuous_columns = []
    categorical_columns = []
    columns_in_order: List[str] = []
    for variabledict in variabledicts:
        if variabledict["type"] == "continuous":
            continuous_columns.append(variabledict["name"])
            columns_in_order.append(variabledict["name"])
        elif variabledict["type"] == "categorical":
            categorical_columns.append(variabledict["name"])
            columns_in_order.append(variabledict["name"])

    # separate
    continuous = rawdataframe[continuous_columns]
    categorical = rawdataframe[categorical_columns]

    # only keep subjects that are in this analysis
    # also sets order
    continuous = continuous.loc[subjects]
    categorical = categorical.loc[subjects]

    # change type to numeric
    continuous = continuous.astype(float)

    # change type first to string then to category
    categorical = categorical.astype(str)
    categorical = categorical.astype("category")

    # merge
    dataframe = pd.merge(
        categorical,
        continuous,
        how="outer",
        left_index=True,
        right_index=True
    )

    # maintain order
    dataframe = dataframe.loc[subjects, columns_in_order]

    return dataframe


def _generate_rhs(contrastdicts, columns_var_gt_0) -> List[Term]:
    rhs = [Term([])]  # force intercept
    for contrastdict in contrastdicts:
        if contrastdict["type"] == "infer":
            if not columns_var_gt_0[contrastdict["variable"]].all():
                logger.warning(
                    f'Not adding term "{contrastdict["variable"]}" to design matrix '
                    "because it has zero variance"
                )
                continue
            # for every term in the model a contrast of type infer needs to be specified
            rhs.append(Term([LookupFactor(name) for name in contrastdict["variable"]]))

    return rhs


def _make_contrasts_list(contrast_matrices: List[Tuple[str, pd.DataFrame]]):
    contrasts: List[Tuple] = []
    contrast_names = []

    for contrast_name, contrast_matrix in contrast_matrices:  # t contrasts
        contrast_name = contrast_name.capitalize()

        if contrast_matrix.shape[0] == 1:
            _, contrast_vector = next(contrast_matrix.iterrows())
            contrasts.append(
                (contrast_name, "T", list(contrast_vector.index), list(contrast_vector))
            )

            contrast_names.append(contrast_name)

    for contrast_name, contrast_matrix in contrast_matrices:  # f contrasts
        contrast_name = contrast_name.capitalize()

        if contrast_matrix.shape[0] > 1:

            tcontrasts = []  # an f contrast consists of multiple t contrasts
            for i, contrast_vector in contrast_matrix.iterrows():
                tname = f"{contrast_name}_{i:d}"
                tcontrasts.append(
                    (tname, "T", list(contrast_vector.index), list(contrast_vector))
                )

            contrasts.extend(tcontrasts)  # add t contrasts to the model
            contrasts.append((contrast_name, "F", tcontrasts))  # then add the f contrast

            contrast_names.append(contrast_name)  # we only care about the f contrast

    contrast_numbers = [f"{i:02d}" for i in range(1, len(contrast_names) + 1)]

    return contrasts, contrast_numbers, contrast_names


def intercept_only_design(
    n: int
) -> Tuple[Dict[str, List[float]], List[Tuple], List[str], List[str]]:
    return (
        {"intercept": [1.0] * n},
        [("intercept", "T", ["intercept"], [1])],
        ["01"],
        ["intercept"],
    )


def group_design(
    spreadsheet: Path, contrastdicts: List[Dict], variabledicts: List[Dict], subjects: List[str]
) -> Tuple[Dict[str, List[float]], List[Tuple], List[str], List[str]]:

    dataframe = _prepare_data_frame(spreadsheet, variabledicts, subjects)

    # remove zero variance columns
    columns_var_gt_0 = dataframe.apply(pd.Series.nunique) > 1  # does not count NA
    assert isinstance(columns_var_gt_0, pd.Series)
    dataframe = dataframe.loc[:, columns_var_gt_0]

    # don't need to specify lhs
    lhs: List[Term] = []

    # generate rhs
    rhs = _generate_rhs(contrastdicts, columns_var_gt_0)

    # specify patsy design matrix
    modelDesc = ModelDesc(lhs, rhs)
    dmat = dmatrix(modelDesc, dataframe, return_type="dataframe")
    _check_multicollinearity(dmat)

    # prepare lsmeans
    unique_values_categorical = [
        (0.0,)
        if is_numeric_dtype(dataframe[f])
        else dataframe[f].unique()
        for f in dataframe.columns
    ]
    grid = pd.DataFrame(
        list(product(*unique_values_categorical)), columns=dataframe.columns
    )
    reference_dmat = dmatrix(dmat.design_info, grid, return_type="dataframe")

    # data frame to store contrasts
    contrast_matrices: List[Tuple[str, pd.DataFrame]] = []

    for field, columnslice in dmat.design_info.term_name_slices.items():  # type: ignore
        constraint = {
            column: 0 for column in dmat.design_info.column_names[columnslice]  # type: ignore
        }
        contrast = dmat.design_info.linear_constraint(constraint)  # type: ignore

        assert np.all(contrast.variable_names == dmat.columns)

        contrast_matrix = pd.DataFrame(contrast.coefs, columns=dmat.columns)

        if field == "Intercept":  # do not capitalize
            field = field.lower()
        contrast_matrices.append((field, contrast_matrix))

    for contrastdict in contrastdicts:
        if contrastdict["type"] == "t":
            (variable,) = contrastdict["variable"]
            variable_levels: List[str] = list(dataframe[variable].unique())

            # Generate the lsmeans matrix where there is one row for each
            # factor level. Each row is a contrast vector.
            # This contrast vector corresponds to the mean of the dependent
            # variable at the factor level.
            # For example, we would have one row that calculates the mean
            # for patients, and one for controls.

            lsmeans = pd.DataFrame(index=variable_levels, columns=dmat.columns)
            for level in variable_levels:
                reference_rows = reference_dmat.loc[grid[variable] == level]
                lsmeans.loc[level] = reference_rows.mean()

            value_dict = contrastdict["values"]
            names = [name for name in value_dict.keys() if name in variable_levels]
            values = [value_dict[name] for name in names]

            # If we wish to test the mean of each group against zero,
            # we can simply use these contrasts and be done.
            # To test a linear hypothesis such as patient-control=0,
            # which is expressed here as {"patient":1, "control":-1},
            # we translate it to a contrast vector by taking the linear
            # combination of the lsmeans contrasts.

            contrast_vector = lsmeans.loc[names].mul(values, axis=0).sum()  # type: ignore
            contrast_matrix = pd.DataFrame([contrast_vector], columns=dmat.columns)

            contrast_name = f"{contrastdict['name']}"
            contrast_matrices.append((contrast_name, contrast_matrix))

    npts, nevs = dmat.shape

    if nevs >= npts:
        logger.warning(
            "Reverting to simple intercept only design. \n"
            f"nevs ({nevs}) >= npts ({npts})"
        )
        return intercept_only_design(len(subjects))

    regressors = dmat.to_dict(orient="list", into=OrderedDict)  # type: ignore
    contrasts, contrast_numbers, contrast_names = _make_contrasts_list(
        contrast_matrices
    )

    return regressors, contrasts, contrast_numbers, contrast_names
