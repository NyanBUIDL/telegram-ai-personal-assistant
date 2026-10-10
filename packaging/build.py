"""Pinned Windows build and provenance; output is private and unsigned."""

import argparse
import hashlib
import importlib.metadata as metadata
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import tomllib
from pathlib import Path


def validate_output_tree(root, target):
    """Reject redirected ancestors and descendants before any output mutation."""
    root, target = root.absolute(), target.absolute()
    if target == root or not target.is_relative_to(root):
        raise ValueError("Build output containment failed")

    def reject_reparse(path):
        try:
            attributes = path.lstat()
        except FileNotFoundError:
            return
        if stat.S_ISLNK(attributes.st_mode) or getattr(attributes, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise ValueError("Reparse point in build output or its ancestors")

    for path in [target, *target.parents]:
        reject_reparse(path)
    if not target.resolve().is_relative_to(root.resolve()):
        raise ValueError("Physical build output containment failed")
    if not target.exists():
        return  # Fresh output paths have no descendants to inspect.

    def fail_walk(error):
        raise error

    for directory, directories, files in os.walk(target, followlinks=False, onerror=fail_walk):
        # Reject each child before os.walk can descend into a Windows junction.
        for name in directories + files:
            reject_reparse(Path(directory) / name)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def locked_versions(path):
    return {canonical(line.split("==")[0]): line.split("==")[1].split()[0]
            for line in path.read_text().splitlines() if line and not line.startswith("#")}


def validate_environment(expected, actual):
    if expected != actual:
        raise ValueError("Build environment differs from the complete dependency locks")


def validate_requirements(requirements, versions):
    from packaging.requirements import Requirement

    for value in requirements:
        requirement = Requirement(value)
        if requirement.marker is None or requirement.marker.evaluate({"extra": "desktop"}):
            version = versions.get(canonical(requirement.name))
            if version is None or version not in requirement.specifier:
                raise ValueError("Dependency lock is stale against project requirements")


def resource_files(root):
    config = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    resources = config["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]
    result = []
    for relative in resources:
        path = root / relative
        if not path.exists() or path.is_symlink():
            raise ValueError("Required immutable resource is missing or linked: " + relative)
        candidates = sorted(path.rglob("*")) if path.is_dir() else [path]
        for item in candidates:
            if item.is_symlink():
                raise ValueError("Linked resource is forbidden")
            if item.is_file():
                if item.suffix.lower() not in {".py", ".md", ".js", ".css", ".html", ".ttf", ".otf", ".woff", ".woff2", ".svg", ".ico"}:
                    raise ValueError("Unexpected resource: " + item.name)
                result.append(item)
    if not (root / "dashboard-prototype/dist/client/index.html").is_file():
        raise ValueError("Dashboard build is incomplete")
    return result


def run(args, root, **kwargs):
    return subprocess.run([str(arg) for arg in args], cwd=root, check=True, **kwargs)


def git(root, *args):
    return run(["git", *args], root, capture_output=True, text=True).stdout.strip()


def validate_frontend_toolchain(root, node, npm):
    """Verify the complete upstream extraction documented in TOOLCHAIN.md."""
    node_version = run([node, "--version"], root, capture_output=True, text=True).stdout.strip()
    npm_version = run([node, npm, "--version"], root, capture_output=True, text=True).stdout.strip()
    if node_version != "v24.19.0" or npm_version != "11.21.0":
        raise ValueError("Requires Node 24.19.0 and npm 11.21.0")
    npm_files = {path.relative_to(npm.parent.parent).as_posix(): sha(path) for path in sorted(npm.parent.parent.rglob("*")) if path.is_file()}
    npm_digest = hashlib.sha256(json.dumps(npm_files, sort_keys=True).encode()).hexdigest()
    if sha(node) != "3602f2bb1a10f2cbab4c36886218a33c1ab3db87290e73b033c46c77147d0237" or npm_digest != "10522cee6906a10146490d1bd6e286e76c6b489bc5d1f0150f6f9b9f35186bea":
        raise ValueError("Frontend toolchain bytes differ from the verified Node/npm inputs")
    return node_version, npm_version, npm_files


def source_inventory(root):
    names = git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard").split("\0")
    return {name: sha(root / name) for name in names if (root / name).is_file()}


def notices(root, destination):
    destination.mkdir(parents=True, exist_ok=True)
    records = []
    components = locked_versions(root / "packaging/windows-runtime.lock")
    components["pyinstaller"] = metadata.version("pyinstaller")  # The executable contains its bootloader.
    for name, version in components.items():
        dist = metadata.distribution(name)
        copied = []
        for entry in dist.files or []:
            if any(word in str(entry).lower() for word in ("license", "copying", "copyright", "notice")):
                source = Path(dist.locate_file(entry))
                if source.is_file():
                    target = destination / name / str(entry).replace("../", "")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, target)
                    copied.append({"file": target.relative_to(destination).as_posix(), "sha256": sha(target)})
        records.append({"name": name, "version": version,
                        "license": dist.metadata.get("License-Expression") or dist.metadata.get("License"),
                        "project_urls": dist.metadata.get_all("Project-URL") or [], "notices": copied})
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if not python_license.is_file():
        raise ValueError("CPython license text is missing")
    shutil.copyfile(python_license, destination / "CPython-LICENSE.txt")
    frontend = []
    for name in ("@phosphor-icons/react", "react", "react-dom", "scheduler"):
        package = root / "dashboard-prototype/node_modules" / name
        info = json.loads((package / "package.json").read_text(encoding="utf-8"))
        copied = []
        for source in package.iterdir():
            if source.is_file() and source.name.lower().startswith(("license", "notice", "copying")):
                target = destination / "frontend" / name / source.name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
                copied.append({"file": target.relative_to(destination).as_posix(), "sha256": sha(target)})
        frontend.append({"name": name, "version": info["version"], "license": info.get("license"), "notices": copied})
    result = {"schema_version": 1, "python_and_bootloader_components": records, "frontend_components": frontend,
              "unresolved_distribution_gates": ["Purchased font embedding/redistribution entitlement and notices", "Qt plugin notices, corresponding source/replacement compliance review", "Project private-use distribution authorization"],
              "scope": "Installed runtime wheel metadata; frozen file inventory and frontend lock accompany this record"}
    (destination / "runtime-sbom.json").write_text(json.dumps(result, indent=2), encoding="utf-8")


def main():
    root = Path(__file__).absolute().parents[1]
    output = root / "build/windows"
    validate_output_tree(root, output)
    (output / "manifest.json").unlink(missing_ok=True)
    parser = argparse.ArgumentParser()
    parser.add_argument("--node", required=True)
    parser.add_argument("--npm-cli", required=True)
    parser.add_argument("--preflight", action="store_true")
    args = parser.parse_args()
    if os.name != "nt" or platform.python_version() != "3.12.14" or sys.maxsize <= 2**32:
        raise ValueError("Requires Windows CPython 3.12.14 x64")
    if metadata.version("pip") != "25.0.1":
        raise ValueError("Requires the CPython 3.12.14 bootstrap pip 25.0.1")
    output.mkdir(parents=True, exist_ok=True)
    expected = locked_versions(root / "packaging/windows-build.lock") | locked_versions(root / "packaging/windows-runtime.lock")
    actual = {canonical(dist.metadata["Name"]): dist.version for dist in metadata.distributions() if canonical(dist.metadata["Name"]) != "pip"}
    validate_environment(expected, actual)
    config = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    validate_requirements(config["project"]["dependencies"] + config["project"]["optional-dependencies"]["desktop"] + config["build-system"]["requires"], expected)
    before = source_inventory(root)
    commit = git(root, "rev-parse", "HEAD")
    dirty = bool(git(root, "status", "--porcelain"))
    if dirty and not args.preflight:
        raise ValueError("Dirty source requires --preflight; cannot create a release candidate")
    (output / "inputs.json").write_text(json.dumps({"source_commit": commit, "source_dirty": dirty, "source_sha256": before}, indent=2, sort_keys=True), encoding="utf-8")
    node = Path(shutil.which(args.node) or args.node).resolve(strict=True)
    npm = Path(args.npm_cli).resolve(strict=True)
    node_version, npm_version, npm_files = validate_frontend_toolchain(root, node, npm)
    env = dict(os.environ, PATH=str(node.parent) + os.pathsep + os.environ.get("PATH", ""))
    env.pop("PYTHONPATH", None)
    validate_output_tree(root, root / "dashboard-prototype/node_modules")
    validate_output_tree(root, root / "dashboard-prototype/dist")
    run([node, npm, "ci", "--ignore-scripts", "--no-audit", "--no-fund"], root / "dashboard-prototype", env=env)
    run([node, npm, "run", "build"], root / "dashboard-prototype", env=env)
    resources = resource_files(root)
    notice_dir = output / "notices"
    validate_output_tree(root, output)
    if notice_dir.exists():
        shutil.rmtree(notice_dir)
    notices(root, notice_dir)
    # Ambient tools can shadow Windows DLLs (for example Poppler's incompatible ICU).
    env["PATH"] = os.pathsep.join([str(Path(sys.executable).parent), sys.base_prefix, str(Path(os.environ["SystemRoot"]) / "System32"), os.environ["SystemRoot"]])
    env["PYTHONPATH"] = str(root / "src")
    env["PYINSTALLER_CONFIG_DIR"] = str(output / "pyinstaller-cache")
    run([sys.executable, "-m", "hatchling", "build", "-t", "wheel", "-d", output / "wheel"], root, env=env)
    run([sys.executable, "-m", "PyInstaller", "--clean", "--noconfirm", "--distpath", output / "dist", "--workpath", output / "work", root / "packaging/assistant.spec"], root, env=env)
    artifact = output / "dist/TelegramAIPersonalAssistant"
    executable = artifact / "TelegramAIPersonalAssistant.exe"
    diagnostic = output / "diagnostics.json"
    diagnostic.unlink(missing_ok=True)
    env.pop("QT_QPA_PLATFORM", None)
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    env.pop("QT_PLUGIN_PATH", None)
    env.pop("QT_QPA_PLATFORM_PLUGIN_PATH", None)
    env["PATH"] = os.pathsep.join([str(Path(os.environ["SystemRoot"]) / "System32"), os.environ["SystemRoot"]])
    run([executable, "--diagnostics", diagnostic], output, env=env, timeout=90)
    checks = json.loads(diagnostic.read_text())
    if checks["status"] != "ok" or not checks["frozen"]:
        raise ValueError("Frozen diagnostics failed")
    if before != source_inventory(root) or commit != git(root, "rev-parse", "HEAD"):
        raise ValueError("Source inputs changed during build")
    inventory = {path.relative_to(artifact).as_posix(): sha(path) for path in sorted(artifact.rglob("*")) if path.is_file()}
    from alembic.script import ScriptDirectory

    manifest = {"schema_version": 1, "version": tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"],
                "database_revision": ScriptDirectory(str(root / "alembic")).get_current_head(),
                "source_commit": commit, "source_dirty": dirty, "purpose": "dirty-preflight-only" if args.preflight else "private-preview-candidate",
                "signed": False, "distribution": "private-only", "python": platform.python_version(),
                "pip": metadata.version("pip"),
                "node": {"version": node_version, "executable_sha256": sha(node)},
                "npm": {"version": npm_version, "package_files": npm_files},
                "dependencies": expected, "source_sha256": before,
                "resources_sha256": {path.relative_to(root).as_posix(): sha(path) for path in resources},
                "files_sha256": inventory, "diagnostics": checks,
                "wheel_sha256": {path.name: sha(path) for path in sorted((output / "wheel").glob("*.whl"))},
                "executable": "dist/TelegramAIPersonalAssistant/TelegramAIPersonalAssistant.exe"}
    manifest_path = output / "manifest.json"
    validate_output_tree(root, output)
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"executable": str(executable), "manifest": str(manifest_path), "manifest_sha256": sha(manifest_path), "executable_sha256": sha(executable)}))


if __name__ == "__main__":
    main()
