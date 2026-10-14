# Review instructions

This private repository belongs to Andrii Orlov. Review the public API,
corruption handling and native evidence before proposing a FrameRef integration.

```sh
python -m pip install -r requirements.txt
python -m pip install --no-deps .
python -m unittest discover -s tests -v
python -m compileall -q src tools tests
python tools/run_thousand.py /tmp/pur-review-new-output
```

Never overwrite the committed native fixtures or a user's board. Run generated
cases in a new directory. Keep common geometry/model in `core.py` and storage
encodings in codecs. Preserve unknown columns and payloads on ordinary edits.
Reject lossy conversions unless the explicit compatibility path reports them.

Review `docs/research/VERSION_RESEARCH.md`, `docs/CORE.md`, and native results.
Recognized future formats are unverified, not certified. Pillow pen rasterization
is approximate. A manifest bridge is not an installed FrameRef plugin.

Do not send Polygonflow email. The owner must test real boards and approve the
review before any proposal is sent.
