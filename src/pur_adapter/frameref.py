"""FrameRef file_version 2 <-> PureRef, with explicit conversion reports.

Measured against the installed FrameRef: item origins are top-left, PureRef
image/note origins are centred. Original files are never overwritten.
"""
import io
import json
import math
import tempfile
import uuid
from pathlib import Path

from PIL import Image, ImageDraw
from .board import Board
from .core import affine, finite, multiply
from .container import UnsupportedFormat
from . import qt
from .drawings import Stroke, SOLID, render_strokes
from .images import open_as_displayed
from .manifest import asset_path, MAX_ITEMS
from .render import _note_box, parse_color
from .scene import drawing_order, world_properties
from .text import html_from_plain, is_rich_text

KEYS = ('m11', 'm12', 'm13', 'm21', 'm22', 'm23', 'm31', 'm32', 'm33')


def properties(item):
    return {p['name']: p['value'] for p in item['properties']}


def matrix(value):
    values = [value[k] for k in KEYS]
    finite(**dict(zip(KEYS, values)))
    if qt.has_perspective(values, tolerance=0):
        raise UnsupportedFormat('Perspective transforms are not supported by this converter')
    return values


def color(value):
    if isinstance(value, str):
        rgba = parse_color(value, None)
        if rgba is None:
            raise ValueError('Invalid FrameRef colour')
        return rgba
    result = tuple(value[k] for k in ('r', 'g', 'b', 'a'))
    if any(not isinstance(v, int) or not 0 <= v <= 255 for v in result):
        raise ValueError('Invalid FrameRef colour')
    return result


def hex_color(value):
    r, g, b, a = color(value)
    return f'#{a:02x}{r:02x}{g:02x}{b:02x}'


def prop(name, kind, value):
    return dict(action=0, name=name, type=kind, value=value)


def native_item(kind, width, height, transform, *, opacity=1, locked=False,
                z=0, label='', extra=()):
    transparent = dict(r=0, g=0, b=0, a=0)
    return dict(type=kind, properties=[
        prop('uid', 'QUuid', '{' + str(uuid.uuid4()) + '}'),
        prop('width', 'float', width), prop('height', 'float', height),
        prop('itemTransform', 'QTransform', dict(zip(KEYS, transform))),
        prop('zDepth', 'float', z), prop('itemOpacity', 'float', opacity),
        prop('cropOffset', 'QPointF', dict(x=0, y=0)),
        prop('antialiased', 'bool', True), prop('sourceURL', 'QString', ''),
        prop('label', 'QString', label), prop('centerHtml', 'QString', ''),
        prop('cornerRadius', 'int', 0), prop('borderWidth', 'int', 0),
        prop('borderColor', 'QColor', transparent), prop('borderStyle', 'Qt::PenStyle', 0),
        prop('backgroundColor', 'QColor', transparent),
        prop('rating', 'int', 0), prop('tags', 'QStringList', []),
        prop('createdAt', 'qlonglong', 0), prop('drawGrayscale', 'bool', False),
        prop('normalizedCropRegion', 'QRectF', dict(x=0, y=0, width=1, height=1)),
        prop('isCropped', 'bool', False), prop('locked', 'bool', locked),
        *extra])


def _warn(report, item, message):
    report['changes'].append(dict(item=str(item), message=message))


def _place(board, item_id, m, p):
    board.update(item_id, x=m[6], y=m[7], matrix=[m[0], m[1], m[3], m[4]],
                 opacity=p.get('itemOpacity', 1), locked=p.get('locked', False),
                 z=p.get('zDepth', 0), comment=p.get('sourceURL') or None)


def _load(path):
    document = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    if document.get('file_version') != 2:
        raise UnsupportedFormat('Expected a FrameRef file_version 2 board')
    items = document['board']['items']
    if not isinstance(items, list) or len(items) > MAX_ITEMS:
        raise ValueError('Invalid or excessive FrameRef item list')
    return document, items


