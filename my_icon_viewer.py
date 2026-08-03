import bpy
from bpy.types import PropertyGroup, Operator, Panel
from bpy.props import StringProperty, BoolProperty, EnumProperty, FloatProperty

# =========================================================================
# Data / Categories / Keywords
# =========================================================================

def get_icon_symbol(name):
    """Get a Unicode symbol/emoji for a given Blender icon name using strict matching"""
    name_upper = name.upper()
    
    # EXACT MATCHES (Highest Priority) - Common icons
    exact_map = {
        'FILE_FOLDER': '📁', 'FILE': '📄', 'FILE_IMAGE': '🖼️', 'FILE_MOVIE': '🎬',
        'FILE_SOUND': '🔊', 'FILE_TEXT': '📝', 'FILE_BLEND': '🔶', 'FILE_BACKUP': '💾',
        'FOLDER_REDIRECT': '↪️', 'ERROR': '❌', 'CANCEL': '✖️', 'CHECKMARK': '✅',
        'QUIT': '🚪', 'URL': '🌐', 'RECOVER_LAST': '↩️', 'FCURVE': '📈',
        'ALIASED': '〰️', 'ANTIALIASED': '➖', 'BRUSH_DATA': '🖌️', 'IMAGE_DATA': '🖼️',
        'INDIRECT_ONLY_OFF': '👁️', 'INDIRECT_ONLY_ON': '👻', 'RESTRICT_VIEW_OFF': '👁️',
        'RESTRICT_VIEW_ON': '🙈', 'RESTRICT_SELECT_OFF': '👆', 'RESTRICT_SELECT_ON': '🚫',
        'RESTRICT_RENDER_OFF': '📷', 'RESTRICT_RENDER_ON': '📵', 'LOCKED': '🔒',
        'UNLOCKED': '🔓', 'PINNED': '📌', 'UNPINNED': '📍', 'HIDE_OFF': '👁️',
        'HIDE_ON': '🙈', 'LIGHT_POINT': '💡', 'LIGHT_SUN': '☀️', 'LIGHT_SPOT': '🔦',
        'LIGHT_HEMI': '⛅', 'LIGHT_AREA': '⬜', 'CUBE': '🧊', 'MESH_CUBE': '🧊',
        'SPHERE': '⚽', 'MESH_UVSPHERE': '🌐', 'MESH_ICOSPHERE': '🌐', 'MESH_CIRCLE': '⭕',
        'MESH_CYLINDER': '🛢️', 'MESH_CONE': '⚠️', 'MESH_GRID': '▦', 'MESH_MONKEY': '🐵',
        'MONKEY': '🐵', 'SURFACE_NCIRCLE': '⭕', 'SURFACE_NSURFACE': '🟦', 'CURVE_BEZCURVE': '〰️',
        'CURVE_NCURVE': '〰️', 'META_EMPTY': '◻️', 'META_PLANE': '⬜', 'EMPTY_DATA': '✝️',
        'ARMATURE_DATA': '🦴', 'BONE_DATA': '🦴', 'CAMERA_DATA': '📷', 'MATERIAL_DATA': '🎨',
        'TEXTURE_DATA': '🏁', 'ANIM_DATA': '🏃', 'WORLD_DATA': '🌍', 'SCENE_DATA': '🎬',
        'PARTICLE_DATA': '✨', 'MODIFIER': '🔧', 'MODIFIER_DATA': '🔧', 'SHADING_BBOX': '📦',
        'SHADING_WIRE': '🕸️', 'SHADING_SOLID': '⚪', 'SHADING_RENDERED': '🟣',
        'OVERLAY': '🔳', 'XRAY': '🦴', 'ZOOM_IN': '➕', 'ZOOM_OUT': '➖', 'ZOOM_ALL': '⇱',
        'ZOOM_SELECTED': '🎯', 'VIEW3D': '🧊', 'GRAPH': '📈', 'OUTLINER': '≣',
        'PROPERTIES': '🛠️', 'CONSOLE': '💻', 'INFO': 'ℹ️', 'PREFERENCES': '⚙️',
        'TEXT': '📝', 'HELP': '❓', 'GHOST_ENABLED': '👻', 'GHOST_DISABLED': '👻',
        'MOD_MIRROR': '↔️', 'MOD_ARRAY': '▦', 'MOD_BEVEL': '🧊', 'MOD_BOOLEAN': '✂️',
        'MOD_BUILD': '🏗️', 'MOD_DECIM': '📉', 'MOD_EXPLODE': '💥', 'MOD_FLUID': '💧',
        'MOD_MASK': '🎭', 'MOD_PARTICLE_INSTANCE': '✨', 'MOD_PARTICLE_SYSTEM': '✨',
        'MOD_SCREW': '🔩', 'MOD_SIMPLEDEFORM': '🌀', 'MOD_SOLIDIFY': '🧱', 'MOD_SUBSURF': '🔴',
        'MOD_WARP': '🌀', 'MOD_WAVE': '🌊', 'MOD_REMESH': '🔄', 'MOD_SKIN': '🧥',
        'MOD_TRIANGULATE': '📐', 'MOD_WIREFRAME': '🕸️', 'SNAP_ON': '🧲', 'SNAP_OFF': '🧲',
        'HOME': '🏠', 'DOCUMENTS': '📁', 'TEMP': '⏱️', 'SORTALPHA': '🔤', 'SORTBYEXT': '🏷️',
        'SORTSIZE': '⚖️', 'SORTTIME': '📅', 'LONGDISPLAY': '☰', 'SHORTDISPLAY': '▪️▪️',
        'GIZMO': '🧭', 'TRIA_RIGHT': '▶', 'TRIA_DOWN': '▼', 'TRIA_LEFT': '◀', 'TRIA_UP': '▲',
        'ARROW_LEFTRIGHT': '↔️', 'DISCLOSURE_TRI_DOWN': '▼', 'DISCLOSURE_TRI_RIGHT': '▶',
        'RADIOBUT_ON': '◉', 'RADIOBUT_OFF': '○', 'CHECKBOX_HLT': '☑️', 'CHECKBOX_DEHLT': '⬜',
        'PLAY': '▶️', 'PAUSE': '⏸️', 'REW': '⏪', 'FF': '⏩', 'REC': '🔴', 'STOP': '⏹️',
        'PREV_KEYFRAME': '⏮️', 'NEXT_KEYFRAME': '⏭️', 'KEYFRAME': '◆', 'KEYFRAME_HLT': '🔶',
        'HANDLE_ALIGNED': '—', 'HANDLE_AUTO': '—', 'HANDLE_FREE': '—', 'HANDLE_VECTOR': '—',
        'VIEW_CAMERA': '📷', 'VIEW_ORTHO': '📐', 'VIEW_PERSPECTIVE': '🏗️',
        'ALIGN_LEFT': '⇤', 'ALIGN_CENTER': '⇥⇤', 'ALIGN_RIGHT': '⇥',
        'ALIGN_TOP': '⤒', 'ALIGN_BOTTOM': '⤓', 'ALIGN_MIDDLE': '⤓⤒',
        'BOLD': '𝐁', 'ITALIC': '𝑰', 'UNDERLINE': 'U̲', 'SMALL_CAPS': 'ᴀʙᴄ',
        'COPYDOWN': '⬇️', 'PASTEDOWN': '📋', 'PASTEFLIP': '🔁',
        'VIS_SEL_11': '⬛', 'VIS_SEL_10': '⬜', 'VIS_SEL_01': '⬜', 'VIS_SEL_00': '⬜',
        'COLOR': '🎨', 'TRASH': '🗑️', 'PLUS': '➕', 'ADD': '➕', 'REMOVE': '➖',
        'X': '❌', 'MOUSE_LMB': '🖱️', 'MOUSE_MMB': '🖱️', 'MOUSE_RMB': '🖱️',
        'MOUSE_MOVE': '↔️', 'FUND': '💰', 'HEART': '❤️', 'COMMUNITY': '👥',
        'USER': '👤', 'SYSTEM': '🖥️', 'SETTINGS': '⚙️', 'TOOL_SETTINGS': '🛠️',
    }
    
    if name_upper in exact_map:
        return exact_map[name_upper]
    
    # PREFIX MATCHES
    if name_upper.startswith('FILE'): return '📄'
    if name_upper.startswith('FOLDER'): return '📁'
    if name_upper.startswith('DISK'): return '💾'
    if name_upper.startswith('MESH_'): return '🧊'
    if name_upper.startswith('CURVE_'): return '〰️'
    if name_upper.startswith('META_'): return '☁️'
    if name_upper.startswith('SURFACE_'): return '🟦'
    if name_upper.startswith('LATTICE'): return '⊞'
    if name_upper.startswith('MOD_'): return '🔧'
    if name_upper.startswith('CON_'): return '🔗'
    if name_upper.startswith('LIGHT_'): return '💡'
    if name_upper.startswith('VIEW_'): return '👁️'
    if name_upper.startswith('TRIA_'): return '▶'
    if name_upper.startswith('ARROW_'): return '➡️'
    if name_upper.startswith('ALIGN_'): return '📊'
    if name_upper.startswith('KEYFRAME'): return '◆'
    if name_upper.startswith('DRIVER'): return '🚗'
    if name_upper.startswith('ACTION'): return '🎬'
    if name_upper.startswith('GRAPH'): return '📈'
    if name_upper.startswith('PHYSICS'): return '⚛️'
    if name_upper.startswith('PARTICLE'): return '✨'
    if name_upper.startswith('FORCE_'): return '💨'
    if name_upper.startswith('FLUID'): return '💧'
    if name_upper.startswith('BRUSH_'): return '🖌️'
    if name_upper.startswith('SCULPT'): return '🗿'
    if name_upper.startswith('PAINT'): return '🎨'
    if name_upper.startswith('NODE_'): return '🔗'
    if name_upper.startswith('MAT_'): return '🎨'
    if name_upper.startswith('SHADING_'): return '🟣'
    
    return '🔹'


