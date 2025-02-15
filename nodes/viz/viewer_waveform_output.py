# This file is part of project Sverchok. It's copyrighted by the contributors
# recorded in the version control history of the file, available from
# its original location https://github.com/nortikin/sverchok/commit/master
#
# SPDX-License-Identifier: GPL3
# License-Filename: LICENSE

import os
import numpy as np
import wave
import struct
import inspect

import bpy
import blf
import bgl
import gpu
from gpu_extras.batch import batch_for_shader

from mathutils import Vector

from sverchok.utils.sv_operator_mixins import SvGenericNodeLocator
from sverchok.node_tree import SverchCustomTreeNode
from sverchok.data_structure import updateNode, node_id
from sverchok.ui import bgl_callback_nodeview as nvBGL

DATA_SOCKET = 'SvStringsSocket'

def get_offset_xy(node):
    x, y = [int(j) for j in (Vector(node.absolute_location) + Vector((node.width + 20, 0)))[:]]
    return x, y


class gridshader():
    def __init__(self, dims, loc, palette, channels):
        x, y = (0, 0)
        w, h = dims

        if channels == 2:
            h *= 2

        h1 = h * (1 / 4)
        h2 = h / 2
        h3 = h * (3 / 4)

        hc = palette.high_colour
        lc = palette.low_colour

        if channels == 1:
            """
            0 - - - - - - 1  low color           0 = (x,     y)
            |  -          |                      1 = (x + W, y)
            |      -      |
            |          -  |
            2 + + + + + + 3  high color          2 = (x,     y - h2)
            |  -          |                      3 = (x + w, y - h2)
            |      -      |
            |          -  |
            4 - - - - - - 5  low color           4 = (x,     y - h)
                                                 5 = (x + w, y - h)
            """
            self.background_coords = [
                (x, y),      (x + w, y),
                (x, y - h2), (x + w, y - h2),
                (x, y - h),  (x + w, y - h)]
            self.background_indices = [(0, 1, 3), (0, 3, 2), (2, 3, 5), (2, 5, 4)]
            self.background_colors = [lc, lc, hc, hc, lc, lc]

        elif channels == 2:
            """
            0 - - - - - - 1  low color           0 = (x,     y)
            |  -          |                      1 = (x + W, y)
            |      -      |
            |          -  |
            2 - - - - - - 3  high color          2 = (x,     y - h1)
            |  -          |                      3 = (x + w, y - h1)
            |      -      |
            |          -  |
            4 + + + + + + 5  low color           4 = (x,     y - h2)
            |  -          |                      5 = (x + w, y - h2)
            |      -      |
            |          -  |
            6 - - - - - - 7  high color          6 = (x,     y - h3)
            |  -          |                      7 = (x + w, y - h3)
            |      -      |
            |          -  |
            8 - - - - - - 9  low color           8 = (x,     y - h)
                                                 9 = (x + w, y - h)

            """

            self.background_coords = [
                (x, y),      (x + w, y),
                (x, y - h1), (x + w, y - h1),
                (x, y - h2), (x + w, y - h2),
                (x, y - h3), (x + w, y - h3),
                (x, y - h),  (x + w, y - h)]

            self.background_indices = [
                (0, 1, 3), (0, 3, 2),
                (2, 3, 5), (2, 5, 4),
                (4, 5, 7), (4, 7, 6),
                (6, 7, 9), (6, 9, 8)]

            self.background_colors = [lc, lc, hc, hc, lc, lc, hc, hc, lc, lc]

def advanced_grid_xy(context, args, xy):
    x, y = xy
    geom, config = args
    matrix = gpu.matrix.get_projection_matrix()

    ## background
    config.background_shader.bind()
    config.background_shader.uniform_float("viewProjectionMatrix", matrix)
    config.background_shader.uniform_float("x_offset", x)
    config.background_shader.uniform_float("y_offset", y)
    config.background_batch.draw(config.background_shader)

    ## background grid / ticks
    if hasattr(config, 'tick_shader'):
        config.tick_shader.bind()
        config.tick_shader.uniform_float("viewProjectionMatrix", matrix)
        config.tick_shader.uniform_float("color", (0.4, 0.4, 0.9, 1))
        config.tick_shader.uniform_float("x_offset", x)
        config.tick_shader.uniform_float("y_offset", y)
        config.tick_batch.draw(config.tick_shader)

    ## line graph
    config.line_shader.bind()
    config.line_shader.uniform_float("viewProjectionMatrix", matrix)
    config.line_shader.uniform_float("color", (1, 0, 0, 1))
    config.line_shader.uniform_float("x_offset", x)
    config.line_shader.uniform_float("y_offset", y)
    config.line_batch.draw(config.line_shader)