def frameref_to_pur(source, output):
    source, output = Path(source), Path(output)
    if output.exists():
        raise FileExistsError(output)
    report = dict(direction='FrameRef → PureRef', source=str(source), output=str(output), changes=[])
    _, items = _load(source)
    by_uid = {}
    with Board() as board:
        for item in items:
            p = properties(item)
            uid, kind = p.get('uid', ''), item['type']
            if uid in by_uid:
                raise ValueError('Duplicate FrameRef UID')
            by_uid[uid] = p
            if kind == 'EdgeItem':
                continue
            m = matrix(p.get('itemTransform', dict(zip(KEYS, affine()))))
            width, height = p['width'], p['height']
            finite(width=width, height=height)
            if width <= 0 or height <= 0:
                raise ValueError('FrameRef item size must be positive')
            name = p.get('label') or kind
            if kind == 'ImageItem':
                zoom = p.get('localZoomPan')
                offset = p.get('cropOffset', dict(x=0, y=0))
                if (zoom and matrix(zoom) != affine()) or offset != dict(x=0, y=0):
                    raise UnsupportedFormat('Local image zoom/pan or cropOffset is not yet supported')
                path = asset_path(source.parent / 'content', p['filePath'])
                item_id = board.add_image(path, name=name)
                iw, ih = board.image_settings(item_id)['pixel_size']
                m = multiply(affine(iw/2, ih/2), multiply(
                    [width/iw, 0, 0, 0, height/ih, 0, 0, 0, 1], m))
                options = dict(grayscale=p.get('drawGrayscale', False),
                               bilinear=p.get('antialiased', True))
                if p.get('isCropped'):
                    c = p['normalizedCropRegion']
                    options['crop'] = (c['x']*iw, c['y']*ih, c['width']*iw, c['height']*ih)
                board.configure_image(item_id, **options)
            elif kind in ('TextItem', 'NoteItem'):
                item_id = board.add_note(p.get('html', p.get('centerHtml', '')), name=name)
                board.style_note(item_id, width=width, height=height,
                                 background_color=hex_color(p.get('backgroundColor', '#00202020')))
                m = multiply(affine(width/2, height/2), m)
                _warn(report, uid, 'Text layout and box height can differ between the applications.')
            elif kind == 'ColorSwatchItem':
                rgba = color(p['color'])
                image = Image.new('RGBA', (320, 320), rgba)
                if p.get('showCaption', True):
                    ImageDraw.Draw(image).text((12, 290), hex_color(p['color']), fill='white')
                payload = io.BytesIO()
                image.save(payload, format='PNG')
                item_id = board.add_image_bytes(payload.getvalue(), 'Colour swatch', file_suffix='.png')
                m = multiply(affine(160, 160), multiply(
                    [width/320, 0, 0, 0, height/320, 0, 0, 0, 1], m))
                _warn(report, uid, 'Colour swatch converted to a static image.')
            else:
                raise UnsupportedFormat(f'FrameRef {kind} is not supported; no output was written')
            _place(board, item_id, m, p)
            if p.get('tags') or p.get('rating') or (p.get('borderWidth') and p.get('borderStyle')) or p.get('centerHtml'):
                _warn(report, uid, 'FrameRef tags, rating, borders or overlays are not represented in PureRef.')
        for item in items:
            if item['type'] != 'EdgeItem':
                continue
            p = properties(item)
            anchors = []
            normals = []
            for prefix in ('from', 'to'):
                endpoint = by_uid.get(p[prefix+'ItemUid'])
                if endpoint is None:
                    raise ValueError('FrameRef connection references a missing item')
                side = p[prefix+'Side']
                # Side order: top, right, bottom, left. Unlike image centres,
                # side anchors keep the connector outside the image rectangle.
                if side not in (0, 1, 2, 3):
                    raise UnsupportedFormat(f'Unknown FrameRef connector side: {side}')
                w, h = endpoint['width'], endpoint['height']
                x, y, nx, ny = ((w/2, 0, 0, -1), (w, h/2, 1, 0),
                                 (w/2, h, 0, 1), (0, h/2, -1, 0))[side]
                m = matrix(endpoint['itemTransform'])
                anchors.append(qt.map_point(m, x, y))
                dx, dy = nx*m[0]+ny*m[3], nx*m[1]+ny*m[4]
                length = math.hypot(dx, dy)
                if length == 0:
                    raise ValueError('Degenerate connector endpoint transform')
                normals.append((dx/length, dy/length))
            if p.get('direction', 0) != 0:
                raise UnsupportedFormat('Directional FrameRef connectors are not yet supported')
            a, b = anchors
            # shortcut: approximate FrameRef's routing with a cubic Bezier;
            # exact control-point distances are not stored in file_version 2.
            distance = max(40, math.hypot(b[0]-a[0], b[1]-a[1]) * .4)
            controls = [(x+nx*distance, y+ny*distance)
                        for (x,y),(nx,ny) in zip(anchors,normals)]
            stroke = Stroke(color(p['color']), 2,
                            ((0, *a), (2, *controls[0]), (3, *controls[1]), (3, *b)),
                            0, (0, 0), SOLID)
            item_id = board.add_drawing([stroke], name='FrameRef connection')
            _place(board, item_id, affine(), p)
            _warn(report, p.get('uid', ''),
                  'Connection converted to a static curve with side anchors; routing is approximate and live bindings are lost.')
        report['items'] = len(board.items)
        _save_pur_with_report(board, output, report)
    return report


