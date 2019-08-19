"""
General IO routines
===================

"""
from typing import Union
import numpy as np
import pandas as pd
from contextlib import contextmanager

from pathlib import Path

_FILE_TYPES = Union[Path, str]
_MATRIX_TYPES = Union[pd.DataFrame, pd.Series, np.ndarray]


def coerce_matrix(matrix: _MATRIX_TYPES, allow_raw=True, force_square=True) -> np.ndarray:
    """
    Infers a NumPy array from given input

    Args:
        matrix:
        allow_raw:
        force_square:

    Returns:
        2D ndarray of type float32
    """
    if isinstance(matrix, pd.DataFrame):
        if force_square:
            assert matrix.index.equals(matrix.columns)
        return matrix.values.astype(np.float32)
    elif isinstance(matrix, pd.Series):
        assert matrix.index.nlevels == 2, "Cannot infer a matrix from a Series with more or fewer than 2 levels"
        wide = matrix.unstack()

        union = wide.index | wide.columns
        wide = wide.reindex_axis(union, fill_value=0.0, axis=0).reindex_axis(union, fill_value=0.0, axis=1)
        return wide.values.astype(np.float32)

    if not allow_raw:
        raise NotImplementedError()

    matrix = np.array(matrix, dtype=np.float32)
    assert len(matrix.shape) == 2
    i, j = matrix.shape
    assert i == j

    return matrix


def expand_array(a: np.ndarray, n: int, axis: int = None) -> np.ndarray:
    """
    Expands an array across all dimensions by a set amount

    Args:
        a: The array to expand
        n: The (non-negative) number of items to expand by.
        axis (int or None): The axis to expand along, or None to exapnd along all axes

    Returns: The expanded array
    """

    if axis is None: new_shape = [dim + n for dim in a.shape]
    else:
        new_shape = []
        for i, dim in enumerate(a.shape):
            dim += n if i == axis else 0
            new_shape.append(dim)

    out = np.zeros(new_shape, dtype=a.dtype)

    indexer = [slice(0, dim) for dim in a.shape]
    out[indexer] = a

    return out


@contextmanager
def open_file(file_handle: _FILE_TYPES, **kwargs):
    """
    Context manager for opening files provided as several different types. Supports a file handler as a str, unicode,
    pathlib.Path, or an already-opened handler.

    Args:
        file_handle (str or unicode or Path or File): The item to be opened or is already open.
        **kwargs: Keyword args passed to open. Usually mode='w'.

    Yields:
        File: The opened file handler. Automatically closed once out of context.

    """
    opened = False
    if isinstance(file_handle, str):
        f = open(file_handle, **kwargs)
        opened = True
    elif isinstance(file_handle, Path):
        f = file_handle.open(**kwargs)
        opened = True
    else:
        f = file_handle

    try:
        yield f
    finally:
        if opened:
            f.close()