# Visual keyword mapping for semantic search
ICON_VISUAL_KEYWORDS = {
    'VIEW_PAN': ['hand', 'palm', 'grab', 'move', 'drag', 'pan'],
    'HAND': ['hand', 'palm', 'grab', 'hold', 'grasp'],
    'MOUSE_MOVE': ['hand', 'cursor', 'pointer', 'move'],
    'LIGHT_SUN': ['sun', 'rays', 'star', 'bright', 'sunlight', 'sunshine'],
    'LIGHT_POINT': ['bulb', 'lightbulb', 'lamp', 'point', 'light'],
    'LIGHT_SPOT': ['spotlight', 'cone', 'beam', 'flashlight'],
    'LIGHT_AREA': ['panel', 'square', 'area', 'soft'],
    'MESH_CUBE': ['box', 'square', 'cube', 'block'],
    'MESH_CIRCLE': ['ring', 'circle', 'round', 'loop'],
    'MESH_UVSPHERE': ['ball', 'sphere', 'globe', 'round'],
    'MESH_MONKEY': ['suzanne', 'monkey', 'head', 'face'],
    'MESH_CONE': ['cone', 'triangle', 'pyramid'],
    'MESH_CYLINDER': ['cylinder', 'tube', 'pipe', 'can'],
    'MESH_TORUS': ['donut', 'ring', 'torus', 'doughnut'],
    'SPHERE': ['ball', 'orb', 'globe', 'round'],
    'CUBE': ['box', 'square', 'block'],
    'CONE': ['cone', 'triangle', 'pyramid'],
    'CAMERA_DATA': ['camera', 'lens', 'photo', 'video', 'film'],
    'OUTLINER_OB_CAMERA': ['camera', 'lens', 'photo', 'view'],
    'VIEW_CAMERA': ['camera', 'lens', 'viewport'],
    'HIDE_ON': ['eye', 'closed', 'invisible', 'hidden'],
    'HIDE_OFF': ['eye', 'open', 'visible', 'show', 'view'],
    'RESTRICT_VIEW_ON': ['eye', 'disabled', 'hidden'],
    'RESTRICT_VIEW_OFF': ['eye', 'visible', 'shown'],
    'LOCKED': ['lock', 'locked', 'secure', 'padlock', 'closed'],
    'UNLOCKED': ['unlock', 'unlocked', 'open', 'padlock'],
    'DECORATE_LOCKED': ['lock', 'locked', 'diamond'],
    'DECORATE_UNLOCKED': ['unlock', 'unlocked', 'diamond'],
    'HEART': ['heart', 'love', 'favorite', 'like'],
    'FUND': ['dollar', 'money', 'donation', 'support'],
    'TOOL_SETTINGS': ['wrench', 'spanner', 'tool', 'settings'],
    'BRUSH_DATA': ['brush', 'paint', 'stroke', 'bristle'],
    'EYEDROPPER': ['dropper', 'pipette', 'pick', 'sample'],
    'ZOOM_IN': ['magnify', 'plus', 'zoom', 'closer', 'enlarge'],
    'ZOOM_OUT': ['shrink', 'minus', 'zoom', 'smaller', 'reduce'],
    'VIEW_ZOOM': ['magnifying', 'glass', 'search', 'zoom'],
    'VIEWZOOM': ['magnifying', 'glass', 'zoom', 'magnify'],
    'RIGHTARROW': ['arrow', 'right', 'next', 'forward'],
    'TRIA_RIGHT': ['triangle', 'arrow', 'right', 'play'],
    'TRIA_LEFT': ['triangle', 'arrow', 'left', 'back'],
    'TRIA_UP': ['triangle', 'arrow', 'up', 'expand'],
    'TRIA_DOWN': ['triangle', 'arrow', 'down', 'collapse'],
    'PLAY': ['play', 'start', 'run', 'go', 'triangle'],
    'PAUSE': ['pause', 'stop', 'halt', 'bars'],
    'REW': ['rewind', 'backward', 'reverse', 'previous'],
    'FF': ['forward', 'fastforward', 'next', 'skip'],
    'REC': ['record', 'circle', 'dot', 'capture'],
    'FILE_FOLDER': ['folder', 'directory', 'files'],
    'FILE': ['document', 'file', 'paper', 'page'],
    'FILE_IMAGE': ['picture', 'image', 'photo', 'png', 'jpg'],
    'FILE_MOVIE': ['video', 'film', 'movie', 'mp4'],
    'FILE_SOUND': ['audio', 'sound', 'music', 'speaker', 'wave'],
    'CHECKMARK': ['check', 'tick', 'yes', 'correct', 'done', 'approved'],
    'X': ['cross', 'close', 'exit', 'cancel', 'no', 'wrong'],
    'CANCEL': ['x', 'cross', 'close', 'exit', 'stop'],
    'ERROR': ['warning', 'alert', 'danger', 'error', 'cross'],
    'QUESTION': ['help', 'question', 'mark', 'info'],
    'TRASH': ['delete', 'bin', 'garbage', 'remove', 'trash'],
    'PLUS': ['add', 'new', 'create', 'plus', 'cross'],
    'REMOVE': ['subtract', 'delete', 'minus', 'remove'],
    'ADD': ['plus', 'new', 'create', 'add'],
    'DUPLICATE': ['copy', 'clone', 'duplicate', 'double'],
    'LINKED': ['chain', 'link', 'connected', 'linked'],
    'UNLINKED': ['broken', 'chain', 'unlinked', 'disconnected'],
    'LINK_BLEND': ['chain', 'link', 'blend', 'file'],
    'MOD_MIRROR': ['mirror', 'flip', 'symmetry', 'reflect'],
    'MOD_ARRAY': ['array', 'duplicate', 'grid', 'repeat'],
    'MOD_BOOLEAN': ['boolean', 'union', 'difference', 'cut'],
    'MOD_SUBSURF': ['smooth', 'subdivide', 'subdivision', 'smooth'],
    'MOD_SOLIDIFY': ['thick', 'thickness', 'solid', 'extrude'],
    'PINNED': ['pin', 'pinned', 'tack', 'fixed'],
    'UNPINNED': ['unpin', 'unpinned', 'loose'],
    'MONKEY': ['suzanne', 'monkey', 'mascot', 'head'],
    'BLENDER': ['blender', 'logo', 'icon'],
    'HOME': ['house', 'home', 'start'],
    'USER': ['person', 'profile', 'account', 'user', 'avatar'],
    'COMMUNITY': ['people', 'group', 'users', 'community'],
    'INTERNET': ['globe', 'world', 'web', 'online', 'network'],
    'SETTINGS': ['gear', 'cog', 'settings', 'options', 'config'],
    'OPTIONS': ['dots', 'menu', 'more', 'options'],
    'THREE_DOTS': ['dots', 'menu', 'more', 'ellipsis'],
    'GRIP': ['handle', 'grip', 'dots', 'drag'],
    'MEMORY': ['ram', 'memory', 'chip', 'computer'],
    'EXPERIMENTAL': ['flask', 'beaker', 'lab', 'test', 'experiment'],
    'DISCLOSURE_TRI_DOWN': ['dropdown', 'expand', 'chevron', 'down'],
    'DISCLOSURE_TRI_RIGHT': ['collapse', 'chevron', 'right', 'closed'],
    'COLLECTION_NEW': ['folder', 'plus', 'new', 'collection'],
    'SNAP_ON': ['magnet', 'snap', 'grid', 'align'],
    'SNAP_OFF': ['magnet', 'off', 'free'],
}

