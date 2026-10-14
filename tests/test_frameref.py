import io
import json
from pathlib import Path
import tempfile
import unittest

from PIL import Image
from pur_adapter import Board
from pur_adapter.core import affine, multiply
from pur_adapter.container import UnsupportedFormat
from pur_adapter.frameref import convert, native_item, properties, KEYS
from pur_adapter import qt
from pur_adapter.scene import world_properties


class FrameRefTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        pixels = Image.new('RGB',(80,40),'red')
        Image.Image.paste(pixels, 'blue', (40,0,80,40))
        data = io.BytesIO()
        pixels.save(data,format='PNG')
        self.payload = data.getvalue()
        self.image = self.root/'image.png'
        self.image.write_bytes(self.payload)

    def test_native_top_left_to_pur_center_and_roundtrip(self):
        content = self.root/'content'
        content.mkdir()
        (content/'image.png').write_bytes(self.payload)
        m = affine(350,-70,scale=2,rotation=35)
        entry = native_item('ImageItem',80,40,m,opacity=.6,locked=True,
                            extra=[dict(name='filePath',value='image.png')])
        source = self.root/'main.frameref'
        source.write_text(json.dumps(dict(file_version=2,board=dict(items=[entry]))))
        output = self.root/'out.pur'
        report = convert(source,output)
        self.assertEqual(report['items'],1)
        with Board.load(output) as board:
            item = board.items[0]
            expected = multiply(affine(40,20),m)
            for a,b in zip(board.transform_matrix(item.id),expected):
                self.assertAlmostEqual(a,b)
            self.assertEqual(item.opacity,.6)
            self.assertTrue(item.locked)
            board.export_assets(self.root/'assets')
            self.assertEqual(next((self.root/'assets').iterdir()).read_bytes(),self.payload)
        destination = self.root/'returned'
        convert(output,destination)
        p = properties(json.loads((destination/'main.frameref').read_text())['board']['items'][0])
        for key,value in zip(KEYS,m):
            self.assertAlmostEqual(p['itemTransform'][key],value)

    def test_crop_flip_and_nested_world_geometry(self):
        source = self.root/'source.pur'
        with Board() as board:
            group = board.add_group(x=200,y=300)
            board.update(group,scale=2,rotation=20,opacity=.5)
            image = board.add_image(self.image,x=100,y=50,parent_id=group)
            board.configure_image(image,crop=(10,5,30,20),flip_horizontal=True,grayscale=True)
            board.update(image,opacity=.8)
            world = world_properties(board)[image][0]
            im = qt.read_transform(board._row('items_images',image)['image_transform'])
            expected = multiply(multiply(affine(10,5),im),world)
            board.save(source)
        destination = self.root/'converted'
        report = convert(source,destination)
        self.assertEqual(len(report['changes']),2)
        p = properties(json.loads((destination/'main.frameref').read_text())['board']['items'][0])
        self.assertTrue(p['drawGrayscale'])
        self.assertAlmostEqual(p['itemOpacity'],.4)
        for key,value in zip(KEYS,expected):
            self.assertAlmostEqual(p['itemTransform'][key],value)
        with Image.open(destination/'content'/p['filePath']) as image:
            self.assertEqual(image.size,(30,20))

    def test_failure_has_no_outputs_and_path_escape_is_rejected(self):
        entry = native_item('VideoItem',80,40,affine())
        source = self.root/'main.frameref'
        source.write_text(json.dumps(dict(file_version=2,board=dict(items=[entry]))))
        output = self.root/'out.pur'
        with self.assertRaises(UnsupportedFormat):
            convert(source,output)
        self.assertFalse(output.exists())
        self.assertFalse(output.with_name(output.name+'.conversion.json').exists())
        entry = native_item('ImageItem',80,40,affine(),
                            extra=[dict(name='filePath',value='../image.png')])
        source.write_text(json.dumps(dict(file_version=2,board=dict(items=[entry]))))
        with self.assertRaisesRegex(ValueError,'escapes'):
            convert(source,output)
        self.assertFalse(output.exists())

    def test_bottom_connector_extends_below_images(self):
        content = self.root/'content'
        content.mkdir()
        (content/'image.png').write_bytes(self.payload)
        entries = [native_item('ImageItem',80,40,affine(x,0),
                   extra=[dict(name='filePath',value='image.png')]) for x in (0,200)]
        uids = [properties(e)['uid'] for e in entries]
        edge = native_item('EdgeItem',1,1,affine(),z=1010,extra=[
            dict(name='fromItemUid',value=uids[0]),dict(name='toItemUid',value=uids[1]),
            dict(name='fromSide',value=2),dict(name='toSide',value=2),
            dict(name='color',value=dict(r=175,g=175,b=175,a=255)),
            dict(name='direction',value=0)])
        source = self.root/'main.frameref'
        source.write_text(json.dumps(dict(file_version=2,board=dict(items=entries+[edge]))))
        output = self.root/'connected.pur'
        convert(source,output)
        with Board.load(output) as board:
            item = next(i for i in board.items if i.kind == 'drawing')
            stroke = board.drawing_strokes(item.id)[0]
            self.assertEqual(stroke.elements[0],(0,40,40))
            self.assertEqual(stroke.elements[-1],(3,240,40))
            self.assertGreater(stroke.elements[1][2],40)
            self.assertGreater(stroke.elements[2][2],40)
            self.assertEqual(item.z,1010)

    def test_native_note_with_unset_text_colour(self):
        source = self.root/'note.pur'
        with Board() as board:
            item_id = board.add_note('Native note')
            board._conn.execute('UPDATE items_notes SET text_color=NULL WHERE id=?', (item_id,))
            board.save(source)
        destination = self.root/'note-FrameRef'
        convert(source, destination)
        document = json.loads((destination/'main.frameref').read_text())
        p = properties(document['board']['items'][0])
        self.assertIn('#ffffff', p['html'])
        self.assertIn('Native note', p['html'])
        self.assertEqual(p['width'] > 0, True)

    def test_no_overwrite_and_newer_version_rejected(self):
        source = self.root/'main.frameref'
        source.write_text(json.dumps(dict(file_version=3,board=dict(items=[]))))
        output = self.root/'out.pur'
        with self.assertRaises(UnsupportedFormat):
            convert(source,output)
        output.write_bytes(b'keep me')
        with self.assertRaises(FileExistsError):
            convert(source,output)
        self.assertEqual(output.read_bytes(),b'keep me')


if __name__ == '__main__':
    unittest.main()



class NoteHtmlRoundTrip(unittest.TestCase):
    def test_native_note_keeps_its_font_size_through_frameref(self):
        # Wrapping a whole HTML document in <div> used to drop the body's font size.
        fixture = Path(__file__).resolve().parents[1] / 'fixtures/native-drawing-2.1.3.pur'
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            convert(fixture, folder / 'board-FrameRef')
            document = json.loads((folder / 'board-FrameRef/main.frameref').read_text(encoding='utf-8'))
            note = next(properties(item) for item in document['board']['items']
                        if item['type'] == 'TextItem')
            self.assertFalse(note['html'].startswith('<div'))
            self.assertRegex(note['html'], r'<body style="[^"]*font-size:22px[^"]*color:#ffffff;"')
            convert(folder / 'board-FrameRef/main.frameref', folder / 'back.pur')
            with Board.load(folder / 'back.pur') as board:
                text = next(item.text for item in board.items if item.kind == 'note')
                self.assertIn('font-size:22px', text)
                self.assertEqual(sorted(item.name for item in board.items),
                                 ['', 'Note', 'Ukrainian reference'])
