# ##### BEGIN GPL LICENSE BLOCK #####
#
#  This program is free software; you can redistribute it and/or
#  modify it under the terms of the GNU General Public License
#  as published by the Free Software Foundation; either version 2
#  of the License, or (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with this program; if not, write to the Free Software Foundation,
#  Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301, USA.
#
# ##### END GPL LICENSE BLOCK #####

from itertools import accumulate

import bpy
from bpy.props import EnumProperty, IntProperty, BoolProperty

from sverchok.node_tree import SverchCustomTreeNode
from sverchok.data_structure import updateNode, SvSetSocketAnyType, SvGetSocketAnyType


node_item_list = [
    ("Set", lambda i: set(i)),
    ("Ordered Set",   ),
    ("Sequential Set",  ),
    ("Unique Consecutives",  ),
    ("Accumulating Sum", lambda a: list(accumulate(a))),
    ("Intersection", lambda a, b: set(a) & set(b)),
    ("Union", lambda a, b: set(a) | set(b)),
    ("Difference", lambda a, b: set(a) - set(b)),
    ("Symmetric Diff", lambda a, b: set(a) ^ set(b))
]

func_dict = {k: v for k, v in node_item_list}

class ListModifierNode(bpy.types.Node, SverchCustomTreeNode):
    ''' List Modifier'''
    bl_idname = 'ListModifierNode'
    bl_label = 'List Modifier'
    bl_icon = 'OUTLINER_OB_EMPTY'

    mode_items = [(name, name, "", idx) for idx, (name, _) in enumerate(node_item_list)

    func_ = EnumProperty(
        name="Modes",
        description="Mode Choices",
        default="AVR", items=mode_items,
        update=updateNode
    )

    listify = BoolProperty(
        default=True,
        description='Output lists or proper sets',
        )


    def draw_buttons(self, context, layout):
        layout.prop(self, "func_")

    def sv_init(self, context):
        self.inputs.new('StringsSocket', "Data1")
        self.inputs.new('StringsSocket', "Data2")        
        self.outputs.new('StringsSocket', "Result")

    def process(self):

        inputs = self.inputs
        outputs = self.outputs

        # no logic applied yet
        if outputs[0].is_linked:
            if inputs['Data2'].is_linked:
                data1 = inputs['Data1'].sv_get()
                data2 = inputs['Data2'].sv_get()
                func = func_dict[self.func_]
                out = [func(data)]
                outputs[0].sv_set([out])


def register():
    bpy.utils.register_class(ListModifierNode)


def unregister():
    bpy.utils.unregister_class(ListModifierNode)