class SvWaveformViewerOperator(bpy.types.Operator, SvGenericNodeLocator):
    bl_idname = "node.waveform_viewer_callback"
    bl_label = "Waveform Viewer Operator"

    fn_name: bpy.props.StringProperty(default='')

    def sv_execute(self, context, node):
        getattr(node, self.fn_name)()


class SvWaveformViewerOperatorDP(bpy.types.Operator, SvGenericNodeLocator):
    bl_idname = "node.waveform_viewer_dirpick"
    bl_label = "Waveform Viewer Directory Picker"

    filepath: bpy.props.StringProperty(
        name="File Path", description="Filepath used for writing waveform files",
        maxlen=1024, default="", subtype='FILE_PATH')

    def sv_execute(self, context, node):
        node.set_dir(self.filepath)

    def invoke(self, context, event):
        wm = context.window_manager
        wm.fileselect_add(self)
        return {'RUNNING_MODAL'}

# place here (out of node) to supress warnings during headless testing. i think.
def get_2d_uniform_color_shader():
    # return gpu.shader.from_builtin('2D_UNIFORM_COLOR')
    uniform_2d_vertex_shader = '''
    in vec2 pos;
    uniform mat4 viewProjectionMatrix;
    uniform float x_offset;
    uniform float y_offset;

    void main()
    {
       gl_Position = viewProjectionMatrix * vec4(pos.x + x_offset, pos.y + y_offset, 0.0f, 1.0f);
    }
    '''

    uniform_2d_fragment_shader = '''
    uniform vec4 color;
    out vec4 FragColor;

    void main()
    {
       FragColor = color;
    }
    '''
    return gpu.types.GPUShader(uniform_2d_vertex_shader, uniform_2d_fragment_shader)

def get_2d_smooth_color_shader():
    # return gpu.shader.from_builtin('2D_SMOOTH_COLOR')
    smooth_2d_vertex_shader = '''
    in vec2 pos;
    layout(location=1) in vec4 color;

    uniform mat4 viewProjectionMatrix;
    uniform float x_offset;
    uniform float y_offset;

    out vec4 a_color;

    void main()
    {
        gl_Position = viewProjectionMatrix * vec4(pos.x + x_offset, pos.y + y_offset, 0.0f, 1.0f);
        a_color = color;
    }
    '''

    smooth_2d_fragment_shader = '''
    in vec4 a_color;
    out vec4 FragColor;

    void main()
    {
        FragColor = a_color;
    }
    '''
    return gpu.types.GPUShader(smooth_2d_vertex_shader, smooth_2d_fragment_shader)

signed_digital_voltage_max = {
    8: 127,
    16: 32767,
    24: 256**3,
    32: 256**4
}