def get_broad_category(icon_name):
    """Map icon names to broader category groups (18 categories)"""
    name_upper = icon_name.upper()
    
    if any(name_upper.startswith(p) for p in ['FILE', 'FOLDER', 'DISK', 'DRIVE', 'IMPORT', 'EXPORT', 'APPEND', 'LINK', 'LIBRARY', 'PACKAGE', 'DESKTOP', 'DOCUMENTS', 'BOOKMARKS', 'CURRENT', 'NEWFOLDER', 'SYSTEM', 'TEMP', 'FILTER', 'SORT', 'TAG', 'BACK', 'FORWARD', 'HOME', 'PRESET', 'SETTINGS']): return 'Files & Data'
    
    if any(name_upper.startswith(p) for p in ['MESH', 'CURVE', 'EMPTY', 'META', 'LATTICE', 'ARMATURE', 'BONE', 'SURFACE', 'VOLUME', 'POINTCLOUD', 'GREASEPENCIL', 'FONT', 'TEXT', 'STROKE', 'MONKEY', 'CUBE', 'SPHERE', 'CONE', 'CURVES', 'GP_', 'OBJECT', 'COLLECTION', 'GROUP', 'GEOMETRY']): return 'Objects & Geometry'
    
    if any(name_upper.startswith(p) for p in ['MOD_', 'CON_', 'HOOK', 'CONSTRAINT', 'MODIFIER', 'SHADERFX']): return 'Modifiers & Constraints'
    
    if any(name_upper.startswith(p) for p in ['KEYFRAME', 'KEY_', 'ACTION', 'DRIVER', 'NLA', 'IPO_', 'HANDLE', 'MARKER', 'PMARKER', 'ANIM', 'KEYTYPE', 'HANDLETYPE', 'KEYINGSET', 'FCURVE', 'DECORATE_ANIMATE', 'DECORATE_KEYFRAME', 'DECORATE_DRIVER', 'NORMALIZE', 'MUTE_IPO', 'SOLO_']): return 'Animation & Keyframes'
    
    if any(name_upper.startswith(p) for p in ['RENDER', 'SHADING', 'LIGHT', 'CAMERA', 'MATERIAL', 'TEXTURE', 'WORLD', 'NODE', 'MAT', 'IMAGE', 'OUTPUT', 'LIGHTPROBE', 'SEQ_']): return 'Rendering & Shading'
    
    if any(name_upper.startswith(p) for p in ['TRIA', 'ARROW', 'DISCLOSURE', 'PANEL', 'WINDOW', 'MENU', 'TOPBAR', 'STATUSBAR', 'COLLAPSEMENU', 'DOT', 'GRIP', 'BORDERMOVE', 'RIGHTARROW', 'DOWNARROW', 'THREE_DOTS', 'CHECKMARK', 'X', 'CANCEL', 'ERROR', 'QUESTION', 'BLANK', 'NONE', 'QUIT', 'HELP', 'CHECKBOX', 'RADIOBUT', 'SCREEN', 'AREA_', 'SPLIT_', 'ALIGN_', 'ANCHOR_']): return 'UI & Navigation'
    
    if any(name_upper.startswith(p) for p in ['PLAY', 'PAUSE', 'REC', 'FF', 'REW', 'LOOP', 'TIME', 'FRAME', 'PREV_KEY', 'NEXT_KEY', 'GHOST', 'PREVIEW', 'RECOVER', 'FREEZE']): return 'Playback & Timeline'
    
    if any(name_upper.startswith(p) for p in ['WORKSPACE', 'SCENE', 'OUTLINER', 'PROPERTIES', 'CONSOLE', 'INFO', 'SEQUENCE', 'UV', 'GRAPH', 'FILEBROWSER', 'SPREADSHEET', 'PREFERENCES', 'SCRIPT', 'ASSET', 'TRACKER', 'VIEW3D']): return 'Editors & Workspaces'
    
    if any(name_upper.startswith(p) for p in ['PHYSICS', 'PARTICLE', 'FORCE_', 'FLUID', 'RIGID', 'BOIDS', 'STRANDS', 'SPEAKER', 'SOUND']): return 'Physics & Simulation'
    
    if any(name_upper.startswith(p) for p in ['TOOL', 'BRUSH', 'PLUS', 'REMOVE', 'DUPLICATE', 'TRASH', 'ADD', 'COPY', 'EYEDROPPER', 'AUTO', 'RNA', 'PLUGIN', 'OPTIONS', 'BLENDER', 'STYLUS', 'HAND', 'ALIASED', 'ANTIALIASED']): return 'Tools & Operations'
    
    if any(name_upper.startswith(p) for p in ['LOCK', 'UNLINKED', 'LINKED', 'PIN', 'HIDE', 'RESTRICT', 'SNAP', 'FAKE_USER', 'RECORD', 'VIS_SEL', 'INDIRECT', 'ONIONSKIN', 'HOLDOUT', 'AUTOMERGE', 'CLIPUV', 'DECORATE_LOCK', 'DECORATE_OVERRIDE', 'DECORATE_LINKED']): return 'States & Toggles'
    
    if any(name_upper.startswith(p) for p in ['SELECT', 'PIVOT', 'CURSOR', 'PROP_', 'ORIENTATION', 'GIZMO', 'TRANSFORM', 'VERTEXSEL', 'EDGESEL', 'FACESEL', 'CENTER', 'PASTEDOWN', 'PASTEFLIP', 'COPYDOWN', 'STICKY', 'FIXED_SIZE', 'NORMALS']): return 'Selection & Transform'
    
    if any(name_upper.startswith(p) for p in ['EDITMODE', 'SCULPTMODE', 'POSE', 'VPAINT', 'WPAINT', 'TPAINT', 'PARTICLEMODE', 'OBJECT_DATA', 'TRACKING', 'SHAPEKEY']): return 'Modes & Editing'
    
    if any(name_upper.startswith(p) for p in ['VIEW', 'ZOOM', 'OVERLAY', 'XRAY', 'GRID', 'AXIS', 'LAYER', 'LOCKVIEW', 'VIEWZOOM', 'FULLSCREEN', 'IMGDISPLAY', 'LONGDISPLAY', 'SHORTDISPLAY', 'FONTPREVIEW']): return 'View & Display'
    
    if any(name_upper.startswith(p) for p in ['COLOR', 'RGB', 'COLORSET', 'COLLECTION_COLOR', 'STRIP_COLOR', 'LAYERGROUP_COLOR']): return 'Colors & Visual'
    
    if any(name_upper.startswith(p) for p in ['EVENT', 'MOUSE', 'KEY_', 'GESTURE', 'PAD']): return 'Input & Events'
    
    if any(name_upper.startswith(p) for p in ['BOLD', 'ITALIC', 'UNDERLINE', 'SMALL_CAPS', 'WORDWRAP', 'LINENUMBERS', 'SYNTAX', 'SCRIPTPLUGINS']): return 'Text & Formatting'
    
    if any(name_upper.startswith(p) for p in ['_DATA', 'NODE_SOCKET']): return 'Data Types'
    
    return 'Miscellaneous'