def _save_pur_with_report(board, output, report):
    report_path = output.with_name(output.name + '.conversion.json')
    if report_path.exists():
        raise FileExistsError(report_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    # Prepare both files before publishing; rollback only outputs created here.
    with tempfile.TemporaryDirectory(prefix='.conversion-', dir=output.parent) as temp:
        staged = Path(temp) / output.name
        board.save(staged)
        staged_report = Path(temp) / report_path.name
        staged_report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        staged_report.rename(report_path)
        try:
            staged.rename(output)
        except BaseException:
            report_path.unlink()
            raise


def pur_to_frameref(source, output):
    source, output = Path(source), Path(output)
    if output.exists():
        raise FileExistsError(output)
    report = dict(direction='PureRef → FrameRef', source=str(source), output=str(output), changes=[])
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.conversion-', dir=output.parent) as temp:
        folder = Path(temp) / 'board'
        content = folder / 'content'
        content.mkdir(parents=True)
        native = []
        with Board.load(source) as board:
            world = world_properties(board)
            for rank, item_id in enumerate(drawing_order(board)):
                item = board.get(item_id)
                m, opacity = world[item_id]
                matrix(dict(zip(KEYS, m)))
                extra = []
                if item.kind == 'group':
                    _warn(report, item.id, 'Group hierarchy flattened; children keep their world positions.')
                    continue
                if item.kind == 'image':
                    settings = board.image_settings(item.id)
                    asset = board._row('images', item.image_id)
                    if not settings['embedded']:
                        raise UnsupportedFormat('Embed linked PureRef images before conversion')
                    payload = bytes(asset['data'])
                    iw, ih = settings['pixel_size']
                    im = qt.read_transform(board._row('items_images', item.id)['image_transform'])
                    matrix(dict(zip(KEYS, im)))
                    extension = asset['format'].lower()
                    if extension == 'jpeg':
                        extension = 'jpg'
                    width, height = iw, ih
                    if settings['cropped'] or settings['frame']:
                        if settings['crop'] is None:
                            raise UnsupportedFormat('Non-rectangular crops are not supported')
                        with open_as_displayed(payload) as picture:
                            picture.seek(settings['frame'] or 0)
                            picture = picture.convert('RGBA')
                            x, y, width, height = settings['crop']
                            if any(v != round(v) for v in (x, y, width, height)):
                                raise UnsupportedFormat('Fractional-pixel crops are not supported')
                            picture = picture.crop((int(x), int(y), int(x+width), int(y+height)))
                            data = io.BytesIO()
                            picture.save(data, format='PNG')
                            payload = data.getvalue()
                        im = multiply(affine(x, y), im)
                        extension = 'png'
                        _warn(report, item.id, 'Crop or animation frame baked into a static PNG.')
                    if settings['speed'] != 1 or settings['playing']:
                        _warn(report, item.id, 'Animation playback state/speed is not transferred.')
                    m = multiply(im, m)
                    filename = f'image-{item.id}.{extension}'
                    (content / filename).write_bytes(payload)
                    kind = 'ImageItem'
                    extra = [prop('filePath', 'QString', filename),
                             prop('downloadUrl', 'QString', ''),
                             prop('localZoomPan', 'QTransform', dict(zip(KEYS, affine())))]
                elif item.kind == 'note':
                    x, y, width, height = _note_box(board, item)
                    m = multiply(affine(x, y), m)
                    kind = 'TextItem'
                    text = item.text or ''
                    if not is_rich_text(text):
                        text = html_from_plain(text)
                    style = board.note_style(item.id)
                    text = '<div style="color:' + ('#%02x%02x%02x' % parse_color(style['text_color'], (255, 255, 255, 255))[:3]) + '">' + text + '</div>'
                    rgba = parse_color(style['background_color'], (32,32,32,255))
                    extra = [prop('html', 'QString', text), prop('clipText', 'bool', False),
                             prop('textAlignment', 'QFlags<Qt::AlignmentFlag>', 0),
                             prop('backgroundColor', 'QColor', dict(zip(('r','g','b','a'), rgba)))]
                    _warn(report, item.id, 'Text layout and automatic note size can differ between the applications.')
                elif item.kind == 'drawing':
                    png, bounds, _ = render_strokes(board.drawing_strokes(item.id), 4)
                    left, top, right, bottom = bounds
                    with Image.open(io.BytesIO(png)) as picture:
                        width, height = picture.size
                    m = multiply([.25,0,0,0,.25,0,left,top,1], m)
                    filename = f'drawing-{item.id}.png'
                    (content / filename).write_bytes(png)
                    kind = 'ImageItem'
                    extra = [prop('filePath', 'QString', filename),
                             prop('localZoomPan', 'QTransform', dict(zip(KEYS, affine())))]
                    _warn(report, item.id, 'Pen drawing rasterized to PNG; vector editing is lost.')
                else:
                    raise UnsupportedFormat(f'Unsupported PureRef item kind: {item.kind}')
                entry = native_item(kind, width, height, m, opacity=opacity,
                                    locked=item.locked, z=rank, label=item.name, extra=extra)
                if item.kind == 'image':
                    for p in entry['properties']:
                        if p['name'] == 'drawGrayscale':
                            p['value'] = settings['grayscale']
                        if p['name'] == 'antialiased':
                            p['value'] = settings['bilinear']
                if item.comment:
                    _warn(report, item.id, 'PureRef comment is not transferred.')
                native.append(entry)
        corners = []
        for entry in native:
            p = properties(entry)
            m = matrix(p['itemTransform'])
            corners.extend(qt.map_point(m,x,y) for x,y in
                           ((0,0),(p['width'],0),(0,p['height']),(p['width'],p['height'])))
        center = [(min(p[j] for p in corners)+max(p[j] for p in corners))/2
                  if corners else 0 for j in (0,1)]
        document = dict(file_version=2, board=dict(items=native,
                        items_center=dict(zip(('x','y'), center)),
                        viewport=dict(m11=1,m12=0,m21=0,m22=1,dx=0,dy=0,x=0,y=0,name='viewport')))
        (folder / 'main.frameref').write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding='utf-8')
        report['items'] = len(native)
        (folder / 'conversion.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        folder.rename(output)
    return report


def convert(source, output):
    source = Path(source)
    if source.suffix.lower() == '.pur':
        return pur_to_frameref(source, output)
    if source.suffix.lower() == '.frameref':
        return frameref_to_pur(source, output)
    raise ValueError('Choose a .pur or .frameref file')



