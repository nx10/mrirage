from typing import Tuple, Union, Optional, Iterable

import numpy as np

from .common import RAS_SPACE_LABELS
from .utils import eye_1d
from ..datacube import Datacube


def cuboid(shape: Iterable, dtype=np.float64) -> Tuple[np.ndarray, np.ndarray]:
    """
    Get vertices and edges of a cuboid defined by shape
    :param shape: Cuboid dimensions (x, y, z)
    :param dtype: Output dtype
    :return: (vertices, edge indices)
    """
    x, y, z = (i-1 for i in shape)
    return np.array([
        [0, 0, 0, 1],  # 0
        [x, 0, 0, 1],  # 1
        [0, y, 0, 1],  # 2
        [0, 0, z, 1],  # 3
        [x, y, 0, 1],  # 4
        [0, y, z, 1],  # 5
        [x, 0, z, 1],  # 6
        [x, y, z, 1]  # 7
    ], dtype=dtype).T, np.array([
        [0, 1],
        [0, 2],
        [0, 3],
        [1, 4],
        [1, 6],
        [2, 4],
        [2, 5],
        [3, 5],
        [3, 6],
        [4, 7],
        [5, 7],
        [6, 7],
    ], dtype=int)


def intersect_line_plane(
        line_origin: np.ndarray,
        line_dir: np.ndarray,
        plane_origin: np.ndarray,
        plane_normal: np.ndarray,
        epsilon=1e-6) -> Optional[np.ndarray]:
    nu = plane_normal.dot(line_dir)

    if abs(nu) < epsilon:
        return None

    w = line_origin - plane_origin
    si = -plane_normal.dot(w) / nu
    psi = w + si * line_dir + plane_origin

    return psi


def intersect_points_plane(
        point1: np.ndarray,
        point2: np.ndarray,
        plane_origin: np.ndarray,
        plane_normal: np.ndarray,
        epsilon=1e-6) -> Optional[np.ndarray]:
    direction = point2 - point1
    orig = point1

    inter = intersect_line_plane(
        line_origin=orig,
        line_dir=direction,
        plane_origin=plane_origin,
        plane_normal=plane_normal,
        epsilon=epsilon
    )

    if inter is None:
        return None

    between_dist = np.linalg.norm(direction)
    point1_dist = np.linalg.norm(inter - point1)
    point2_dist = np.linalg.norm(inter - point2)

    if (between_dist + epsilon) < (point1_dist + point2_dist):
        return None

    return inter


def intersect_poly(
        vertices: np.ndarray,
        edges: np.ndarray,
        plane_origin: np.ndarray,
        plane_normal: np.ndarray
) -> Optional[np.ndarray]:
    inters = []
    for e in edges:
        line = vertices[0:3, e]

        inter = intersect_points_plane(
            point1=line[:, 0],
            point2=line[:, 1],
            plane_origin=plane_origin,
            plane_normal=plane_normal)

        if inter is not None:
            inters.append(inter)

    re = np.array(inters, dtype=np.float64).T
    if re.shape[0] == 0:
        return None
    return np.vstack([re, np.ones(re.shape[1])])


def slice_image(  # pylint: disable=too-many-locals
        data: Datacube,
        axis: int,
        axis_offset: float,
        bounds: Optional[np.ndarray] = None,
        sampling_dims: Optional[Tuple] = None) \
        -> Optional[Tuple[np.ndarray, np.ndarray, np.ndarray]]:

    corners, edges = cuboid(data.image.shape)
    corners_trans = data.transform(corners)

    inters = intersect_poly(
        corners_trans, edges,
        plane_origin=eye_1d(3, axis, v=axis_offset, dtype=np.float64),
        plane_normal=eye_1d(3, axis, dtype=np.float64))

    # data cube does not intersect plane
    if inters is None or inters.shape[1] < 3:
        return None

    # select variable dimensions
    var_dims = eye_1d(4, 3, False, True, dtype=bool)
    var_dims[axis] = False

    var_inters = inters[var_dims]
    x_min, y_min = np.min(var_inters, axis=1)
    x_max, y_max = np.max(var_inters, axis=1)

    # constrain to bounds
    if bounds is not None:
        var_bounds = bounds[var_dims]
        x_min_bounds, y_min_bounds = np.min(var_bounds, axis=1)
        x_max_bounds, y_max_bounds = np.max(var_bounds, axis=1)

        x_min = max(x_min, x_min_bounds)
        y_min = max(y_min, y_min_bounds)
        x_max = min(x_max, x_max_bounds)
        y_max = min(y_max, y_max_bounds)

        # todo dont need to do stuff above
        x_min, y_min = np.min(var_bounds, axis=1)
        x_max, y_max = np.max(var_bounds, axis=1)

    # sampling rectangle
    rect = np.full((4, 4), fill_value=1, dtype=np.float64)
    rect[var_dims] = np.array([
        [x_min, x_max, x_max, x_min],
        [y_min, y_min, y_max, y_max]
    ])
    rect[axis] = axis_offset  # equals: inters[axis, 0]

    # find minimum needed resolution (by measuring rectangle sides in data-space)
    if sampling_dims is None:
        rect_trans = data.transform_inv(rect)
        w1 = np.linalg.norm(rect_trans[:, 1] - rect_trans[:, 0])
        w2 = np.linalg.norm(rect_trans[:, 3] - rect_trans[:, 2])
        h1 = np.linalg.norm(rect_trans[:, 2] - rect_trans[:, 1])
        h2 = np.linalg.norm(rect_trans[:, 0] - rect_trans[:, 3])

        w = max(w1, w2)
        h = max(h1, h2)
        wn = int(np.ceil(w)) + 1
        hn = int(np.ceil(h)) + 1
    else:
        hn, wn = sampling_dims

    # create sampling grid
    sample_grid = np.full((4, wn * hn), fill_value=1, dtype=np.float64)
    sample_grid[var_dims] = np.mgrid[
                            x_min:x_max:complex(wn),
                            y_min:y_max:complex(hn)
                            ].reshape(2, -1)
    sample_grid[axis] = axis_offset  # equals: inters[axis, 0]

    # transform sampling grid (and round for nearest neighbour TODO)
    sample_grid_trans = data.transform_inv(sample_grid).astype(int)

    # clip sampling grid TODO
    for i in range(3):
        sample_grid_trans[i] = sample_grid_trans[i].clip(0, data.image.shape[i] - 1)

    # raster 2D image
    x = sample_grid_trans[0:3].reshape((3, hn, wn), order='F')
    rastered = data.image[x[0], x[1], x[2]]

    # select axis labels
    labs = RAS_SPACE_LABELS[var_dims[0:3]]

    # Todo: convert rounded (for nearest neighbour) estimates
    #  back forth to get more accurate axis_lims
    axis_lims = rect[var_dims][:, (True, False, True, False)]

    return rastered, axis_lims, labs