class SvWaveformViewer(bpy.types.Node, SverchCustomTreeNode):

    """
    Triggers: SvWaveformViewer
    Tooltip:

    """

    def extended_docstring(self, auto_print=False):
        text = inspect.cleandoc("""
        a dedicated node converting input streams into samples of type: (Wav, ..)

        https://en.wikipedia.org/wiki/Audio_bit_depth

        wav:
            bitness | range of valid values     | signed | sum
            --------+---------------------------+--------+
            8  bit  | 0 to 255                  | no     | 256
            8  bit  | -128 to 127               | yes    | 256
            16 bit  | -32768 to 32767           | yes    | 256**2
            24 bit  | -8388608 to 8388608       | yes    | 256**3
            32 bit  | -2147483648 to 2147483648 | yes    | 256**4
            64 bit  |       uhmm..              | yes    | 256**8

        sample rate is not locked.


        """)
        if auto_print:
            print(text)
        else:
            return text


    bl_idname = 'SvWaveformViewer'
    bl_label = 'SvWaveformViewer'
    bl_icon = 'FORCE_HARMONIC'

    n_id: bpy.props.StringProperty(default='')
    location_theta: bpy.props.FloatProperty(name="location theta")

    def update_socket_count(self, context):
        ... # if self.num_channels < MAX_SOCKETS

    def get_drawing_attributes(self):
        from sverchok.settings import get_params
        props = get_params({
            'render_scale': 1.0, 
            'render_location_xy_multiplier': 1.0})
        self.location_theta = props.render_location_xy_multiplier
        return props.render_scale


    activate: bpy.props.BoolProperty(name="show graph", update=updateNode)

    num_channels: bpy.props.IntProperty(
        name='num channels', default=1, min=1, max=2,
        description='num channels interleaved', update=updateNode)

    bitrates = [(k, k, '', i) for i, k in enumerate("8 16 24 32".split())]

    bits: bpy.props.EnumProperty(
        items=bitrates,
        description="standard bitrate options",
        default="16", update=updateNode
    )

    sample_rate: bpy.props.IntProperty(
        name="sample rate", min=8000, default=48000)

    auto_normalize: bpy.props.BoolProperty(
        name="normalize")

    colour_limits: bpy.props.BoolProperty(
        name="color limits")

    multi_channel_sockets: bpy.props.BoolProperty(
        name="multiplex", update=updateNode, description="sockets carry multiple layers of data")

    sample_data_length: bpy.props.IntProperty(min=0, name="sample data len")

    dirname: bpy.props.StringProperty()
    filename: bpy.props.StringProperty()

    # ui props
    display_sampler_config: bpy.props.BoolProperty(default=False, name="Display Sampler Config", update=updateNode)
    display_graph_config: bpy.props.BoolProperty(default=False, name="Display Graph Config", update=updateNode)

    graph_width: bpy.props.IntProperty(name='w', default=220, min=1, update=updateNode)
    graph_height: bpy.props.IntProperty(name='h', default=220, min=1, update=updateNode)

    def sv_init(self, context):
        self.inputs.new(DATA_SOCKET, 'channel 0')
        self.inputs.new(DATA_SOCKET, 'channel 1')
        self.id_data.update_gl_scale_info()

    def draw_buttons(self, context, layout):

        col = layout.column(align=True)

        col.prop(self, "display_sampler_config", toggle=True)
        if self.display_sampler_config:
            box1 = col.box()
            box_col = box1.column(align=True)

            box_col.prop(self, 'num_channels')
            box_col.prop(self, 'sample_rate')
            box_row1 = box_col.row()
            box_row1.prop(self, 'bits', expand=True)

            box_col.separator()
            row1 = box_col.row()
            row1.prop(self, 'auto_normalize', toggle=True)
            row1.prop(self, 'multi_channel_sockets', toggle=True)

            box_col.separator()
            box_col.prop(self, 'colour_limits')

            box_col.separator()
            row = box_col.row(align=True)
            row.prop(self, 'dirname', text='')
            cb = "node.waveform_viewer_dirpick"
            self.wrapper_tracked_ui_draw_op(row, cb, icon='FILE', text='')
            box_col.prop(self, "filename")

        if (self.filename and self.dirname):
            cb = "node.waveform_viewer_callback"
            op = self.wrapper_tracked_ui_draw_op(col, cb, icon='CURSOR', text='WRITE')
            op.fn_name = "process_wave"
            col.separator()

        graph_row_1 = col.row(align=True)
        graph_row_1.prop(self, "display_graph_config", toggle=True)
        graph_row_1.prop(self, 'activate', text='', icon='NORMALIZE_FCURVES')

        if self.display_graph_config:
            box2 = col.box()
            box_col2 = box2.column(align=True)

            row3 = box_col2.row()
            row3.prop(self, 'graph_width', text='W')
            row3.prop(self, 'graph_height', text='H')

    def generate_tick_data(self, wave_data, dims, loc):
        """
        The scope displays time-ticks based on a multiple of 512 frames

        | . . . . | . . . . | . . . . | . . . . | . . . .

        """

        tick_data = lambda: None
        tick_data.verts = []
        tick_data.indices = []
        w, h = dims
        x, y = (0, 0)

        if self.num_channels == 2:
            h *= 2

        th = h / 12
        h2 = 0.5 * h

        unit_data = np.array(wave_data)
        samples_per_channel = int(unit_data.size / 2) if (self.num_channels == 2) else unit_data.size
        time_data = np.linspace(0, w, samples_per_channel, endpoint=True)

        # time_data at this point will provide a tick for each sample (mono, or stereo..)
        if (samples_per_channel // 128) > 1:
            ticks = time_data[::128].copy()
            tick_x = ticks.repeat(2)
            Y1 = np.array([th, -th])
            tick_y = np.tile(Y1, int(tick_x.shape[0] /2))
            np_verts = np.vstack([tick_x, tick_y]).T + [x , y - h2]
            tick_data.verts = np_verts.tolist()

            indices = np.arange(np_verts.shape[0])
            tick_data.indices = indices.reshape((-1, 2)).tolist()

        return tick_data

    def generate_2d_drawing_data(self, wave_data, wave_params, dims, loc):
        """
        equip wave_data with time domain, and rescale and translate (offset)
        """

        voltage_max = signed_digital_voltage_max.get(int(self.bits))
        num_channels = wave_params[0]
        num_frames = wave_params[3]
        w, h = dims
        x, y = loc

        # set up a container
        data = lambda: None
        data.verts = None
        data.indices = None

        unit_data = np.array(wave_data)

        if num_channels == 2:
            """
            GL_LINES    indices = [0 2] [1 3] [2 4] [3 5] [4 6] [5 7]
                        wave_data = [A0, A1, B0, B1, C0, C1....]
                        time_data = [0.0, 0.0, 0.1, 0.1, 0.2, 0.2, 0.3, 0.4....)

            2 different waveforms come in, interwoven.
            - lines must be drawn between A0 B0, B0 C0, C0 D0,....
            - lines must be drawn between A1 B1, B1 C1, C1 D1,..
            - to draw 2d data, separated the interweave we add a second dimension to the array:
                (0.0, A0), (0.0, A1), (0.1, B0), (0.1, B1), (0.2, C0), (0.2, C1),.....


                    C0,
            A0.    .   'D0
               'B0'


            A1-.       -D1
                B1--C1'

            0.0 0.1 0.2 0.3


            """
            h *= 2
            y1 = -int(1 / 4 * h)
            y3 = -int(3 / 4 * h)
            h2 = -int(h/2)

            # [x] interweaved UNIT_DATA
            A1 = unit_data   # np.array([0, 0, 1, 1, 2, 2, 3, 3, 4, 4])

            # [x] rescale | depends on bitrate, and whether there is normalizing
            A1_AMPED = A1 * (h2 / voltage_max)

            # [x] offset (translate: LEFT=TOP, RIGHT=BOTTOM)
            B1 = np.array([y1, y3])
            OFFSET = np.tile(B1, int(A1.size / 2))
            unit_data = A1_AMPED + OFFSET

            samples_per_channel = unit_data.size

            # edges..
            ext = []
            gather = ext.extend
            _ = [gather(((d, d+2), (d+1, d+3))) for d in range(0, samples_per_channel-2, 2)]
            data.indices = ext

            time_data = np.linspace(0, w, samples_per_channel/2, endpoint=True).repeat(2)
            data.verts = (np.vstack([time_data, unit_data]).T + [x , y]).tolist()
        else:

            """
            GL_LINE_STRIP   no indices needed
            """

            h2 = -int(h/2)
            time_data = np.linspace(0, w, num_frames, endpoint=True)
            A1 = unit_data

            # [x] rescale
            A1_AMPED = A1 * (h2 / voltage_max)

            # [x] offset
            OFFSET = h2
            unit_data = A1_AMPED + OFFSET

            data.verts = (np.vstack([time_data, unit_data]).T + [x , y]).tolist()

        return data



    def process(self):
        n_id = node_id(self)
        nvBGL.callback_disable(n_id)

        if self.activate:

            if not self.inputs[0].other:
                return

            # parameter containers
            config = lambda: None
            geom = lambda: None
            palette = lambda: None

            palette.high_colour = (0.33, 0.33, 0.33, 1.0)
            palette.low_colour = (0.1, 0.1, 0.1, 1.0)

            scale = self.get_drawing_attributes()

            # some aliases
            w = self.graph_width
            h = self.graph_height
            dims = (w, h)
            loc = (0, 0)
            config.scale = scale

            grid_data = gridshader(dims, loc, palette, self.num_channels)

            # this is for drawing only.
            wave_data = self.get_wavedata(raw=False)
            wave_params = self.get_waveparams()
            wave_data_processed = self.generate_2d_drawing_data(wave_data, wave_params, dims, loc)
            scope_tick_data = self.generate_tick_data(wave_data, dims, loc)

            # GRAPH PART - Background
            config.background_shader = get_2d_smooth_color_shader()
            config.background_batch = batch_for_shader(
                config.background_shader, 'TRIS', {
                "pos": grid_data.background_coords,
                "color": grid_data.background_colors},
                indices=grid_data.background_indices
            )

            if scope_tick_data.verts:
                params, kw_params = (('LINES', {"pos": scope_tick_data.verts}), {"indices": scope_tick_data.indices})
                config.tick_shader = get_2d_uniform_color_shader()
                config.tick_batch = batch_for_shader(config.tick_shader, *params, **kw_params)

            # LINE PART
            coords = wave_data_processed.verts
            indices = wave_data_processed.indices
            config.line_shader = get_2d_uniform_color_shader()
            params, kw_params = (('LINES', {"pos": coords}), {"indices": indices}) if indices else (('LINE_STRIP', {"pos": coords}), {})
            config.line_batch = batch_for_shader(config.line_shader, *params, **kw_params)


            # -------------- final draw data accumulation and pass to callback ------------------

            draw_data = {
                'mode': 'custom_function_context',
                'tree_name': self.id_data.name[:],
                'node_name': self.name[:],
                'loc': get_offset_xy,
                'custom_function': advanced_grid_xy,
                'args': (geom, config)
            }

            nvBGL.callback_enable(self.n_id, draw_data)


    def sv_free(self):
        nvBGL.callback_disable(node_id(self))

    def sv_copy(self, node):
        self.n_id = ''

    def process_wave(self):
        print('process wave pressed')
        if not self.dirname and self.filename:
            return

        # could be cached. from process()
        wave_data = self.get_wavedata()
        wave_params = self.get_waveparams()

        filepath = os.path.join(self.dirname, self.filename)
        filetype = "wav"

        full_filepath_with_ext = f"{filepath}.{filetype}"
        with wave.open(full_filepath_with_ext, 'w') as write_wave:
            write_wave.setparams(wave_params)
            write_wave.writeframesraw(wave_data)

    def get_waveparams(self):
        # reference http://blog.acipo.com/wave-generation-in-python/
        # (nchannels, sampwidth, framerate, nframes, comptype, compname)

        # sampwidth
        # :    1 = 8bit,
        # :    2 = 16bit, (int values between +-32767)
        # :    4 = 32bit?
        num_frames = self.num_channels * self.sample_data_length
        bitrate = int(int(self.bits) / 8)
        return (self.num_channels, bitrate, self.sample_rate, num_frames, 'NONE', 'NONE')

    def get_wavedata(self, raw=True):
        """
        do they match? what convention to use to fill up one if needed..
        - copy opposite channel if channel data is short
        - repeat last channel value until data length matches longest

        yikes, this logic blows...
        """
        if self.multi_channel_sockets:
            data = self.inputs[0].sv_get()
            # print(f'multi channel socket = True, (num_channels, len(data)) == ({self.num_channels}, {len(data)})')
            data = self.interleave(*data) if (self.num_channels, len(data)) == (2, 2) else data[0]
        else:
            if self.num_channels == 2:
                data_left = self.inputs[0].sv_get()[0]
                data_right = self.inputs[1].sv_get()[0]
                len_left = len(data_left)
                len_right = len(data_right)
                # print(f'multi channel socket = False, num_channels = 2, (len(data_left), len(data_right)) == ({len_left}, {len_right})')
                data = self.interleave(data_left, data_right)
            elif self.num_channels == 1:
                data = self.inputs[0].sv_get()[0]
                # print(f'multi channel socket = False, num_channels = 1, (len(data),) == ({len(data)}, )')


        # at this point data is a single list.
        self.sample_data_length = len(data)
        # data = "".join((wave.struct.pack('h', int(d)) for d in data))
        if raw:
            data = b''.join(wave.struct.pack('<h', int(d)) for d in data)
        return data

    def interleave(self, data_left, data_right):
        # if type data is two numpy arrays:
        # ..
        # else
        flat_list = []
        flat_add = flat_list.extend
        _ = [flat_add((l, r)) for l, r in zip(data_left, data_right)]
        return flat_list

    def set_dir(self, dirname):
        self.dirname = dirname
        print(self.dirname, dirname)


classes = [SvWaveformViewer, SvWaveformViewerOperator, SvWaveformViewerOperatorDP]
register, unregister = bpy.utils.register_classes_factory(classes)