def get_super_compact_category(icon_name):
    """Map icon names to compact categories"""
    broad = get_broad_category(icon_name)
    category_map = {
        'Objects & Geometry': 'Content Creation',
        'Modifiers & Constraints': 'Content Creation',
        'Physics & Simulation': 'Content Creation',
        'Modes & Editing': 'Content Creation',
        'Selection & Transform': 'Content Creation',
        'Animation & Keyframes': 'Animation & Motion',
        'Playback & Timeline': 'Animation & Motion',
        'Rendering & Shading': 'Visual & Rendering',
        'Colors & Visual': 'Visual & Rendering',
        'View & Display': 'Visual & Rendering',
        'UI & Navigation': 'Interface & Tools',
        'Tools & Operations': 'Interface & Tools',
        'States & Toggles': 'Interface & Tools',
        'Files & Data': 'Data & Assets',
        'Editors & Workspaces': 'Data & Assets',
        'Input & Events': 'Data & Assets',
        'Text & Formatting': 'Data & Assets',
        'Data Types': 'Data & Assets',
        'Miscellaneous': 'Miscellaneous'
    }
    return category_map.get(broad, 'Miscellaneous')


# =========================================================================
# Utilities
# =========================================================================

def icon_matches_search(icon_name, search_terms):
    """Check if icon matches search terms (name or visual keywords)"""
    icon_lower = icon_name.lower()
    
    for term in search_terms:
        term_lower = term.strip().lower()
        if not term_lower:
            continue
            
        # Check icon name
        if term_lower in icon_lower:
            return True
        
        # Check visual keywords
        if icon_name in ICON_VISUAL_KEYWORDS:
            keywords = ICON_VISUAL_KEYWORDS[icon_name]
            if any(term_lower in keyword for keyword in keywords):
                return True
    
    return False

