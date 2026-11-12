# Pkit archive

My personal toolkit to manage my archive. It got ideas inspired from git annex.

## Examples

I have a manga to be archive let call it Pandora Hearts.

```python
manga = 'pandora-hearts'
local_path = f"/mnt/archive/{manga}"
paths = [StorePath(remote="local", src=local_path), StorePath(backend="ia", src=manga), StorePath("wayback", "/")]
archive.add(paths)
```
