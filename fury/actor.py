from __future__ import division, print_function, absolute_import

import numpy as np
import vtk
from vtk.util import numpy_support

import fury.shaders
from fury import layout
from fury.colormap import colormap_lookup_table, create_colormap, orient2rgb
from fury.utils import (lines_to_vtk_polydata, set_input, apply_affine,
                        numpy_to_vtk_points, numpy_to_vtk_colors,
                        set_polydata_vertices, set_polydata_triangles)
from fury.utils import numpy_to_vtk_matrix, shallow_copy


def slicer(data, affine=None, value_range=None, opacity=1.,
           lookup_colormap=None, interpolation='linear', picking_tol=0.025):
    """Cut 3D scalar or rgb volumes into 2D images.

    Parameters
    ----------
    data : array, shape (X, Y, Z) or (X, Y, Z, 3)
        A grayscale or rgb 4D volume as a numpy array. If rgb then values
        expected on the range [0, 255].
    affine : array, shape (4, 4)
        Grid to space (usually RAS 1mm) transformation matrix. Default is None.
        If None then the identity matrix is used.
    value_range : None or tuple (2,)
        If None then the values will be interpolated from (data.min(),
        data.max()) to (0, 255). Otherwise from (value_range[0],
        value_range[1]) to (0, 255).
    opacity : float, optional
        Opacity of 0 means completely transparent and 1 completely visible.
    lookup_colormap : vtkLookupTable
        If None (default) then a grayscale map is created.
    interpolation : string
        If 'linear' (default) then linear interpolation is used on the final
        texture mapping. If 'nearest' then nearest neighbor interpolation is
        used on the final texture mapping.
    picking_tol : float
        The tolerance for the vtkCellPicker, specified as a fraction of
        rendering window size.

    Returns
    -------
    image_actor : ImageActor
        An object that is capable of displaying different parts of the volume
        as slices. The key method of this object is ``display_extent`` where
        one can input grid coordinates and display the slice in space (or grid)
        coordinates as calculated by the affine parameter.

    """
    if value_range is None:
        value_range = (data.min(), data.max())

    if data.ndim != 3:
        if data.ndim == 4:
            if data.shape[3] != 3:
                raise ValueError('Only RGB 3D arrays are currently supported.')
            else:
                nb_components = 3
        else:
            raise ValueError('Only 3D arrays are currently supported.')
    else:
        nb_components = 1

    vol = data

    im = vtk.vtkImageData()
    I, J, K = vol.shape[:3]
    im.SetDimensions(I, J, K)
    # for now setting up for 1x1x1 but transformation comes later.
    voxsz = (1., 1., 1.)
    # im.SetOrigin(0,0,0)
    im.SetSpacing(voxsz[2], voxsz[0], voxsz[1])

    vtk_type = numpy_support.get_vtk_array_type(vol.dtype)
    im.AllocateScalars(vtk_type, nb_components)
    # im.AllocateScalars(vtk.VTK_UNSIGNED_CHAR, nb_components)

    # copy data
    # what I do below is the same as what is
    # commented here but much faster
    # for index in ndindex(vol.shape):
    #     i, j, k = index
    #     im.SetScalarComponentFromFloat(i, j, k, 0, vol[i, j, k])
    vol = np.swapaxes(vol, 0, 2)
    vol = np.ascontiguousarray(vol)

    if nb_components == 1:
        vol = vol.ravel()
    else:
        vol = np.reshape(vol, [np.prod(vol.shape[:3]), vol.shape[3]])

    uchar_array = numpy_support.numpy_to_vtk(vol, deep=0)
    im.GetPointData().SetScalars(uchar_array)

    if affine is None:
        affine = np.eye(4)

    # Set the transform (identity if none given)
    transform = vtk.vtkTransform()
    transform_matrix = vtk.vtkMatrix4x4()
    transform_matrix.DeepCopy((
        affine[0][0], affine[0][1], affine[0][2], affine[0][3],
        affine[1][0], affine[1][1], affine[1][2], affine[1][3],
        affine[2][0], affine[2][1], affine[2][2], affine[2][3],
        affine[3][0], affine[3][1], affine[3][2], affine[3][3]))
    transform.SetMatrix(transform_matrix)
    transform.Inverse()

    # Set the reslicing
    image_resliced = vtk.vtkImageReslice()
    set_input(image_resliced, im)
    image_resliced.SetResliceTransform(transform)
    image_resliced.AutoCropOutputOn()

    # Adding this will allow to support anisotropic voxels
    # and also gives the opportunity to slice per voxel coordinates
    RZS = affine[:3, :3]
    zooms = np.sqrt(np.sum(RZS * RZS, axis=0))
    image_resliced.SetOutputSpacing(*zooms)

    image_resliced.SetInterpolationModeToLinear()
    image_resliced.Update()

    vtk_resliced_data = image_resliced.GetOutput()

    ex1, ex2, ey1, ey2, ez1, ez2 = vtk_resliced_data.GetExtent()

    resliced = numpy_support.vtk_to_numpy(
        vtk_resliced_data.GetPointData().GetScalars())

    # swap axes here
    if data.ndim == 4:
        if data.shape[-1] == 3:
            resliced = resliced.reshape(ez2 + 1, ey2 + 1, ex2 + 1, 3)
    if data.ndim == 3:
        resliced = resliced.reshape(ez2 + 1, ey2 + 1, ex2 + 1)

    class ImageActor(vtk.vtkImageActor):
        def __init__(self):
            self.picker = vtk.vtkCellPicker()
            self.output = None
            self.shape = None
            self.outline_actor = None

        def input_connection(self, output):

            # outline only
            outline = vtk.vtkOutlineFilter()
            outline.SetInputData(vtk_resliced_data)
            outline_mapper = vtk.vtkPolyDataMapper()
            outline_mapper.SetInputConnection(outline.GetOutputPort())
            self.outline_actor = vtk.vtkActor()
            self.outline_actor.SetMapper(outline_mapper)
            self.outline_actor.GetProperty().SetColor(1, 0.5, 0)
            self.outline_actor.GetProperty().SetLineWidth(5)
            self.outline_actor.GetProperty().SetRenderLinesAsTubes(True)
            # crucial
            self.GetMapper().SetInputConnection(output.GetOutputPort())
            self.output = output
            self.shape = (ex2 + 1, ey2 + 1, ez2 + 1)

        def display_extent(self, x1, x2, y1, y2, z1, z2):
            self.SetDisplayExtent(x1, x2, y1, y2, z1, z2)
            self.Update()
            bounds = self.GetBounds()
            # xmin, xmax, ymin, ymax, zmin, zmax = bounds
            # line = np.array([[xmin, ymin, zmin]])
            # self.outline_actor = actor.line()

        def display(self, x=None, y=None, z=None):
            if x is None and y is None and z is None:
                self.display_extent(ex1, ex2, ey1, ey2, ez2//2, ez2//2)
            if x is not None:
                self.display_extent(x, x, ey1, ey2, ez1, ez2)
            if y is not None:
                self.display_extent(ex1, ex2, y, y, ez1, ez2)
            if z is not None:
                self.display_extent(ex1, ex2, ey1, ey2, z, z)

        def resliced_array(self):
            """ Returns resliced array as numpy array"""
            resliced = numpy_support.vtk_to_numpy(
                vtk_resliced_data.GetPointData().GetScalars())

            # swap axes here
            if data.ndim == 4:
                if data.shape[-1] == 3:
                    resliced = resliced.reshape(ez2 + 1, ey2 + 1, ex2 + 1, 3)
            if data.ndim == 3:
                resliced = resliced.reshape(ez2 + 1, ey2 + 1, ex2 + 1)
            resliced = np.swapaxes(resliced, 0, 2)
            resliced = np.ascontiguousarray(resliced)
            return resliced

        def opacity(self, value):
            self.GetProperty().SetOpacity(value)

        def tolerance(self, value):
            self.picker.SetTolerance(value)

        def copy(self):
            im_actor = ImageActor()
            im_actor.input_connection(self.output)
            im_actor.SetDisplayExtent(*self.GetDisplayExtent())
            im_actor.opacity(self.GetOpacity())
            im_actor.tolerance(self.picker.GetTolerance())
            if interpolation == 'nearest':
                im_actor.SetInterpolate(False)
            else:
                im_actor.SetInterpolate(True)
                im_actor.GetMapper().BorderOn()
            return im_actor

        def shallow_copy(self):
            # TODO rename copy to shallow_copy
            self.copy()

    r1, r2 = value_range

    image_actor = ImageActor()
    if nb_components == 1:
        lut = lookup_colormap
        if lookup_colormap is None:
            # Create a black/white lookup table.
            lut = colormap_lookup_table((r1, r2), (0, 0), (0, 0), (0, 1))

        plane_colors = vtk.vtkImageMapToColors()
        plane_colors.SetLookupTable(lut)
        plane_colors.SetInputConnection(image_resliced.GetOutputPort())
        plane_colors.Update()
        image_actor.input_connection(plane_colors)
    else:
        image_actor.input_connection(image_resliced)
    image_actor.display()
    image_actor.opacity(opacity)
    image_actor.tolerance(picking_tol)

    if interpolation == 'nearest':
        image_actor.SetInterpolate(False)
    else:
        image_actor.SetInterpolate(True)

    image_actor.GetMapper().BorderOn()

    return image_actor


def surface(vertices, faces=None, colors=None, smooth=None, subdivision=3):
    """Generates a surface actor from an array of vertices
        The color and smoothness of the surface can be customized by specifying
        the type of subdivision algorithm and the number of subdivisions.

        Parameters
        ----------
        vertices : array, shape (X, Y, Z)
            The point cloud defining the surface.
        faces : array
            An array of precomputed triangulation for the point cloud.
            It is an optional parameter, it is computed locally if None
        colors : (N, 3) array
            Specifies the colors associated with each vertex in the
            vertices array.
            Optional parameter, if not passed, all vertices
            are colored white
        smooth : string - "loop" or "butterfly"
            Defines the type of subdivision to be used
            for smoothing the surface
        subdivision : integer, default = 3
            Defines the number of subdivisions to do for
            each triangulation of the point cloud.
            The higher the value, smoother the surface
            but at the cost of higher computation

        Returns
        -------
        surface_actor : vtkActor
            A vtkActor visualizing the final surface
            computed from the point cloud is returned.

    """
    from scipy.spatial import Delaunay
    points = vtk.vtkPoints()
    points.SetData(numpy_support.numpy_to_vtk(vertices))
    triangle_poly_data = vtk.vtkPolyData()
    triangle_poly_data.SetPoints(points)

    if colors is not None:
        triangle_poly_data.GetPointData().\
            SetScalars(numpy_support.numpy_to_vtk(colors))

    if faces is None:
        tri = Delaunay(vertices[:, [0, 1]])
        faces = np.array(tri.simplices, dtype='i8')

    if faces.shape[1] == 3:
        triangles = np.empty((faces.shape[0], 4), dtype=np.int64)
        triangles[:, -3:] = faces
        triangles[:, 0] = 3
    else:
        triangles = faces

    if not triangles.flags['C_CONTIGUOUS'] or triangles.dtype != 'int64':
        triangles = np.ascontiguousarray(triangles, 'int64')

    cells = vtk.vtkCellArray()
    cells.SetCells(triangles.shape[0],
                   numpy_support.numpy_to_vtkIdTypeArray(triangles, deep=True))
    triangle_poly_data.SetPolys(cells)

    clean_poly_data = vtk.vtkCleanPolyData()
    clean_poly_data.SetInputData(triangle_poly_data)

    mapper = vtk.vtkPolyDataMapper()
    surface_actor = vtk.vtkActor()

    if smooth is None:
        mapper.SetInputData(triangle_poly_data)
        surface_actor.SetMapper(mapper)

    elif smooth == "loop":
        smooth_loop = vtk.vtkLoopSubdivisionFilter()
        smooth_loop.SetNumberOfSubdivisions(subdivision)
        smooth_loop.SetInputConnection(clean_poly_data.GetOutputPort())
        mapper.SetInputConnection(smooth_loop.GetOutputPort())
        surface_actor.SetMapper(mapper)

    elif smooth == "butterfly":
        smooth_butterfly = vtk.vtkButterflySubdivisionFilter()
        smooth_butterfly.SetNumberOfSubdivisions(subdivision)
        smooth_butterfly.SetInputConnection(clean_poly_data.GetOutputPort())
        mapper.SetInputConnection(smooth_butterfly.GetOutputPort())
        surface_actor.SetMapper(mapper)

    return surface_actor


def contour_from_roi(data, affine=None,
                     color=np.array([1, 0, 0]), opacity=1):
    """Generate surface actor from a binary ROI.

    The color and opacity of the surface can be customized.

    Parameters
    ----------
    data : array, shape (X, Y, Z)
        An ROI file that will be binarized and displayed.
    affine : array, shape (4, 4)
        Grid to space (usually RAS 1mm) transformation matrix. Default is None.
        If None then the identity matrix is used.
    color : (1, 3) ndarray
        RGB values in [0,1].
    opacity : float
        Opacity of surface between 0 and 1.

    Returns
    -------
    contour_assembly : vtkAssembly
        ROI surface object displayed in space
        coordinates as calculated by the affine parameter.

    """
    if data.ndim != 3:
        raise ValueError('Only 3D arrays are currently supported.')
    else:
        nb_components = 1

    data = (data > 0) * 1
    vol = np.interp(data, xp=[data.min(), data.max()], fp=[0, 255])
    vol = vol.astype('uint8')

    im = vtk.vtkImageData()
    di, dj, dk = vol.shape[:3]
    im.SetDimensions(di, dj, dk)
    voxsz = (1., 1., 1.)
    # im.SetOrigin(0,0,0)
    im.SetSpacing(voxsz[2], voxsz[0], voxsz[1])
    im.AllocateScalars(vtk.VTK_UNSIGNED_CHAR, nb_components)

    # copy data
    vol = np.swapaxes(vol, 0, 2)
    vol = np.ascontiguousarray(vol)

    if nb_components == 1:
        vol = vol.ravel()
    else:
        vol = np.reshape(vol, [np.prod(vol.shape[:3]), vol.shape[3]])

    uchar_array = numpy_support.numpy_to_vtk(vol, deep=0)
    im.GetPointData().SetScalars(uchar_array)

    if affine is None:
        affine = np.eye(4)

    # Set the transform (identity if none given)
    transform = vtk.vtkTransform()
    transform_matrix = vtk.vtkMatrix4x4()
    transform_matrix.DeepCopy((
        affine[0][0], affine[0][1], affine[0][2], affine[0][3],
        affine[1][0], affine[1][1], affine[1][2], affine[1][3],
        affine[2][0], affine[2][1], affine[2][2], affine[2][3],
        affine[3][0], affine[3][1], affine[3][2], affine[3][3]))
    transform.SetMatrix(transform_matrix)
    transform.Inverse()

    # Set the reslicing
    image_resliced = vtk.vtkImageReslice()
    set_input(image_resliced, im)
    image_resliced.SetResliceTransform(transform)
    image_resliced.AutoCropOutputOn()

    # Adding this will allow to support anisotropic voxels
    # and also gives the opportunity to slice per voxel coordinates

    rzs = affine[:3, :3]
    zooms = np.sqrt(np.sum(rzs * rzs, axis=0))
    image_resliced.SetOutputSpacing(*zooms)

    image_resliced.SetInterpolationModeToLinear()
    image_resliced.Update()

    skin_extractor = vtk.vtkContourFilter()
    skin_extractor.SetInputData(image_resliced.GetOutput())

    skin_extractor.SetValue(0, 1)

    skin_normals = vtk.vtkPolyDataNormals()
    skin_normals.SetInputConnection(skin_extractor.GetOutputPort())
    skin_normals.SetFeatureAngle(60.0)

    skin_mapper = vtk.vtkPolyDataMapper()
    skin_mapper.SetInputConnection(skin_normals.GetOutputPort())
    skin_mapper.ScalarVisibilityOff()

    skin_actor = vtk.vtkActor()

    skin_actor.SetMapper(skin_mapper)
    skin_actor.GetProperty().SetOpacity(opacity)

    skin_actor.GetProperty().SetColor(color[0], color[1], color[2])

    return skin_actor


def streamtube(lines, colors=None, opacity=1, linewidth=0.1, tube_sides=9,
               lod=True, lod_points=10 ** 4, lod_points_size=3,
               spline_subdiv=None, lookup_colormap=None):
    """Use streamtubes to visualize polylines

    Parameters
    ----------
    lines : list
        list of N curves represented as 2D ndarrays

    colors : array (N, 3), list of arrays, tuple (3,), array (K,), None
        If None then a standard orientation colormap is used for every line.
        If one tuple of color is used. Then all streamlines will have the same
        colour.
        If an array (N, 3) is given, where N is equal to the number of lines.
        Then every line is coloured with a different RGB color.
        If a list of RGB arrays is given then every point of every line takes
        a different color.
        If an array (K, ) is given, where K is the number of points of all
        lines then these are considered as the values to be used by the
        colormap.
        If an array (L, ) is given, where L is the number of streamlines then
        these are considered as the values to be used by the colormap per
        streamline.
        If an array (X, Y, Z) or (X, Y, Z, 3) is given then the values for the
        colormap are interpolated automatically using trilinear interpolation.

    opacity : float
        Takes values from 0 (fully transparent) to 1 (opaque). Default is 1.
    linewidth : float
        Default is 0.01.
    tube_sides : int
        Default is 9.
    lod : bool
        Use vtkLODActor(level of detail) rather than vtkActor. Default is True.
        Level of detail actors do not render the full geometry when the
        frame rate is low.
    lod_points : int
        Number of points to be used when LOD is in effect. Default is 10000.
    lod_points_size : int
        Size of points when lod is in effect. Default is 3.
    spline_subdiv : int
        Number of splines subdivision to smooth streamtubes. Default is None.
    lookup_colormap : vtkLookupTable
        Add a default lookup table to the colormap. Default is None which calls
        :func:`fury.actor.colormap_lookup_table`.

    Examples
    --------
    >>> import numpy as np
    >>> from fury import actor, window
    >>> scene = window.Scene()
    >>> lines = [np.random.rand(10, 3), np.random.rand(20, 3)]
    >>> colors = np.random.rand(2, 3)
    >>> c = actor.streamtube(lines, colors)
    >>> scene.add(c)
    >>> #window.show(scene)

    Notes
    -----
    Streamtubes can be heavy on GPU when loading many streamlines and
    therefore, you may experience slow rendering time depending on system GPU.
    A solution to this problem is to reduce the number of points in each
    streamline. In Dipy we provide an algorithm that will reduce the number of
    points on the straighter parts of the streamline but keep more points on
    the curvier parts. This can be used in the following way::

        from dipy.tracking.distances import approx_polygon_track
        lines = [approx_polygon_track(line, 0.2) for line in lines]

    Alternatively we suggest using the ``line`` actor which is much more
    efficient.

    See Also
    --------
    :func:`fury.actor.line`

    """
    # Poly data with lines and colors
    poly_data, is_colormap = lines_to_vtk_polydata(lines, colors)
    next_input = poly_data

    # Set Normals
    poly_normals = set_input(vtk.vtkPolyDataNormals(), next_input)
    poly_normals.ComputeCellNormalsOn()
    poly_normals.ComputePointNormalsOn()
    poly_normals.ConsistencyOn()
    poly_normals.AutoOrientNormalsOn()
    poly_normals.Update()
    next_input = poly_normals.GetOutputPort()

    # Spline interpolation
    if (spline_subdiv is not None) and (spline_subdiv > 0):
        spline_filter = set_input(vtk.vtkSplineFilter(), next_input)
        spline_filter.SetSubdivideToSpecified()
        spline_filter.SetNumberOfSubdivisions(spline_subdiv)
        spline_filter.Update()
        next_input = spline_filter.GetOutputPort()

    # Add thickness to the resulting lines
    tube_filter = set_input(vtk.vtkTubeFilter(), next_input)
    tube_filter.SetNumberOfSides(tube_sides)
    tube_filter.SetRadius(linewidth)
    # TODO using the line above we will be able to visualize
    # streamtubes of varying radius
    # tube_filter.SetVaryRadiusToVaryRadiusByScalar()
    tube_filter.CappingOn()
    tube_filter.Update()
    next_input = tube_filter.GetOutputPort()

    # Poly mapper
    poly_mapper = set_input(vtk.vtkPolyDataMapper(), next_input)
    poly_mapper.ScalarVisibilityOn()
    poly_mapper.SetScalarModeToUsePointFieldData()
    poly_mapper.SelectColorArray("Colors")
    poly_mapper.Update()

    # Color Scale with a lookup table
    if is_colormap:
        if lookup_colormap is None:
            lookup_colormap = colormap_lookup_table()
        poly_mapper.SetLookupTable(lookup_colormap)
        poly_mapper.UseLookupTableScalarRangeOn()
        poly_mapper.Update()

    # Set Actor
    if lod:
        actor = vtk.vtkLODActor()
        actor.SetNumberOfCloudPoints(lod_points)
        actor.GetProperty().SetPointSize(lod_points_size)
    else:
        actor = vtk.vtkActor()

    actor.SetMapper(poly_mapper)

    actor.GetProperty().SetInterpolationToPhong()
    actor.GetProperty().BackfaceCullingOn()
    actor.GetProperty().SetOpacity(opacity)

    return actor


def line(lines, colors=None, opacity=1, linewidth=1,
         spline_subdiv=None, lod=True, lod_points=10 ** 4, lod_points_size=3,
         lookup_colormap=None, depth_cue=False, fake_tube=False):
    """ Create an actor for one or more lines.

    Parameters
    ------------
    lines :  list of arrays

    colors : array (N, 3), list of arrays, tuple (3,), array (K,), None
        If None then a standard orientation colormap is used for every line.
        If one tuple of color is used. Then all streamlines will have the same
        colour.
        If an array (N, 3) is given, where N is equal to the number of lines.
        Then every line is coloured with a different RGB color.
        If a list of RGB arrays is given then every point of every line takes
        a different color.
        If an array (K, ) is given, where K is the number of points of all
        lines then these are considered as the values to be used by the
        colormap.
        If an array (L, ) is given, where L is the number of streamlines then
        these are considered as the values to be used by the colormap per
        streamline.
        If an array (X, Y, Z) or (X, Y, Z, 3) is given then the values for the
        colormap are interpolated automatically using trilinear interpolation.

    opacity : float, optional
        Takes values from 0 (fully transparent) to 1 (opaque). Default is 1.

    linewidth : float, optional
        Line thickness. Default is 1.
    spline_subdiv : int, optional
        Number of splines subdivision to smooth streamtubes. Default is None
        which means no subdivision.
    lod : bool
        Use vtkLODActor(level of detail) rather than vtkActor. Default is True.
        Level of detail actors do not render the full geometry when the
        frame rate is low.
    lod_points : int
        Number of points to be used when LOD is in effect. Default is 10000.
    lod_points_size : int
        Size of points when lod is in effect. Default is 3.
    lookup_colormap : bool, optional
        Add a default lookup table to the colormap. Default is None which calls
        :func:`fury.actor.colormap_lookup_table`.
    depth_cue : boolean
        Add a size depth cue so that lines shrink with distance to the camera.
        Works best with linewidth <= 1.
    fake_tube: boolean
        Add shading to lines to approximate the look of tubes.

    Returns
    ----------
    v : vtkActor or vtkLODActor object
        Line.

    Examples
    ----------
    >>> from fury import actor, window
    >>> scene = window.Scene()
    >>> lines = [np.random.rand(10, 3), np.random.rand(20, 3)]
    >>> colors = np.random.rand(2, 3)
    >>> c = actor.line(lines, colors)
    >>> scene.add(c)
    >>> #window.show(scene)
    """
    # Poly data with lines and colors
    poly_data, is_colormap = lines_to_vtk_polydata(lines, colors)
    next_input = poly_data

    # use spline interpolation
    if (spline_subdiv is not None) and (spline_subdiv > 0):
        spline_filter = set_input(vtk.vtkSplineFilter(), next_input)
        spline_filter.SetSubdivideToSpecified()
        spline_filter.SetNumberOfSubdivisions(spline_subdiv)
        spline_filter.Update()
        next_input = spline_filter.GetOutputPort()

    poly_mapper = set_input(vtk.vtkPolyDataMapper(), next_input)
    poly_mapper.ScalarVisibilityOn()
    poly_mapper.SetScalarModeToUsePointFieldData()
    poly_mapper.SelectColorArray("Colors")
    poly_mapper.Update()

    if depth_cue:
        poly_mapper.SetGeometryShaderCode(fury.shaders.load("line.geom"))

        @vtk.calldata_type(vtk.VTK_OBJECT)
        def vtkShaderCallback(_caller, _event, calldata=None):
            program = calldata
            if program is not None:
                program.SetUniformf("linewidth", linewidth)

        poly_mapper.AddObserver(vtk.vtkCommand.UpdateShaderEvent,
                                vtkShaderCallback)

    # Color Scale with a lookup table
    if is_colormap:

        if lookup_colormap is None:
            lookup_colormap = colormap_lookup_table()

        poly_mapper.SetLookupTable(lookup_colormap)
        poly_mapper.UseLookupTableScalarRangeOn()
        poly_mapper.Update()

    # Set Actor
    if lod:
        actor = vtk.vtkLODActor()
        actor.SetNumberOfCloudPoints(lod_points)
        actor.GetProperty().SetPointSize(lod_points_size)
    else:
        actor = vtk.vtkActor()

    actor.SetMapper(poly_mapper)
    actor.GetProperty().SetLineWidth(linewidth)
    actor.GetProperty().SetOpacity(opacity)

    if fake_tube:
        actor.GetProperty().SetRenderLinesAsTubes(True)

    return actor


def scalar_bar(lookup_table=None, title=" "):
    """ Default scalar bar actor for a given colormap (colorbar)

    Parameters
    ----------
    lookup_table : vtkLookupTable or None
        If None then ``colormap_lookup_table`` is called with default options.
    title : str

    Returns
    -------
    scalar_bar : vtkScalarBarActor

    See Also
    --------
    :func:`fury.actor.colormap_lookup_table`

    """
    lookup_table_copy = vtk.vtkLookupTable()
    if lookup_table is None:
        lookup_table = colormap_lookup_table()
    # Deepcopy the lookup_table because sometimes vtkPolyDataMapper deletes it
    lookup_table_copy.DeepCopy(lookup_table)
    scalar_bar = vtk.vtkScalarBarActor()
    scalar_bar.SetTitle(title)
    scalar_bar.SetLookupTable(lookup_table_copy)
    scalar_bar.SetNumberOfLabels(6)

    return scalar_bar


def _arrow(pos=(0, 0, 0), color=(1, 0, 0), scale=(1, 1, 1), opacity=1):
    """ Internal function for generating arrow actors.
    """
    arrow = vtk.vtkArrowSource()
    # arrow.SetTipLength(length)

    arrowm = vtk.vtkPolyDataMapper()
    arrowm.SetInputConnection(arrow.GetOutputPort())

    arrowa = vtk.vtkActor()
    arrowa.SetMapper(arrowm)

    arrowa.GetProperty().SetColor(color)
    arrowa.GetProperty().SetOpacity(opacity)
    arrowa.SetScale(scale)

    return arrowa


def axes(scale=(1, 1, 1), colorx=(1, 0, 0), colory=(0, 1, 0), colorz=(0, 0, 1),
         opacity=1):
    """ Create an actor with the coordinate's system axes where
    red = x, green = y, blue = z.

    Parameters
    ----------
    scale : tuple (3,)
        Axes size e.g. (100, 100, 100). Default is (1, 1, 1).
    colorx : tuple (3,)
        x-axis color. Default red (1, 0, 0).
    colory : tuple (3,)
        y-axis color. Default green (0, 1, 0).
    colorz : tuple (3,)
        z-axis color. Default blue (0, 0, 1).
    opacity : float, optional
        Takes values from 0 (fully transparent) to 1 (opaque). Default is 1.

    Returns
    -------
    vtkAssembly
    """

    arrowx = _arrow(color=colorx, scale=scale, opacity=opacity)
    arrowy = _arrow(color=colory, scale=scale, opacity=opacity)
    arrowz = _arrow(color=colorz, scale=scale, opacity=opacity)

    arrowy.RotateZ(90)
    arrowz.RotateY(-90)

    ass = vtk.vtkAssembly()
    ass.AddPart(arrowx)
    ass.AddPart(arrowy)
    ass.AddPart(arrowz)

    return ass


def odf_slicer(odfs, affine=None, mask=None, sphere=None, scale=2.2,
               norm=True, radial_scale=True, opacity=1.,
               colormap='plasma', global_cm=False):
    """ Slice spherical fields in native or world coordinates

    Parameters
    ----------
    odfs : ndarray
        4D array of spherical functions
    affine : array
        4x4 transformation array from native coordinates to world coordinates
    mask : ndarray
        3D mask
    sphere : Sphere
        a sphere
    scale : float
        Distance between spheres.
    norm : bool
        Normalize `sphere_values`.
    radial_scale : bool
        Scale sphere points according to odf values.
    opacity : float
        Takes values from 0 (fully transparent) to 1 (opaque). Default is 1.
    colormap : None or str
        If None then white color is used. Otherwise the name of colormap is
        given. Matplotlib colormaps are supported (e.g., 'inferno').
    global_cm : bool
        If True the colormap will be applied in all ODFs. If False
        it will be applied individually at each voxel (default False).

    Returns
    ---------
    actor : vtkActor
        Spheres
    """

    if mask is None:
        mask = np.ones(odfs.shape[:3], dtype=np.bool)
    else:
        mask = mask.astype(np.bool)

    szx, szy, szz = odfs.shape[:3]

    class OdfSlicerActor(vtk.vtkLODActor):
        def __init__(self):
            self.mapper = None

        def display_extent(self, x1, x2, y1, y2, z1, z2):
            tmp_mask = np.zeros(odfs.shape[:3], dtype=np.bool)
            tmp_mask[x1:x2 + 1, y1:y2 + 1, z1:z2 + 1] = True
            tmp_mask = np.bitwise_and(tmp_mask, mask)

            self.mapper = _odf_slicer_mapper(odfs=odfs,
                                             affine=affine,
                                             mask=tmp_mask,
                                             sphere=sphere,
                                             scale=scale,
                                             norm=norm,
                                             radial_scale=radial_scale,
                                             colormap=colormap,
                                             global_cm=global_cm)
            self.SetMapper(self.mapper)

        def display(self, x=None, y=None, z=None):
            if x is None and y is None and z is None:
                self.display_extent(0, szx - 1, 0, szy - 1,
                                    int(np.floor(szz/2)), int(np.floor(szz/2)))
            if x is not None:
                self.display_extent(x, x, 0, szy - 1, 0, szz - 1)
            if y is not None:
                self.display_extent(0, szx - 1, y, y, 0, szz - 1)
            if z is not None:
                self.display_extent(0, szx - 1, 0, szy - 1, z, z)

    odf_actor = OdfSlicerActor()
    odf_actor.display_extent(0, szx - 1, 0, szy - 1,
                             int(np.floor(szz/2)), int(np.floor(szz/2)))
    odf_actor.GetProperty().SetOpacity(opacity)

    return odf_actor


def _odf_slicer_mapper(odfs, affine=None, mask=None, sphere=None, scale=2.2,
                       norm=True, radial_scale=True, colormap='plasma',
                       global_cm=False):
    """ Helper function for slicing spherical fields

    Parameters
    ----------
    odfs : ndarray
        4D array of spherical functions
    affine : array
        4x4 transformation array from native coordinates to world coordinates
    mask : ndarray
        3D mask
    sphere : Sphere
        a sphere
    scale : float
        Distance between spheres.
    norm : bool
        Normalize `sphere_values`.
    radial_scale : bool
        Scale sphere points according to odf values.
    colormap : None or str
        If None then sphere vertices are used to compute orientation-based
        color. Otherwise the name of colormap is given. Matplotlib colormaps
        are supported (e.g., 'inferno').
    global_cm : bool
        If True the colormap will be applied in all ODFs. If False
        it will be applied individually at each voxel (default False).

    Returns
    ---------
    mapper : vtkPolyDataMapper
        Spheres mapper
    """
    if mask is None:
        mask = np.ones(odfs.shape[:3])

    ijk = np.ascontiguousarray(np.array(np.nonzero(mask)).T)

    if len(ijk) == 0:
        return None

    if affine is not None:
        ijk = np.ascontiguousarray(apply_affine(affine, ijk))

    faces = np.asarray(sphere.faces, dtype=int)
    vertices = sphere.vertices

    all_xyz = []
    all_faces = []
    all_ms = []
    for (k, center) in enumerate(ijk):

        m = odfs[tuple(center.astype(np.int))].copy()

        if norm:
            m /= np.abs(m).max()

        if radial_scale:
            xyz = vertices * m[:, None]
        else:
            xyz = vertices.copy()

        all_xyz.append(scale * xyz + center)
        all_faces.append(faces + k * xyz.shape[0])
        all_ms.append(m)

    all_xyz = np.ascontiguousarray(np.concatenate(all_xyz))
    all_xyz_vtk = numpy_support.numpy_to_vtk(all_xyz, deep=True)

    all_faces = np.concatenate(all_faces)
    all_faces = np.hstack((3 * np.ones((len(all_faces), 1)),
                           all_faces))
    ncells = len(all_faces)

    all_faces = np.ascontiguousarray(all_faces.ravel(), dtype='i8')
    all_faces_vtk = numpy_support.numpy_to_vtkIdTypeArray(all_faces,
                                                          deep=True)
    if global_cm:
        all_ms = np.ascontiguousarray(
            np.concatenate(all_ms), dtype='f4')

    points = vtk.vtkPoints()
    points.SetData(all_xyz_vtk)

    cells = vtk.vtkCellArray()
    cells.SetCells(ncells, all_faces_vtk)

    if global_cm:
        if colormap is None:
            raise IOError("if global_cm=True, colormap must be defined")
        else:
            cols = create_colormap(all_ms.ravel(), colormap)
    else:
        cols = np.zeros((ijk.shape[0],) + sphere.vertices.shape,
                        dtype='f4')
        for k in range(ijk.shape[0]):
            if colormap is not None:
                tmp = create_colormap(all_ms[k].ravel(), colormap)
            else:
                tmp = orient2rgb(sphere.vertices)
            cols[k] = tmp.copy()

        cols = np.ascontiguousarray(
            np.reshape(cols, (cols.shape[0] * cols.shape[1],
                       cols.shape[2])), dtype='f4')

    vtk_colors = numpy_support.numpy_to_vtk(
        np.asarray(255 * cols),
        deep=True,
        array_type=vtk.VTK_UNSIGNED_CHAR)

    vtk_colors.SetName("Colors")

    polydata = vtk.vtkPolyData()
    polydata.SetPoints(points)
    polydata.SetPolys(cells)

    polydata.GetPointData().SetScalars(vtk_colors)

    mapper = vtk.vtkPolyDataMapper()
    mapper.SetInputData(polydata)

    return mapper


def _makeNd(array, ndim):
    """
    Pads as many 1s at the beginning of array's shape as are need to give
    array ndim dimensions.
    """
    new_shape = (1,) * (ndim - array.ndim) + array.shape
    return array.reshape(new_shape)


def tensor_slicer(evals, evecs, affine=None, mask=None, sphere=None, scale=2.2,
                  norm=True, opacity=1., scalar_colors=None):
    """Slice many tensors as ellipsoids in native or world coordinates.

    Parameters
    ----------
    evals : (3,) or (X, 3) or (X, Y, 3) or (X, Y, Z, 3) ndarray
        eigenvalues
    evecs : (3, 3) or (X, 3, 3) or (X, Y, 3, 3) or (X, Y, Z, 3, 3) ndarray
        eigenvectors
    affine : array
        4x4 transformation array from native coordinates to world coordinates*
    mask : ndarray
        3D mask
    sphere : Sphere
        a sphere
    scale : float
        Distance between spheres.
    norm : bool
        Normalize `sphere_values`.
    opacity : float
        Takes values from 0 (fully transparent) to 1 (opaque). Default is 1.
    scalar_colors : (3,) or (X, 3) or (X, Y, 3) or (X, Y, Z, 3) ndarray
        RGB colors used to show the tensors
        Default None, color the ellipsoids using ``color_fa``

    Returns
    ---------
    actor : vtkActor
        Ellipsoid

    """
    if not evals.shape == evecs.shape[:-1]:
        raise RuntimeError(
            "Eigenvalues shape {} is incompatible with eigenvectors' {}."
            " Please provide eigenvalue and"
            " eigenvector arrays that have compatible dimensions."
            .format(evals.shape, evecs.shape))

    if mask is None:
        mask = np.ones(evals.shape[:3], dtype=np.bool)
    else:
        mask = mask.astype(np.bool)

    szx, szy, szz = evals.shape[:3]

    class TensorSlicerActor(vtk.vtkLODActor):
        def __init__(self):
            self.mapper = None

        def display_extent(self, x1, x2, y1, y2, z1, z2):
            tmp_mask = np.zeros(evals.shape[:3], dtype=np.bool)
            tmp_mask[x1:x2 + 1, y1:y2 + 1, z1:z2 + 1] = True
            tmp_mask = np.bitwise_and(tmp_mask, mask)

            self.mapper = _tensor_slicer_mapper(evals=evals,
                                                evecs=evecs,
                                                affine=affine,
                                                mask=tmp_mask,
                                                sphere=sphere,
                                                scale=scale,
                                                norm=norm,
                                                scalar_colors=scalar_colors)
            self.SetMapper(self.mapper)

        def display(self, x=None, y=None, z=None):
            if x is None and y is None and z is None:
                self.display_extent(0, szx - 1, 0, szy - 1,
                                    int(np.floor(szz/2)), int(np.floor(szz/2)))
            if x is not None:
                self.display_extent(x, x, 0, szy - 1, 0, szz - 1)
            if y is not None:
                self.display_extent(0, szx - 1, y, y, 0, szz - 1)
            if z is not None:
                self.display_extent(0, szx - 1, 0, szy - 1, z, z)

    tensor_actor = TensorSlicerActor()
    tensor_actor.display_extent(0, szx - 1, 0, szy - 1,
                                int(np.floor(szz/2)), int(np.floor(szz/2)))

    tensor_actor.GetProperty().SetOpacity(opacity)

    return tensor_actor


def _tensor_slicer_mapper(evals, evecs, affine=None, mask=None, sphere=None,
                          scale=2.2, norm=True, scalar_colors=None):
    """Helper function for slicing tensor fields

    Parameters
    ----------
    evals : (3,) or (X, 3) or (X, Y, 3) or (X, Y, Z, 3) ndarray
        eigenvalues
    evecs : (3, 3) or (X, 3, 3) or (X, Y, 3, 3) or (X, Y, Z, 3, 3) ndarray
        eigenvectors
    affine : array
        4x4 transformation array from native coordinates to world coordinates
    mask : ndarray
        3D mask
    sphere : Sphere
        a sphere
    scale : float
        Distance between spheres.
    norm : bool
        Normalize `sphere_values`.
    scalar_colors : (3,) or (X, 3) or (X, Y, 3) or (X, Y, Z, 3) ndarray
        RGB colors used to show the tensors
        Default None, color the ellipsoids using ``color_fa``

    Returns
    ---------
    mapper : vtkPolyDataMapper
        Ellipsoid mapper

    """
    if mask is None:
        mask = np.ones(evals.shape[:3])

    ijk = np.ascontiguousarray(np.array(np.nonzero(mask)).T)
    if len(ijk) == 0:
        return None

    if affine is not None:
        ijk = np.ascontiguousarray(apply_affine(affine, ijk))

    faces = np.asarray(sphere.faces, dtype=int)
    vertices = sphere.vertices

    if scalar_colors is None:
        from dipy.reconst.dti import color_fa, fractional_anisotropy
        cfa = color_fa(fractional_anisotropy(evals), evecs)
    else:
        cfa = _makeNd(scalar_colors, 4)

    cols = np.zeros((ijk.shape[0],) + sphere.vertices.shape,
                    dtype='f4')

    all_xyz = []
    all_faces = []
    for (k, center) in enumerate(ijk):
        ea = evals[tuple(center.astype(np.int))]
        if norm:
            ea /= ea.max()
        ea = np.diag(ea.copy())

        ev = evecs[tuple(center.astype(np.int))].copy()
        xyz = np.dot(ev, np.dot(ea, vertices.T))

        xyz = xyz.T
        all_xyz.append(scale * xyz + center)
        all_faces.append(faces + k * xyz.shape[0])

        cols[k, ...] = np.interp(cfa[tuple(center.astype(np.int))], [0, 1],
                                 [0, 255]).astype('ubyte')

    all_xyz = np.ascontiguousarray(np.concatenate(all_xyz))
    all_xyz_vtk = numpy_support.numpy_to_vtk(all_xyz, deep=True)

    all_faces = np.concatenate(all_faces)
    all_faces = np.hstack((3 * np.ones((len(all_faces), 1)),
                           all_faces))
    ncells = len(all_faces)

    all_faces = np.ascontiguousarray(all_faces.ravel(), dtype='i8')
    all_faces_vtk = numpy_support.numpy_to_vtkIdTypeArray(all_faces,
                                                          deep=True)

    points = vtk.vtkPoints()
    points.SetData(all_xyz_vtk)

    cells = vtk.vtkCellArray()
    cells.SetCells(ncells, all_faces_vtk)

    cols = np.ascontiguousarray(
        np.reshape(cols, (cols.shape[0] * cols.shape[1],
                   cols.shape[2])), dtype='f4')

    vtk_colors = numpy_support.numpy_to_vtk(
        cols,
        deep=True,
        array_type=vtk.VTK_UNSIGNED_CHAR)

    vtk_colors.SetName("Colors")

    polydata = vtk.vtkPolyData()
    polydata.SetPoints(points)
    polydata.SetPolys(cells)
    polydata.GetPointData().SetScalars(vtk_colors)

    mapper = vtk.vtkPolyDataMapper()
    mapper.SetInputData(polydata)

    return mapper


def peak_slicer(peaks_dirs, peaks_values=None, mask=None, affine=None,
                colors=(1, 0, 0), opacity=1., linewidth=1,
                lod=False, lod_points=10 ** 4, lod_points_size=3):
    """Visualize peak directions as given from ``peaks_from_model``.

    Parameters
    ----------
    peaks_dirs : ndarray
        Peak directions. The shape of the array can be (M, 3) or (X, M, 3) or
        (X, Y, M, 3) or (X, Y, Z, M, 3)
    peaks_values : ndarray
        Peak values. The shape of the array can be (M, ) or (X, M) or
        (X, Y, M) or (X, Y, Z, M)
    affine : array
        4x4 transformation array from native coordinates to world coordinates
    mask : ndarray
        3D mask
    colors : tuple or None
        Default red color. If None then every peak gets an orientation color
        in similarity to a DEC map.

    opacity : float, optional
        Takes values from 0 (fully transparent) to 1 (opaque)

    linewidth : float, optional
        Line thickness. Default is 1.

    lod : bool
        Use vtkLODActor(level of detail) rather than vtkActor.
        Default is False. Level of detail actors do not render the full
        geometry when the frame rate is low.
    lod_points : int
        Number of points to be used when LOD is in effect. Default is 10000.
    lod_points_size : int
        Size of points when lod is in effect. Default is 3.

    Returns
    -------
    vtkActor

    See Also
    --------
    fury.actor.odf_slicer

    """
    peaks_dirs = np.asarray(peaks_dirs)
    if peaks_dirs.ndim > 5:
        raise ValueError("Wrong shape")

    peaks_dirs = _makeNd(peaks_dirs, 5)
    if peaks_values is not None:
        peaks_values = _makeNd(peaks_values, 4)

    grid_shape = np.array(peaks_dirs.shape[:3])

    if mask is None:
        mask = np.ones(grid_shape).astype(np.bool)

    class PeakSlicerActor(vtk.vtkLODActor):
        def __init__(self):
            self.line = None

        def display_extent(self, x1, x2, y1, y2, z1, z2):

            tmp_mask = np.zeros(grid_shape, dtype=np.bool)
            tmp_mask[x1:x2 + 1, y1:y2 + 1, z1:z2 + 1] = True
            tmp_mask = np.bitwise_and(tmp_mask, mask)

            ijk = np.ascontiguousarray(np.array(np.nonzero(tmp_mask)).T)
            if len(ijk) == 0:
                self.SetMapper(None)
                return
            if affine is not None:
                ijk_trans = np.ascontiguousarray(apply_affine(affine, ijk))
            list_dirs = []
            for index, center in enumerate(ijk):
                # center = tuple(center)
                if affine is None:
                    xyz = center[:, None]
                else:
                    xyz = ijk_trans[index][:, None]
                xyz = xyz.T
                for i in range(peaks_dirs[tuple(center)].shape[-2]):

                    if peaks_values is not None:
                        pv = peaks_values[tuple(center)][i]
                    else:
                        pv = 1.
                    symm = np.vstack((-peaks_dirs[tuple(center)][i] * pv + xyz,
                                      peaks_dirs[tuple(center)][i] * pv + xyz))
                    list_dirs.append(symm)

            self.line = line(list_dirs, colors=colors,
                             opacity=opacity, linewidth=linewidth,
                             lod=lod, lod_points=lod_points,
                             lod_points_size=lod_points_size)

            self.SetProperty(self.line.GetProperty())
            self.SetMapper(self.line.GetMapper())

        def display(self, x=None, y=None, z=None):
            if x is None and y is None and z is None:
                self.display_extent(0, szx - 1, 0, szy - 1,
                                    int(np.floor(szz/2)), int(np.floor(szz/2)))
            if x is not None:
                self.display_extent(x, x, 0, szy - 1, 0, szz - 1)
            if y is not None:
                self.display_extent(0, szx - 1, y, y, 0, szz - 1)
            if z is not None:
                self.display_extent(0, szx - 1, 0, szy - 1, z, z)

    peak_actor = PeakSlicerActor()

    szx, szy, szz = grid_shape
    peak_actor.display_extent(0, szx - 1, 0, szy - 1,
                              int(np.floor(szz / 2)), int(np.floor(szz / 2)))

    return peak_actor


def dots(points, color=(1, 0, 0), opacity=1, dot_size=5):
    """Create one or more 3d points.

    Parameters
    ----------
    points : ndarray, (N, 3)
    color : tuple (3,)
    opacity : float, optional
        Takes values from 0 (fully transparent) to 1 (opaque)
    dot_size : int

    Returns
    --------
    vtkActor

    See Also
    ---------
    fury.actor.point

    """
    if points.ndim == 2:
        points_no = points.shape[0]
    else:
        points_no = 1

    polyVertexPoints = vtk.vtkPoints()
    polyVertexPoints.SetNumberOfPoints(points_no)
    aPolyVertex = vtk.vtkPolyVertex()
    aPolyVertex.GetPointIds().SetNumberOfIds(points_no)

    cnt = 0
    if points.ndim > 1:
        for point in points:
            polyVertexPoints.InsertPoint(cnt, point[0], point[1], point[2])
            aPolyVertex.GetPointIds().SetId(cnt, cnt)
            cnt += 1
    else:
        polyVertexPoints.InsertPoint(cnt, points[0], points[1], points[2])
        aPolyVertex.GetPointIds().SetId(cnt, cnt)
        cnt += 1

    aPolyVertexGrid = vtk.vtkUnstructuredGrid()
    aPolyVertexGrid.Allocate(1, 1)
    aPolyVertexGrid.InsertNextCell(aPolyVertex.GetCellType(),
                                   aPolyVertex.GetPointIds())

    aPolyVertexGrid.SetPoints(polyVertexPoints)
    aPolyVertexMapper = vtk.vtkDataSetMapper()
    aPolyVertexMapper.SetInputData(aPolyVertexGrid)
    aPolyVertexActor = vtk.vtkActor()
    aPolyVertexActor.SetMapper(aPolyVertexMapper)

    aPolyVertexActor.GetProperty().SetColor(color)
    aPolyVertexActor.GetProperty().SetOpacity(opacity)
    aPolyVertexActor.GetProperty().SetPointSize(dot_size)
    return aPolyVertexActor


def point(points, colors, _opacity=1., point_radius=0.1, theta=8, phi=8):
    """Visualize points as sphere glyphs

    Parameters
    ----------
    points : ndarray, shape (N, 3)
    colors : ndarray (N,3) or tuple (3,)
    point_radius : float
    theta : int
    phi : int
    opacity : float, optional
        Takes values from 0 (fully transparent) to 1 (opaque)

    Returns
    -------
    vtkActor

    Examples
    --------
    >>> from fury import window, actor
    >>> scene = window.Scene()
    >>> pts = np.random.rand(5, 3)
    >>> point_actor = actor.point(pts, window.colors.coral)
    >>> scene.add(point_actor)
    >>> # window.show(scene)

    """
    return sphere(centers=points, colors=colors, radii=point_radius,
                  theta=theta, phi=phi, vertices=None, faces=None)


def sphere(centers, colors, radii=1., theta=16, phi=16,
           vertices=None, faces=None):
    """Visualize one or many spheres with different colors and radii

    Parameters
    ----------
    centers : ndarray, shape (N, 3)
    colors : ndarray (N,3) or (N, 4) or tuple (3,) or tuple (4,)
        RGB or RGBA (for opacity) R, G, B and A should be at the range [0, 1]
    radii : float or ndarray, shape (N,)
    theta : int
    phi : int
    vertices : ndarray, shape (N, 3)
    faces : ndarray, shape (M, 3)
        If faces is None then a sphere is created based on theta and phi angles
        If not then a sphere is created with the provided vertices and faces.

    Returns
    -------
    vtkActor

    Examples
    --------
    >>> from fury import window, actor
    >>> scene = window.Scene()
    >>> centers = np.random.rand(5, 3)
    >>> sphere_actor = actor.sphere(centers, window.colors.coral)
    >>> scene.add(sphere_actor)
    >>> # window.show(scene)

    """
    if np.array(colors).ndim == 1:
        colors = np.tile(colors, (len(centers), 1))

    if isinstance(radii, (float, int)):
        radii = radii * np.ones(len(centers), dtype='f8')

    pts = numpy_to_vtk_points(np.ascontiguousarray(centers))
    cols = numpy_to_vtk_colors(255 * np.ascontiguousarray(colors))
    cols.SetName('colors')

    radii_fa = numpy_support.numpy_to_vtk(
        np.ascontiguousarray(radii.astype('f8')), deep=0)
    radii_fa.SetName('rad')

    polydata_centers = vtk.vtkPolyData()
    polydata_sphere = vtk.vtkPolyData()

    if faces is None:
        src = vtk.vtkSphereSource()
        src.SetRadius(1)
        src.SetThetaResolution(theta)
        src.SetPhiResolution(phi)

    else:

        set_polydata_vertices(polydata_sphere, vertices)
        set_polydata_triangles(polydata_sphere, faces)

    polydata_centers.SetPoints(pts)
    polydata_centers.GetPointData().AddArray(radii_fa)
    polydata_centers.GetPointData().SetActiveScalars('rad')
    polydata_centers.GetPointData().AddArray(cols)

    glyph = vtk.vtkGlyph3D()

    if faces is None:
        glyph.SetSourceConnection(src.GetOutputPort())
    else:
        glyph.SetSourceData(polydata_sphere)

    glyph.SetInputData(polydata_centers)
    glyph.Update()

    mapper = vtk.vtkPolyDataMapper()
    mapper.SetInputData(glyph.GetOutput())
    mapper.SetScalarModeToUsePointFieldData()

    mapper.SelectColorArray('colors')

    actor = vtk.vtkActor()
    actor.SetMapper(mapper)

    return actor


def cone(centers, directions, colors, heights=1., resolution=10,
         vertices=None, faces=None):
    """Visualize one or many cones with different colors and radii

    Parameters
    ----------
    centers : ndarray, shape (N, 3)
    directions : ndarray, shape (N, 3)
        The orientation vector of the cone.
    colors : ndarray (N,3) or (N, 4) or tuple (3,) or tuple (4,)
        RGB or RGBA (for opacity) R, G, B and A should be at the range [0, 1]
    heights : ndarray, shape (N)
        The height of the cone.
    resolution : int
        The resolution of the cone.
    vertices : ndarray, shape (N, 3)
    faces : ndarray, shape (M, 3)
        If faces is None then a cone is created based on directions, heights
        and resolution. If not then a cone is created with the provided
        vertices and faces.

    Returns
    -------
    vtkActor

    Examples
    --------
    >>> from fury import window, actor
    >>> scene = window.Scene()
    >>> centers = np.random.rand(5, 3)
    >>> directions = np.random.rand(5, 3)
    >>> heights = np.random.rand(5)
    >>> cone_actor = actor.cone(centers, directions, (1, 1, 1), heights)
    >>> scene.add(cone_actor)
    >>> # window.show(scene)

    """
    if np.array(colors).ndim == 1:
        colors = np.tile(colors, (len(centers), 1))

    pts = numpy_to_vtk_points(np.ascontiguousarray(centers))
    cols = numpy_to_vtk_colors(255 * np.ascontiguousarray(colors))
    cols.SetName('colors')
    if isinstance(heights, np.ndarray):
        heights_fa = numpy_support.numpy_to_vtk(np.asarray(heights),
                                                deep=True,
                                                array_type=vtk.VTK_DOUBLE)
        heights_fa.SetName('heights')
    directions_fa = numpy_support.numpy_to_vtk(np.asarray(directions),
                                               deep=True,
                                               array_type=vtk.VTK_DOUBLE)
    directions_fa.SetName('directions')

    polydata_centers = vtk.vtkPolyData()
    polydata_cone = vtk.vtkPolyData()

    if faces is None:
        src = vtk.vtkConeSource()
        src.SetResolution(resolution)
        if isinstance(heights, int):
            src.SetHeight(heights)
    else:
        set_polydata_vertices(polydata_cone, vertices)
        set_polydata_triangles(polydata_cone, faces)

    polydata_centers.SetPoints(pts)
    polydata_centers.GetPointData().AddArray(cols)
    polydata_centers.GetPointData().AddArray(directions_fa)
    polydata_centers.GetPointData().SetActiveVectors('directions')
    if isinstance(heights, np.ndarray):
        polydata_centers.GetPointData().AddArray(heights_fa)
        polydata_centers.GetPointData().SetActiveScalars('heights')

    glyph = vtk.vtkGlyph3D()
    if faces is None:
        glyph.SetSourceConnection(src.GetOutputPort())
    else:
        glyph.SetSourceData(polydata_cone)

    glyph.SetInputData(polydata_centers)
    glyph.SetOrient(True)
    glyph.SetScaleModeToScaleByScalar()
    glyph.SetVectorModeToUseVector()
    glyph.Update()

    mapper = vtk.vtkPolyDataMapper()
    mapper.SetInputData(glyph.GetOutput())
    mapper.SetScalarModeToUsePointFieldData()
    mapper.SelectColorArray('colors')

    actor = vtk.vtkActor()
    actor.SetMapper(mapper)

    return actor


def label(text='Origin', pos=(0, 0, 0), scale=(0.2, 0.2, 0.2),
          color=(1, 1, 1)):
    """Create a label actor.

    This actor will always face the camera

    Parameters
    ----------
    text : str
        Text for the label.
    pos : (3,) array_like, optional
        Left down position of the label.
    scale : (3,) array_like
        Changes the size of the label.
    color : (3,) array_like
        Label color as ``(r,g,b)`` tuple.

    Returns
    -------
    l : vtkActor object
        Label.

    Examples
    --------
    >>> from fury import window, actor
    >>> scene = window.Scene()
    >>> l = actor.label(text='Hello')
    >>> scene.add(l)
    >>> #window.show(scene)

    """
    atext = vtk.vtkVectorText()
    atext.SetText(text)

    textm = vtk.vtkPolyDataMapper()
    textm.SetInputConnection(atext.GetOutputPort())

    texta = vtk.vtkFollower()
    texta.SetMapper(textm)
    texta.SetScale(scale)

    texta.GetProperty().SetColor(color)
    texta.SetPosition(pos)

    return texta


def text_3d(text, position=(0, 0, 0), color=(1, 1, 1),
            font_size=12, font_family='Arial', justification='left',
            vertical_justification="bottom",
            bold=False, italic=False, shadow=False):
    """ Generate 2D text that lives in the 3D world

    Parameters
    ----------
    text : str
    position : tuple
    color : tuple
    font_size : int
    font_family : str
    justification : str
        Left, center or right (default left)
    vertical_justification : str
        Bottom, middle or top (default bottom)
    bold : bool
    italic : bool
    shadow : bool

    Returns
    -------
    textActor3D
    """

    class TextActor3D(vtk.vtkTextActor3D):
        def message(self, text):
            self.set_message(text)

        def set_message(self, text):
            self.SetInput(text)
            self._update_user_matrix()

        def get_message(self):
            return self.GetInput()

        def font_size(self, size):
            self.GetTextProperty().SetFontSize(24)
            text_actor.SetScale((1./24.*size,)*3)
            self._update_user_matrix()

        def font_family(self, _family='Arial'):
            self.GetTextProperty().SetFontFamilyToArial()
            # self._update_user_matrix()

        def justification(self, justification):
            tprop = self.GetTextProperty()
            if justification == 'left':
                tprop.SetJustificationToLeft()
            elif justification == 'center':
                tprop.SetJustificationToCentered()
            elif justification == 'right':
                tprop.SetJustificationToRight()
            else:
                raise ValueError("Unknown justification: '{}'"
                                 .format(justification))

            self._update_user_matrix()

        def vertical_justification(self, justification):
            tprop = self.GetTextProperty()
            if justification == 'top':
                tprop.SetVerticalJustificationToTop()
            elif justification == 'middle':
                tprop.SetVerticalJustificationToCentered()
            elif justification == 'bottom':
                tprop.SetVerticalJustificationToBottom()
            else:
                raise ValueError("Unknown vertical justification: '{}'"
                                 .format(justification))

            self._update_user_matrix()

        def font_style(self, bold=False, italic=False, shadow=False):
            tprop = self.GetTextProperty()
            if bold:
                tprop.BoldOn()
            else:
                tprop.BoldOff()
            if italic:
                tprop.ItalicOn()
            else:
                tprop.ItalicOff()
            if shadow:
                tprop.ShadowOn()
            else:
                tprop.ShadowOff()

            self._update_user_matrix()

        def color(self, color):
            self.GetTextProperty().SetColor(*color)

        def set_position(self, position):
            self.SetPosition(position)

        def get_position(self):
            return self.GetPosition()

        def _update_user_matrix(self):
            """ Text justification of vtkTextActor3D doesn't seem to be
            working, so we do it manually. Yeah!
            """
            user_matrix = np.eye(4)

            text_bounds = [0, 0, 0, 0]
            self.GetBoundingBox(text_bounds)

            tprop = self.GetTextProperty()
            if tprop.GetJustification() == vtk.VTK_TEXT_LEFT:
                user_matrix[:3, -1] += (-text_bounds[0], 0, 0)
            elif tprop.GetJustification() == vtk.VTK_TEXT_CENTERED:
                tm = -(text_bounds[0] + (text_bounds[1] - text_bounds[0]) / 2.)
                user_matrix[:3, -1] += (tm, 0, 0)
            elif tprop.GetJustification() == vtk.VTK_TEXT_RIGHT:
                user_matrix[:3, -1] += (-text_bounds[1], 0, 0)

            if tprop.GetVerticalJustification() == vtk.VTK_TEXT_BOTTOM:
                user_matrix[:3, -1] += (0, -text_bounds[2], 0)
            elif tprop.GetVerticalJustification() == vtk.VTK_TEXT_CENTERED:
                tm = -(text_bounds[2] + (text_bounds[3] - text_bounds[2]) / 2.)
                user_matrix[:3, -1] += (0, tm, 0)
            elif tprop.GetVerticalJustification() == vtk.VTK_TEXT_TOP:
                user_matrix[:3, -1] += (0, -text_bounds[3], 0)

            user_matrix[:3, -1] *= self.GetScale()
            self.SetUserMatrix(numpy_to_vtk_matrix(user_matrix))

    text_actor = TextActor3D()
    text_actor.message(text)
    text_actor.font_size(font_size)
    text_actor.set_position(position)
    text_actor.font_family(font_family)
    text_actor.font_style(bold, italic, shadow)
    text_actor.color(color)
    text_actor.justification(justification)
    text_actor.vertical_justification(vertical_justification)

    return text_actor


class Container(object):
    """ Provides functionalities for grouping multiple actors using a given
    layout.

    Attributes
    ----------
    anchor : 3-tuple of float
        Anchor of this container used when laying out items in a container.
        The anchor point is relative to the center of the container.
        Default: (0, 0, 0).

    padding : 6-tuple of float
        Padding around this container bounding box. The 6-tuple represents
        (pad_x_neg, pad_x_pos, pad_y_neg, pad_y_pos, pad_z_neg, pad_z_pos).
        Default: (0, 0, 0, 0, 0, 0)

    """
    def __init__(self, layout=layout.Layout()):
        """
        Parameters
        ----------
        layout : ``fury.layout.Layout`` object
            Items of this container will be arranged according to `layout`.
        """
        self.layout = layout
        self._items = []
        self._need_update = True
        self._position = np.zeros(3)
        self._visibility = True
        self.anchor = np.zeros(3)
        self.padding = np.zeros(6)

    @property
    def items(self):
        if self._need_update:
            self.update()

        return self._items

    def add(self, *items, **kwargs):
        """ Adds some items to this container.

        Parameters
        ----------
        items : `vtkProp3D` objects
            Items to add to this container.
        borrow : bool
            If True the items are added as-is, otherwise a shallow copy is
            made first. If you intend to reuse the items elsewhere you
            should set `borrow=False`. Default: True.
        """
        self._need_update = True

        for item in items:
            if not kwargs.get('borrow', True):
                item = shallow_copy(item)

            self._items.append(item)

    def clear(self):
        """ Clears all items of this container. """
        self._need_update = True
        del self._items[:]

    def update(self):
        """ Updates the position of the items of this container. """
        self.layout.apply(self._items)
        self._need_update = False

    def add_to_scene(self, ren):
        """ Adds the items of this container to a given renderer. """
        for item in self.items:
            if isinstance(item, Container):
                item.add_to_scene(ren)
            else:
                ren.add(item)

    def GetBounds(self):
        """ Get the bounds of the container. """
        bounds = np.zeros(6)    # x1, x2, y1, y2, z1, z2
        bounds[::2] = np.inf    # x1, y1, z1
        bounds[1::2] = -np.inf  # x2, y2, z2

        for item in self.items:
            item_bounds = item.GetBounds()
            bounds[::2] = np.minimum(bounds[::2], item_bounds[::2])
            bounds[1::2] = np.maximum(bounds[1::2], item_bounds[1::2])

        # Add padding, if any.
        bounds[::2] -= self.padding[::2]
        bounds[1::2] += self.padding[1::2]

        return tuple(bounds)

    def GetVisibility(self):
        return self._visibility

    def SetVisibility(self, visibility):
        self._visibility = visibility
        for item in self.items:
            item.SetVisibility(visibility)

    def GetPosition(self):
        return self._position

    def AddPosition(self, position):
        self._position += position
        for item in self.items:
            item.AddPosition(position)

    def SetPosition(self, position):
        self.AddPosition(np.array(position) - self._position)

    def GetCenter(self):
        """ Get the center of the bounding box. """
        x1, x2, y1, y2, z1, z2 = self.GetBounds()
        return ((x1+x2)/2., (y1+y2)/2., (z1+z2)/2.)

    def GetLength(self):
        """ Get the length of bounding box diagonal. """
        x1, x2, y1, y2, z1, z2 = self.GetBounds()
        width, height, depth = x2-x1, y2-y1, z2-z1
        return np.sqrt(np.sum([width**2, height**2, depth**2]))

    def NewInstance(self):
        return Container(layout=self.layout)

    def ShallowCopy(self, other):
        self._position = other._position.copy()
        self._anchor = other._anchor
        self.clear()
        self.add(*other._items, borrow=False)
        self.update()

    def __len__(self):
        return len(self._items)


def grid(actors, captions=None, caption_offset=(0, -100, 0), cell_padding=0,
         cell_shape="rect", aspect_ratio=16/9., dim=None):
    """ Creates a grid of actors that lies in the xy-plane.

    Parameters
    ----------
    actors : list of `vtkProp3D` objects
        Actors to be layout in a grid manner.
    captions : list of `vtkProp3D` objects or list of str
        Objects serving as captions (can be any `vtkProp3D` object, not
        necessarily text). There should be one caption per actor. By
        default, there are no captions.
    caption_offset : tuple of float (optional)
        Tells where to position the caption w.r.t. the center of its
        associated actor. Default: (0, -100, 0).
    cell_padding : tuple of 2 floats or float
        Each grid cell will be padded according to (pad_x, pad_y) i.e.
        horizontally and vertically. Padding is evenly distributed on each
        side of the cell. If a single float is provided then both pad_x and
        pad_y will have the same value.
    cell_shape : str
        Specifies the desired shape of every grid cell.
        'rect' ensures the cells are the tightest.
        'square' ensures the cells are as wide as high.
        'diagonal' ensures the content of the cells can be rotated without
        colliding with content of the neighboring cells.
    aspect_ratio : float
        Aspect ratio of the grid (width/height). Default: 16:9.
    dim : tuple of int
        Dimension (nb_rows, nb_cols) of the grid. If provided,
        `aspect_ratio` will be ignored.

    Returns
    -------
    ``fury.actor.Container`` object
        Object that represents the grid containing all the actors and
        captions, if any.
    """
    grid_layout = layout.GridLayout(cell_padding=cell_padding,
                                    cell_shape=cell_shape,
                                    aspect_ratio=aspect_ratio, dim=dim)
    grid = Container(layout=grid_layout)

    if captions is not None:
        actors_with_caption = []
        for actor, caption in zip(actors, captions):

            actor_center = np.array(actor.GetCenter())

            # Offset accordingly the caption w.r.t.
            # the center of the associated actor.
            if isinstance(caption, str):
                caption = text_3d(caption, justification='center')
            else:
                caption = shallow_copy(caption)
            caption.SetPosition(actor_center + caption_offset)

            actor_with_caption = Container()
            actor_with_caption.add(actor, caption)

            # We change the anchor of the container so
            # the actor will be centered in the
            # grid cell.
            actor_with_caption.anchor = actor_center - \
                actor_with_caption.GetCenter()
            actors_with_caption.append(actor_with_caption)

        actors = actors_with_caption

    grid.add(*actors)
    return grid