def get_all_icons():
    """Get all available internal Blender icons"""
    icons = []
    try:
        enum_items = bpy.types.UILayout.bl_rna.functions["prop"].parameters["icon"].enum_items
        for i in range(len(enum_items)):
            icons.append(enum_items[i].identifier)
    except Exception as e:
        print(f"Error getting icons: {e}")
        # Fallback if the standard method fails (unlikely)
        pass
    return icons


# =========================================================================
# Properties
# =========================================================================

class IconViewerProperties(PropertyGroup):
    search_text: StringProperty(
        name="Search",
        description="Filter icons by name or visual appearance (e.g., 'hand', 'sun', 'eye')",
        default=""
    )
    show_names: BoolProperty(
        name="Show Names",
        description="Display icon names next to icons",
        default=False
    )
    icon_scale: FloatProperty(
        name="Icon Scale",
        description="Scale of the icons",
        default=1.0,
        min=0.5,
        max=3.0,
        soft_min=0.8,
        soft_max=2.0,
        step=10
    )
    category_mode: EnumProperty(
        name="Category Mode",
        description="Icon organization method",
        items=[
            ('NONE', 'None', 'Flat list', 'LINENUMBERS_OFF', 0),
            ('COMPACT', 'Compact', 'Five main categories', 'ALIGN_TOP', 1),
            ('BROAD', 'Broad', 'Standard categorization', 'PRESET', 2),
            ('PREFIX', 'Prefix', 'Group by prefix', 'ALIGN_JUSTIFY', 3),
        ],
        default='COMPACT'
    )


