# -*- coding: utf-8 -*-
# emacs: -*- mode: python; py-indent-offset: 4; indent-tabs-mode: nil -*-
# vi: set ft=python sts=4 ts=4 sw=4 et:

import os
from pathlib import Path

import pytest
from nipype.interfaces import ants
from templateflow.api import get as get_template

from ...design import group_design
from ...model.variable import VariableSchema


@pytest.fixture(scope="package")
def mni_downsampled(tmp_path_factory):
    tmp_path = tmp_path_factory.mktemp(basename="mni_downsampled")

    os.chdir(str(tmp_path))

    tpl = get_template("MNI152NLin2009cAsym", resolution=2, desc="brain", suffix="mask")

    result = ants.ResampleImageBySpacing(
        dimension=3, input_image=tpl, out_spacing=(6, 6, 6)
    ).run()
    assert result.outputs is not None

    return result.outputs.output_image


@pytest.fixture(scope="package")
def wakemandg_hensonrn_raw_downsampled(
    tmp_path_factory, wakemandg_hensonrn_raw, mni_downsampled
):
    tmp_path = tmp_path_factory.mktemp(basename="wakemandg_hensonrn_downsampled")

    os.chdir(str(tmp_path))

    data = dict()

    def _downsample(in_file):
        result = ants.ApplyTransforms(
            dimension=3,
            input_image_type=0,
            input_image=in_file,
            reference_image=mni_downsampled,
            interpolation="NearestNeighbor",
            transforms=["identity"],
        ).run()
        assert result.outputs is not None

        return result.outputs.output_image

    for k, v in wakemandg_hensonrn_raw.items():
        if isinstance(v, list):
            data[k] = [_downsample(f) if Path(f).exists() else f for f in v]
        else:
            data[k] = v

    return data


@pytest.fixture(scope="package")
def wakemandg_hensonrn(wakemandg_hensonrn_downsampled):
    data = wakemandg_hensonrn_downsampled

    cope_files = data["stat-effect_statmap"]
    var_cope_files = data["stat-variance_statmap"]
    mask_files = data["mask"]

    subjects = data["subjects"]
    spreadsheet_file = data["spreadsheet"]

    variable_schema = VariableSchema()
    variables = [
        variable_schema.load(dict(name="Sub", type="id")),
        variable_schema.load(dict(name="Age", type="continuous")),
        variable_schema.load(
            dict(name="ReactionTime", type="categorical", levels=["1", "2", "3", "4"])
        ),
    ]

    regressors, contrasts, _, _ = group_design(
        subjects=subjects,
        spreadsheet=spreadsheet_file,
        variables=variables,  # type: ignore
        contrasts=[
            {"variable": ["Age"], "type": "infer"},
            {"variable": ["ReactionTime"], "type": "infer"},
        ],
    )

    return (cope_files, var_cope_files, mask_files, regressors, contrasts)
