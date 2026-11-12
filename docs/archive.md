# Pkit archive

My personal toolkit to manage my archive. It got ideas inspired from git annex.

## Examples

I have a manga to be archive let call it Pandora Hearts.

```python
manga = 'pandora-hearts'
# add path into my archive. it will create metadata and link to CAS
archive.add(path)
remote = f"ia:{manga}"
if archive.exist(remote):
    # map files in folder to IA item by hash
    m = archive.map_files(path, remote)
    archive.link(m)
else:
    archive.upload(path, remote)
```