# =========================================================================
# Operators
# =========================================================================

class ICONVIEWER_OT_copy_icon_name(Operator):
    """Copy icon name to clipboard. Hover to see icon name"""
    bl_idname = "iconviewer.copy_icon_name"
    bl_label = "Copy Icon Name"
    
    icon_name: StringProperty()
    
    def execute(self, context):
        context.window_manager.clipboard = self.icon_name
        self.report({'INFO'}, f"Copied: {self.icon_name}")
        return {'FINISHED'}

class ICONVIEWER_OT_clear_search(Operator):
    """Clear search field"""
    bl_idname = "iconviewer.clear_search"
    bl_label = "Clear Search"
    
    def execute(self, context):
        context.scene.icon_viewer_props.search_text = ""
        return {'FINISHED'}


# =========================================================================
# UI / Panel
# =========================================================================

class ICONVIEWER_PT_main_panel(Panel):
    bl_label = "My Icon Viewer"
    bl_idname = "ICONVIEWER_PT_main_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Dev"
    
    def draw_header(self, context):
        layout = self.layout
        op = layout.operator("wm.url_open", text="", icon='URL', emboss=False)
        op.url = "https://ui.blender.org/icons"
        
    def draw(self, context):
        layout = self.layout
        scene = context.scene
        props = scene.icon_viewer_props
        
        settings_box = layout.box()
        settings_box.label(text="Settings", icon='PREFERENCES')
        
        row = settings_box.row(align=True)
        row.prop(props, "search_text", text="", icon='VIEWZOOM', placeholder="Search by name or looks...")
        if props.search_text:
            row.operator("iconviewer.clear_search", text="", icon='X')
        
        icon = 'HIDE_OFF' if props.show_names else 'HIDE_ON'
        row.prop(props, "show_names", text="", icon=icon)
        row = settings_box.row()
        row.prop(props, "category_mode", text="", expand=True)
        row.prop(props, "icon_scale", text="Icon Scale", slider=True)

        # Get all icon identifiers
        icons = get_all_icons()
        
        # Filter icons based on search (including visual keywords)
        search = props.search_text.strip()
        filtered_icons = icons
        if search:
            search_terms = search.split()
            filtered_icons = [icon for icon in icons if icon_matches_search(icon, search_terms)]
        
        # Results header with count
        if search:
            header_box = layout.box()
            header_row = header_box.row()
            header_row.alignment = 'CENTER'
            header_row.label(text=f"Found {len(filtered_icons)} of {len(icons)} icons", icon='INFO')
        
        # No results message
        if not filtered_icons:
            no_results = layout.box()
            no_results.label(text="No icons match your search", icon='ERROR')
            return
        
        # Display icons based on category mode
        show_names = props.show_names
        icon_scale = props.icon_scale
        category_mode = props.category_mode
        
        if category_mode == 'NONE':
            # Display all icons without categories (flat view)
            icons_box = layout.box()
            
            idx = 0
            while idx < len(filtered_icons):
                grid_row = icons_box.grid_flow(
                    row_major=True,
                    columns=0,
                    even_columns=True,
                    even_rows=False,
                    align=True
                )
                
                grid_row.scale_x = icon_scale
                grid_row.scale_y = icon_scale
                
                for _ in range(50):
                    if idx >= len(filtered_icons):
                        break
                    
                    icon_name = filtered_icons[idx]
                    
                    if show_names:
                        op = grid_row.operator("iconviewer.copy_icon_name",
                                        text=icon_name,
                                        icon=icon_name,
                                        emboss=True)
                    else:
                        op = grid_row.operator("iconviewer.copy_icon_name",
                                        text="",
                                        icon=icon_name,
                                        emboss=True)
                    op.icon_name = icon_name
                    idx += 1
        
        elif category_mode == 'PREFIX':
            # Prefix-based categories (most granular - 50+ categories)
            categories = {}
            for icon_name in filtered_icons:
                prefix = icon_name.split('_')[0] if '_' in icon_name else 'OTHER'
                if prefix not in categories:
                    categories[prefix] = []
                categories[prefix].append(icon_name)
            
            # Display each category with compact styling
            for category in sorted(categories.keys()):
                category_icons = sorted(categories[category])
                
                # Category header
                category_box = layout.box()
                row = category_box.row()
                row.scale_y = 0.6
                row.label(text=f"{category} ({len(category_icons)})", icon='DISCLOSURE_TRI_DOWN')
                # Icons grid for this category
                icons_box = category_box
                
                idx = 0
                while idx < len(category_icons):
                    grid_row = icons_box.grid_flow(
                        row_major=True,
                        columns=0,
                        even_columns=True,
                        even_rows=False,
                        align=True
                    )
                    
                    grid_row.scale_x = icon_scale
                    grid_row.scale_y = icon_scale
                    
                    for _ in range(50):
                        if idx >= len(category_icons):
                            break
                        
                        icon_name = category_icons[idx]
                        
                        if show_names:
                            op = grid_row.operator("iconviewer.copy_icon_name",
                                            text=icon_name,
                                            icon=icon_name,
                                            emboss=True)
                        else:
                            op = grid_row.operator("iconviewer.copy_icon_name",
                                            text="",
                                            icon=icon_name,
                                            emboss=True)
                        op.icon_name = icon_name
                        idx += 1
                
                # Minimal spacing between categories
                layout.separator(factor=0.3)
        
        elif category_mode == 'BROAD':
            # Broad categories (18 comprehensive categories)
            categories = {}
            for icon_name in filtered_icons:
                category = get_broad_category(icon_name)
                if category not in categories:
                    categories[category] = []
                categories[category].append(icon_name)
            
            # Display each category
            for category in sorted(categories.keys()):
                category_icons = sorted(categories[category])
                
                # Category header
                category_box = layout.box()
                row = category_box.row()
                row.scale_y = 0.6
                row.label(text=f"{category} ({len(category_icons)})", icon='DISCLOSURE_TRI_DOWN')
                # Icons grid for this category
                icons_box = category_box
                
                idx = 0
                while idx < len(category_icons):
                    grid_row = icons_box.grid_flow(
                        row_major=True,
                        columns=0,
                        even_columns=True,
                        even_rows=False,
                        align=True
                    )
                    
                    grid_row.scale_x = icon_scale
                    grid_row.scale_y = icon_scale
                    
                    for _ in range(50):
                        if idx >= len(category_icons):
                            break
                        
                        icon_name = category_icons[idx]
                        
                        if show_names:
                            op = grid_row.operator("iconviewer.copy_icon_name",
                                            text=icon_name,
                                            icon=icon_name,
                                            emboss=True)
                        else:
                            op = grid_row.operator("iconviewer.copy_icon_name",
                                            text="",
                                            icon=icon_name,
                                            emboss=True)
                        op.icon_name = icon_name
                        idx += 1

        elif category_mode == 'COMPACT':
            # Super compact categories (5 mega-categories for minimal scrolling)
            categories = {}
            for icon_name in filtered_icons:
                category = get_super_compact_category(icon_name)
                if category not in categories:
                    categories[category] = []
                categories[category].append(icon_name)
            
            # Display each mega-category
            for category in sorted(categories.keys()):
                category_icons = sorted(categories[category])
                
                # Category header
                category_box = layout.box()
                row = category_box.row()
                row.scale_y = 0.6
                row.label(text=f"{category} ({len(category_icons)})", icon='DISCLOSURE_TRI_DOWN')
                # Icons grid for this category
                icons_box = category_box
                
                idx = 0
                while idx < len(category_icons):
                    grid_row = icons_box.grid_flow(
                        row_major=True,
                        columns=0,
                        even_columns=True,
                        even_rows=False,
                        align=True
                    )
                    
                    grid_row.scale_x = icon_scale
                    grid_row.scale_y = icon_scale
                    
                    for _ in range(50):
                        if idx >= len(category_icons):
                            break
                        
                        icon_name = category_icons[idx]
                        
                        if show_names:
                            op = grid_row.operator("iconviewer.copy_icon_name",
                                            text=icon_name,
                                            icon=icon_name,
                                            emboss=True)
                        else:
                            op = grid_row.operator("iconviewer.copy_icon_name",
                                            text="",
                                            icon=icon_name,
                                            emboss=True)
                        op.icon_name = icon_name
                        idx += 1

# =========================================================================
# Registration
# =========================================================================

def register():
    bpy.types.Scene.icon_viewer_props = bpy.props.PointerProperty(type=IconViewerProperties)

def unregister():
    del bpy.types.Scene.icon_viewer_props
