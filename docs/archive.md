# Pkit archive

My personal toolkit to manage my archive. It got ideas inspired from git annex.

## Examples

I have a manga to be archive let call it Pandora Hearts.

```python
manga = "pandora-hearts"
local_path = f"/mnt/archive/{manga}"

# Hash local files, record content + local location.
archive.add(local_path)

# Uploaded to IA by hand years ago? Record every hash match. No transfer.
archive.match(local_path, f"ia:{manga}")

# match, then upload only what's missing, record each success.
archive.copy(local_path, f"ia:{manga}")
archive.copy(local_path, f"s3:my-bucket/manga/{manga}")
```
