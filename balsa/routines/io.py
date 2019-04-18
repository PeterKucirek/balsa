from __future__ import division
from __future__ import print_function

from contextlib import contextmanager

import numpy as np
import pandas as pd
from six import iteritems, itervalues, iterkeys, string_types

from balsa.routines.matrices import fast_unstack

try:
    from pathlib import Path
except ImportError:
    Path = None

try:
    import openmatrix as omx
except ImportError:
    omx = None

# region Common functions


def coerce_matrix(matrix, allow_raw=True, force_square=True):
    """
    Infers a NumPy array from given input

    Args:
        matrix (DataFrame or Series or ndarray or Iterable):

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


def expand_array(a, n, axis=None):
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


# endregion

# region INRO MDF (MatrixData File) format


def read_mdf(file, raw=False, tall=False):
    """
    Reads Emme's official matrix "binary serialization" format, created using inro.emme.matrix.MatrixData.save(). There
    is no official extension for this type of file; '.mdf' is recommended. '.emxd' is also sometimes encountered.

    Args:
        file (str or File or Path): The file to read.
        raw (bool): If True, returns an unlabelled ndarray. Otherwise, a DataFrame will be returned.
        tall (bool): If True, a 1D data structure will be returned. If `raw` is False, a Series will be returned,
            otherwise a 1D ndarray.
    Returns:
        ndarray or DataFrame of the matrix stored in the file.
    """
    with open_file(file, mode='rb') as file_handler:
        magic, version, dtype_index, ndim = np.fromfile(file_handler, np.uint32, count=4)

        if magic != 0xC4D4F1B2 or version != 1 or not (0 < dtype_index <= 4) or not (0 < ndim <= 2):
            raise IOError("Unexpected file header: magic number: %X, version: %d, data type: %d, dimensions: %d."
                          % (magic, version, dtype_index, ndim))

        shape = np.fromfile(file_handler, np.uint32, count=ndim)

        index_list = []
        for n_items in shape:
            indices = np.fromfile(file_handler, np.int32, n_items)
            index_list.append(indices)

        dtype = {1: np.float32, 2: np.float64, 3: np.int32, 4: np.uint32}[dtype_index]
        flat_length = shape.prod()  # Multiply the shape tuple
        matrix = np.fromfile(file_handler, dtype, count=flat_length)

        if raw and tall: return matrix

        matrix.shape = shape

        if raw: return matrix

        if ndim == 1:
            return pd.Series(matrix, index=index_list[0])
        elif ndim == 2:
            matrix = pd.DataFrame(matrix, index=index_list[0], columns=index_list[1])

            return matrix.stack() if tall else matrix

        raise NotImplementedError()  # This should never happen


def to_mdf(matrix, file):
    """
    Writes a matrix to Emme's official "binary serialization" format, to load using inro.emme.matrix.MatrixData.load().
    There is no official extension for this type of file; '.mdf' is recommended.

    Args:
        matrix (DataFrame or Series): The matrix to write to disk. If a Series is given, it MUST have a
            MultiIndex with exactly 2 levels to unstack.
        file (basestring or File or Path): The path or file handler to write to.
    """
    if isinstance(matrix, pd.Series):
        row_index = matrix.index.get_level_values(0).unique()
        column_index = matrix.index.get_level_values(1).unique()
    elif isinstance(matrix, pd.DataFrame):
        row_index = matrix.index
        column_index = matrix.columns
    else:
        raise TypeError("Only labelled matrix objects are supported")

    with open_file(file, mode='wb') as writer:
        data = coerce_matrix(matrix, allow_raw=False)

        np.array([0xC4D4F1B2, 1, 1, 2], dtype=np.uint32).tofile(writer)  # Header
        np.array(data.shape, dtype=np.uint32).tofile(writer)  # Shape

        np.array(row_index, dtype=np.int32).tofile(writer)
        np.array(column_index, dtype=np.int32).tofile(writer)

        data.tofile(writer)


def peek_mdf(file, as_index=True):
    """
    Partially opens an MDF file to get the zone system of its rows and its columns.
    Args:
        file (str or File or Path): The file to read.
        as_index (bool): Set to True to return pandas.Index objects rather than List[int]

    Returns:
        list: One item for each dimension. If as_index is True, the items will be pandas.Index objects,
            otherwise they will be List[int]

    """
    with open_file(file, mode='rb') as file_handler:
        magic, version, dtype_index, ndim = np.fromfile(file_handler, np.uint32, count=4)

        if magic != 0xC4D4F1B2 or version != 1 or not (0 < dtype_index <= 4) or not (0 < ndim <= 2):
            raise IOError("Unexpected file header: magic number: %X, version: %d, data type: %d, dimensions: %d."
                          % (magic, version, dtype_index, ndim))

        shape = np.fromfile(file_handler, np.uint32, count=ndim)

        index_list = []
        for n_items in shape:
            indices = np.fromfile(file_handler, np.int32, n_items)
            index_list.append(indices)

        if not as_index: return index_list

        return [pd.Index(zones) for zones in index_list]

# endregion
# region Raw INRO binary matrix (EMX) format


def read_emx(file, zones=None, tall=False):
    """
    Reads an "internal" Emme matrix (found in <Emme Project>/Database/emmemat); with an '.emx' extension. This data
    format does not contain information about zones. Its size is determined by the dimensions of the Emmebank
    (Emmebank.dimensions['centroids']), regardless of the number of zones actually used in all scenarios.

    Args:
        file (str or File or Path): The file to read.
        zones (Index or int or None): An Index or Iterable will be interpreted as the zone labels for the matrix rows
            and columns; returning a DataFrame or Series (depending on `tall`). If an integer is provided, the returned
            ndarray will be truncated to this 'number of zones'. Otherwise, the returned ndarray will be size to the
            maximum number of zone dimensioned by the Emmebank.
        tall (bool):  If True, a 1D data structure will be returned. If `zone_index` is provided, a Series will be
            returned, otherwise a 1D ndarray.

    Returns:
        DataFrame or Series or ndarray.

    Examples:
        For a project with 20 zones:

        matrix = from_emx("Database/emmemat/mf1.emx")
        print type(matrix), matrix.shape
        >> (numpy.ndarray, (20, 20))

        matrix = from_emx("Database/emmemat/mf1.emx", zones=10)
        print type(matrix), matrix.shape
        >> (numpy.ndarray, (10, 10))

        matrix = from_emx("Database/emmemat/mf1.emx", zones=range(10))
        print type(matrix), matrix.shape
        >> <class 'pandas.core.frame.DataFrame'> (10, 10)

        matrix = from_emx("Database/emmemat/mf1.emx", zones=range(10), tall=True)
        print type(matrix), matrix.shape
        >> <class 'pandas.core.series.Series'> 100

    """
    with open_file(file, mode='rb') as reader:
        data = np.fromfile(reader, dtype=np.float32)

        n = int(len(data) ** 0.5)
        assert len(data) == n ** 2

        if zones is None and tall:
            return data

        data.shape = n, n

        if isinstance(zones, (int, np.int_)):
            data = data[:zones, :zones]

            if tall:
                data.shape = zones * zones
                return data
            return data
        elif zones is None:
            return data

        zones = pd.Index(zones)
        n = len(zones)
        data = data[:n, :n]

        matrix = pd.DataFrame(data, index=zones, columns=zones)

        return matrix.stack() if tall else matrix


def to_emx(matrix, file, emmebank_zones):
    """
    Writes an "internal" Emme matrix (found in <Emme Project>/Database/emmemat); with an '.emx' extension. The number of
    zones that the Emmebank is dimensioned for must be known in order for the file to be written correctly.

    Args:
        matrix (DataFrame or Series or ndarray): The matrix to write to disk. If a Series is given, it MUST have a
            MultiIndex with exactly 2 levels to unstack.
        file (basestring or File): The path or file handler to write to.
        emmebank_zones (int): The number of zones the target Emmebank is dimensioned for.
    """
    assert emmebank_zones > 0

    with open_file(file, mode='wb') as writer:
        data = coerce_matrix(matrix)
        n = data.shape[0]
        if n > emmebank_zones:
            out = data[:emmebank_zones, :emmebank_zones].astype(np.float32)
        else:
            out = np.zeros([emmebank_zones, emmebank_zones], dtype=np.float32)
            out[:n, :n] = data

        out.tofile(writer)

# endregion
# region FORTRAN Optimized formats


def _infer_fortran_zones(n_words):
    """Returns the inverse of n_words = matrix_size * (matrix_size + 1)"""
    n = int(0.5 + ((1 + 4 * n_words)**0.5)/2) - 1
    assert n_words == (n * (n + 1)), "Could not infer a square matrix from file"
    return n


def read_fortran_rectangle(file, n_columns, zones=None, tall=False, reindex_rows=False, fill_value=None):
    """
    Reads a FORTRAN-friendly .bin file (a.k.a. 'simple binary format') which is known to NOT be square. Also works with
    square matrices.

    This file format is an array of 4-bytes, where each row is prefaced by an integer referring to the 1-based positional
    index that FORTRAN uses. The rest of the data are in 4-byte floats. To read this, the number of columns present
    must be known, since the format does not self-specify.

    Args:
        file(str or File or Path): The file to read.
        n_columns (int): The number of columns in the matrix.
        zones (None or int or pandas.Index): An Index or Iterable will be interpreted as the zone labels for the matrix
            rows and columns; returning a DataFrame or Series (depending on `tall`). If an integer is provided, the
            returned ndarray will be truncated to this 'number of zones'.
        tall (bool): If true, a 'tall' version of the matrix will be returned.
        reindex_rows (bool): If true, and zones is an Index, the returned DataFrame will be reindexed to fill-in any
            missing rows.
        fill_value: The value to pass to pandas.reindex()

    Returns:
        ndarray or DataFrame or Series

    Raises:
        AssertionError if the shape is not valid.
    """
    with open_file(file, mode='rb') as reader:
        n_columns = int(n_columns)

        matrix = np.fromfile(reader, dtype=np.float32)
        rows = len(matrix) // (n_columns + 1)
        assert len(matrix) == (rows * (n_columns + 1))

        matrix.shape = rows, n_columns + 1

        # Convert binary representation from float to int, then subtract 1 since FORTRAN uses 1-based positional
        # indexing
        row_index = np.frombuffer(matrix[:, 0].tobytes(), dtype=np.int32) - 1
        matrix = matrix[:, 1:]

        if zones is None:
            if tall:
                matrix.shape = matrix.shape[0] * matrix.shape[1]
            return matrix

        if isinstance(zones, (int, np.int_)):
            matrix = matrix[: zones, :zones]

            if tall:
                matrix.shape = zones * zones
            return matrix

        nzones = len(zones)
        matrix = matrix[: nzones, : nzones]
        row_labels = zones.take(row_index[:nzones])
        matrix = pd.DataFrame(matrix, index=row_labels, columns=zones)

        if reindex_rows:
            matrix = matrix.reindex_axis(zones, axis=0, fill_value=fill_value)

        if tall:
            return matrix.stack()
        return matrix


def read_fortran_square(file, zones=None, tall=False):
    """
    Reads a FORTRAN-friendly .bin file (a.k.a. 'simple binary format') which is known to be square.

    This file format is an array of 4-bytes, where each row is prefaced by an integer referring to the 1-based positional
    index that FORTRAN uses. The rest of the data are in 4-byte floats. To read this, the number of columns present
    must be known, since the format does not self-specify. This method can infer the shape if it is square.

    Args:
        file (str or File or Path): The file to read.
        zones (Index or int or None): An Index or Iterable will be interpreted as the zone labels for the matrix rows
            and columns; returning a DataFrame or Series (depending on `tall`). If an integer is provided, the returned
            ndarray will be truncated to this 'number of zones'. Otherwise, the returned ndarray will be size to the
            maximum number of zone dimensioned by the Emmebank.
        tall (bool):  If True, a 1D data structure will be returned. If `zone_index` is provided, a Series will be
            returned, otherwise a 1D ndarray.

    Returns:
        DataFrame or ndarray

    """
    with open_file(file, mode='rb') as reader:
        floats = np.fromfile(reader, dtype=np.float32)
        n_words = len(floats)
        matrix_size = _infer_fortran_zones(n_words)
        floats.shape = matrix_size, matrix_size + 1

        data = floats[:, 1:]

        if zones is None:
            if tall:
                n = np.prod(data.shape)
                data.shape = n
                return data
            return data

        if isinstance(zones, (int, np.int_)):
            data = data[:zones, :zones]

            if tall:
                data.shape = zones * zones
                return data
            return data
        elif zones is None:
            return data

        zones = pd.Index(zones)
        n = len(zones)
        data = data[:n, :n]

        matrix = pd.DataFrame(data, index=zones, columns=zones)

        return matrix.stack() if tall else matrix


def to_fortran(matrix, file, n_columns=None, min_index=1, force_square=True):
    """
    Reads a FORTRAN-friendly .bin file (a.k.a. 'simple binary format'), in a square format.

    Args:
        matrix (DataFrame or Series or ndarray): The matrix to write to disk. If a Series is given, it MUST have a
            MultiIndex with exactly 2 levels to unstack.
        file (basestring or File): The path or file handler to write to.
        n_columns (int): Optionally specify a desired "width" of the matrix file. For example, n_columns=4000 on a
            matrix 3500x3500 will pad the width with 500 extra columns containing 0. If None if provided or if the
            number of columns <= the width of the given matrix, no padding will be performed.
        min_index (int): The lowest numbered row. Used when slicing matrices
        forec_square (bool):

    """
    assert min_index >= 1
    array = coerce_matrix(matrix, force_square=force_square)

    if n_columns is not None and n_columns > array.shape[1]:
        extra_columns = n_columns - array.shape[1]
        array = expand_array(array, extra_columns, axis=1)

    with open_file(file, mode='wb') as writer:
        rows, columns = array.shape
        temp = np.zeros([rows, columns + 1], dtype=np.float32)
        temp[:, 1:] = array

        index = np.arange(min_index, rows + 1, dtype=np.int32)
        # Mask the integer binary representation as floating point
        index_as_float = np.frombuffer(index.tobytes(), dtype=np.float32)
        temp[:, 0] = index_as_float

        temp.tofile(writer)

# endregion

# region OMX Files

if omx is not None:

    def read_omx(file, matrices=None, mapping=None, raw=False, tall=False, squeeze=True):
        """
        Reads Open Matrix (OMX) files. An OMX file can contain multiple matrices, so this function
        typically returns a Dict.

        Args:
            file: OMX file from which to read. Cannot be an open file handler.
            matrices: List of matrices to read from the file. If None, all matrices will be read.
            mapping: The zone number mapping to use, if known in advance. If None, and the OMX file only contains
                one mapping, then that one is used. No mapping is read if raw is False.
            raw: If True, matrices will be returned as raw Numpy arrays. Otherwise, Pandas objects are returned
            tall: If True, matrices will be returned in 1D format (pd.Series if raw is False). Otherwise, a 2D object
                is returned.
            squeeze: If True, and the file contains exactly one matrix, return that matrix instead of a Dict.

        Returns:
            The matrix, or matrices contained in the OMX file.

        """
        file = str(file)
        with omx.open_file(file, mode='r') as omx_file:
            if mapping is None and not raw:
                all_mappings = omx_file.list_mappings()
                assert len(all_mappings) == 1
                mapping = all_mappings[0]

            if matrices is None:
                matrices = sorted(omx_file.list_matrices())
            else:
                matrices = sorted(matrices)

            if not raw:
                labels = pd.Index(omx_file.mapping(mapping).keys())
                if tall:
                    labels = pd.MultiIndex.from_product([labels, labels], names=['o', 'd'])

            return_value = {}
            for matrix_name in matrices:
                wrapper = omx_file[matrix_name]
                matrix = wrapper.read()

                if tall:
                    n = matrix.shape[0] * matrix.shape[1]
                    matrix.shape = n

                if not raw:
                    if tall: matrix = pd.Series(matrix, index=labels)
                    else: matrix = pd.DataFrame(matrix, index=labels, columns=labels)

                    matrix.name = matrix_name

                return_value[matrix_name] = matrix

            if len(matrices) == 1 and squeeze:
                return return_value[matrices[0]]
            return return_value


    def to_omx(file, matrices, zone_index=None, title='', descriptions=None, attrs=None, mapping='zone_numbers'):
        """
        Creates a new (or overwrites an old) OMX file with a collection of matrices.

        Args:
            file: OMX to write.
            matrices: Collection of matrices to write. MUST be a dict, to permit the encoding of matrix metadata,
                and must contain the same types: all Numpy arrays, all Series, or all DataFrames. Checking is done to
                ensure that all items have the same shape and labels.
            zone_index: Override zone labels to use. Generally only useful if writing a dict of raw Numpy arrays.
            title: The title saved in the OMX file.
            descriptions: A dict of descriptions (one for each given matrix), or None to not use.
            attrs: A dict of dicts (one for each given matrix), or None to not use
            mapping: Name of the mapping internal to the OMX file
        """

        matrices, zone_index = _prep_matrix_dict(matrices, zone_index)

        if descriptions is None:
            descriptions = {name: '' for name in iterkeys(matrices)}
        if attrs is None:
            attrs = {name: None for name in iterkeys(matrices)}

        file = str(file)  # Converts from Path
        with omx.open_file(file, mode='w', title=title) as omx_file:
            omx_file.create_mapping(mapping, zone_index.tolist())

            for name, array in iteritems(matrices):
                description = descriptions[name]
                attr = attrs[name]

                omx_file.create_matrix(name, obj=np.ascontiguousarray(array), title=description, attrs=attr)

        return

    def _prep_matrix_dict(matrices, desired_zone_index):
        collection_type = _check_types(matrices)

        if collection_type == 'RAW':
            checked, n = _check_raw_matrices(matrices)
            zone_index = pd.Index(range(n))
        elif collection_type == 'SERIES':
            checked, zone_index = _check_matrix_series(matrices)
        elif collection_type == 'FRAME':
            checked, zone_index = _check_matrix_frames(matrices)
        else:
            raise NotImplementedError(collection_type)

        if desired_zone_index is not None:
            assert desired_zone_index.equals(zone_index)

        return checked, zone_index

    def _check_types(matrices):
        gen = iter(itervalues(matrices))
        first = next(gen)

        item_type = 'RAW'
        if isinstance(first, pd.Series): item_type = 'SERIES'
        elif isinstance(first, pd.DataFrame): item_type = 'FRAME'

        msg = "All items must be the same type"

        for item in gen:
            if item_type == 'FRAME':
                assert isinstance(item, pd.DataFrame), msg
            elif item_type == 'SERIES':
                assert isinstance(item, pd.Series), msg
            else:
                assert isinstance(item, np.ndarray), msg

        return item_type

    def _check_raw_matrices(matrices):
        gen = iter(iteritems(matrices))
        name, matrix = next(gen)

        n_dim = len(matrix.shape)
        if n_dim == 1:
            shape = matrix.shape[0]
            n = int(shape ** 0.5)
            assert n * n == shape, "Only tall matrices that decompose to square shapes are permitted."
            matrix = matrix[...]
            matrix.shape = n, n
        elif n_dim == 2:
            n, cols = matrix.shape
            assert n == cols, "Only square matrices are permitted"
        else:
            raise TypeError("Only 1D and 2D arrays can be saved to OMX files")

        retval = {name: matrix}
        for name, matrix in gen:
            assert len(matrix.shape) == n_dim

            if n_dim == 1:
                assert matrix.shape[0] == (n * n)
                matrix = matrix[...]
                matrix.shape = n, n
            else:
                assert matrix.shape == (n, n)

            retval[name] = matrix

        return retval, n

    def _check_matrix_series(matrices):
        gen = iter(iteritems(matrices))
        name, matrix = next(gen)

        tall_index = matrix.index
        assert tall_index.nlevels == 2
        matrix = matrix.unstack()
        zone_index = matrix.index
        assert zone_index.equals(matrix.columns)

        retval = {name: matrix.values}
        for name, matrix in gen:
            assert tall_index.equals(matrix.index)
            matrix = fast_unstack(matrix, zone_index, zone_index).values
            retval[name] = matrix
        return retval, zone_index

    def _check_matrix_frames(matrices):
        gen = iter(iteritems(matrices))
        name, matrix = next(gen)

        zone_index = matrix.index
        assert zone_index.equals(matrix.columns)

        retval = {name: matrix.values}
        for name, matrix in gen:
            assert zone_index.equals(matrix.index)
            assert zone_index.equals(matrix.columns)
            retval[name] = matrix.values
        return retval, zone_index

else:
    def read_omx(*args, **kwargs):
        raise NotImplementedError()

    def to_omx(*args, **kwargs):
        raise NotImplementedError()

# endregion


@contextmanager
def open_file(file_handle, **kwargs):
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
    if isinstance(file_handle, string_types):
        f = open(file_handle, **kwargs)
        opened = True
    elif Path is not None and isinstance(file_handle, Path):
        f = file_handle.open(**kwargs)
        opened = True
    else:
        f = file_handle

    try:
        yield f
    finally:
        if opened:
            f.close()