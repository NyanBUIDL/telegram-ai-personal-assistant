# Windows frontend build inputs

Use Windows x64 and CPython 3.12.14. The wrapper creates its own Python
environment from the two hash-enforced locks. Node and npm are extracted locally;
no global install or developer package cache is required. Keep the extraction
path short (for example `E:\tg-tools-20261010`) because npm contains deep paths.
Use a new, nonexistent directory for each acquisition.

The following upstream inputs were retrieved and verified on 2026-10-10:

| Input | Official archive | SHA256 |
| --- | --- | --- |
| Node 24.19.0 Windows x64 | https://nodejs.org/dist/v24.19.0/node-v24.19.0-win-x64.zip | `57f71ab3652e797d84acddc79c81cc9ff1c6ddb2a1974cdb83f00fee9bff4c73` |
| npm 11.21.0 | https://registry.npmjs.org/npm/-/npm-11.21.0.tgz | `783e7c92bf73b442fb800c2d6ef3921e86da8894a700fed45140e37916877482` |

Node's archive digest matches its [upstream checksum list](https://nodejs.org/dist/v24.19.0/SHASUMS256.txt).
The npm archive also matches the `dist.integrity` value in its
[registry metadata](https://registry.npmjs.org/npm/11.21.0):
`sha512-Zov8KhamNneiLdELtj5YALtNmJW4L4fCLTzjfpzXG2w6MSHcf0UxgdlK5uloCuksWT+7mGUU7wi79cO6RqivPg==`.

Save this recipe as a temporary `acquire-frontend.py` outside the checkout. Run it
with the pinned Python, passing the new extraction directory and checkout root:
`python acquire-frontend.py E:\tg-tools-20261010 "E:\path\to\checkout"`.
It downloads only the two pinned archives, checks their bytes before extraction,
then invokes the build helper's exact version and full-tree validation. A failed
acquisition leaves its directory for inspection; choose a new directory to retry.

```python
import base64
import hashlib
import importlib.util
import json
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

destination = Path(sys.argv[1]).absolute()
checkout = Path(sys.argv[2]).resolve(strict=True)
destination.mkdir()  # Refuse reuse; never remove an existing directory.
inputs = [
    ("node-v24.19.0-win-x64.zip",
     "https://nodejs.org/dist/v24.19.0/node-v24.19.0-win-x64.zip",
     "57f71ab3652e797d84acddc79c81cc9ff1c6ddb2a1974cdb83f00fee9bff4c73"),
    ("npm-11.21.0.tgz",
     "https://registry.npmjs.org/npm/-/npm-11.21.0.tgz",
     "783e7c92bf73b442fb800c2d6ef3921e86da8894a700fed45140e37916877482"),
]
provenance = []
for name, url, expected in inputs:
    with urllib.request.urlopen(url, timeout=120) as response:
        content = response.read()
    actual = hashlib.sha256(content).hexdigest()
    if actual != expected:
        raise ValueError("Archive hash mismatch: " + name)
    (destination / name).write_bytes(content)
    provenance.append({"url": url, "sha256": actual, "bytes": len(content)})
integrity = base64.b64encode(hashlib.sha512((destination / inputs[1][0]).read_bytes()).digest()).decode()
if integrity != "Zov8KhamNneiLdELtj5YALtNmJW4L4fCLTzjfpzXG2w6MSHcf0UxgdlK5uloCuksWT+7mGUU7wi79cO6RqivPg==":
    raise ValueError("npm integrity mismatch")
with zipfile.ZipFile(destination / inputs[0][0]) as archive:
    archive.extractall(destination / "node")
with tarfile.open(destination / inputs[1][0]) as archive:
    archive.extractall(destination / "npm", filter="data")
node = destination / "node/node-v24.19.0-win-x64/node.exe"
npm = destination / "npm/package/bin/npm-cli.js"
spec = importlib.util.spec_from_file_location("windows_build", checkout / "packaging/build.py")
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)
node_version, npm_version, files = build.validate_frontend_toolchain(checkout, node, npm)
receipt = {"archives": provenance, "node": str(node), "npm_cli": str(npm),
           "node_version": node_version, "npm_version": npm_version,
           "node_sha256": build.sha(node), "npm_file_count": len(files),
           "npm_tree_sha256": hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()}
(destination / "verified-extraction.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
print(json.dumps(receipt, indent=2))
```

The accepted Node executable SHA256 is
`3602f2bb1a10f2cbab4c36886218a33c1ab3db87290e73b033c46c77147d0237`.
The complete npm extraction contains 1,961 regular files; the build's sorted
relative-path-to-file-SHA256 JSON map hashes to
`10522cee6906a10146490d1bd6e286e76c6b489bc5d1f0150f6f9b9f35186bea`.
Preserve all upstream files, including the 18 bundled `.pyc` files. Do not add
package-manager shims or reuse an npm cache tree. The earlier developer-cache
digest `17a4c596...` is superseded by this complete upstream extraction.

After review/source freeze, run from the checkout root in PowerShell:

```powershell
./scripts/build-windows.ps1 -Python 'C:/path/to/Python312/python.exe' `
    -Node 'E:/tg-tools-20261010/node/node-v24.19.0-win-x64/node.exe' `
    -NpmCli 'E:/tg-tools-20261010/npm/package/bin/npm-cli.js'
```

No arguments are needed when these exact tools are already discoverable as
`python`, `node`, and `npm.cmd`; otherwise use the explicit paths above. A dirty
checkout requires `-Preflight` and produces only a private unsigned preflight.
The acquisition receipt proves tool recovery; it is not a frozen application
build or clean-machine acceptance receipt.
