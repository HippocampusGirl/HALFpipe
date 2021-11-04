# -*- coding: utf-8 -*-
# emacs: -*- mode: python; py-indent-offset: 4; indent-tabs-mode: nil -*-
# vi: set ft=python sts=4 ts=4 sw=4 et:

from json import dumps

import pytest
import numpy as np

from ..json import NumpyJSONEncoder


def test_bool():
    x = np.int32(5) > np.int32(6)

    with pytest.raises(Exception):
        dumps(dict(x=x))

    dumps(dict(x=x), cls=NumpyJSONEncoder)


@pytest.mark.parametrize("cls", [np.int32, np.uint32])
def test_int(cls):
    x = cls(5)

    with pytest.raises(Exception):
        dumps(dict(x=x))

    dumps(dict(x=x), cls=NumpyJSONEncoder)


def test_float():
    x = np.float32(5)

    with pytest.raises(Exception):
        dumps(dict(x=x))

    dumps(dict(x=x), cls=NumpyJSONEncoder)