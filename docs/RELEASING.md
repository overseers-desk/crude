# Releasing crude

This is the end-to-end process for publishing a crude release. "Release X.Y.Z"
means publishing the version already present in the source tree, not choosing a
new version number. The version lives in `pyproject.toml`, `debian/changelog`,
and `crude.spec`; all three carry the same X.Y.Z before a release is cut.

PyPI (`pip install crude`) is the primary install channel. The Homebrew formula's
`url` and `sha256` point at the PyPI sdist — the same tarball pip downloads — so
brew and pip share one file and one hash. The formula is not pointed at the
GitHub `git archive` tarball, which is a different file with a different hash.

crude is pure Python and both the `.deb` and `.rpm` are architecture-independent
(`Architecture: all` / `BuildArch: noarch`), so the build host's architecture
does not affect the artifacts.

## Prerequisites

- `uv` and `twine` for the PyPI publish; PyPI credentials in `~/.pypirc`.
- `dpkg-buildpackage` (Debian packaging) and `rpmbuild` (RPM).
- `gh` authenticated against the `overseers-desk/crude` repository.
- For Homebrew users, no local tooling is needed; the formula references the
  PyPI sdist.

## Steps

1. **Sync the version.** Confirm `pyproject.toml`, `debian/changelog`, and
   `crude.spec` all name the same X.Y.Z, and that the changelog and the spec
   `%changelog` describe the user-visible changes.

2. **Commit** any source, test, and packaging changes for this release.

3. **Tag and push:** `git tag vX.Y.Z && git push origin main vX.Y.Z`.

4. **Publish to PyPI** — the primary channel. Build the distribution and upload
   both the sdist and the wheel:

   ```
   uv build   # produces dist/crude-X.Y.Z.tar.gz (sdist) and the wheel
   uvx twine upload --non-interactive \
     dist/crude-X.Y.Z.tar.gz dist/crude-X.Y.Z-py3-none-any.whl
   ```

   Credentials come from `~/.pypirc`; do not read or print that file. `uv publish`
   does not read `~/.pypirc`, which is why twine is used here. Verify the version
   is live: `curl -sL https://pypi.org/pypi/crude/json` should show
   `info.version == X.Y.Z`.

5. **Build the `.deb`** from the tagged source so the package matches the
   release exactly:

   ```
   git archive --format=tar --prefix=crude-X.Y.Z/ vX.Y.Z | tar -x -C /tmp/crude-build
   cd /tmp/crude-build/crude-X.Y.Z && dpkg-buildpackage -us -uc -b
   # artifact: /tmp/crude-build/crude_X.Y.Z_all.deb
   ```

6. **Build the `.rpm`.** `Source0` points at the GitHub tag tarball, so place
   that tarball where rpmbuild expects it, then build. On a Debian or Ubuntu
   host, pass `--nodeps`: the build tools are present, but rpm's own database
   does not record the Debian-installed `python3-*` build packages, so the
   `BuildRequires` check would otherwise fail.

   ```
   mkdir -p ~/rpmbuild/SOURCES
   curl -sL https://github.com/overseers-desk/crude/archive/refs/tags/vX.Y.Z.tar.gz \
     -o ~/rpmbuild/SOURCES/crude-X.Y.Z.tar.gz
   rpmbuild -bb --nodeps crude.spec
   # artifact: ~/rpmbuild/RPMS/noarch/crude-X.Y.Z-1.noarch.rpm
   ```

7. **Create the GitHub release** and attach both artifacts:

   ```
   gh release create vX.Y.Z --title "crude X.Y.Z" --notes-file <notes> \
     /tmp/crude-build/crude_X.Y.Z_all.deb \
     ~/rpmbuild/RPMS/noarch/crude-X.Y.Z-1.noarch.rpm
   ```

   The notes summarise the user-visible changes and carry the Install block
   below verbatim.

8. **Bump the Homebrew formula in the od tap.** The formula lives in the
   dedicated tap repo, overseers-desk/homebrew-od, at `Formula/crude.rb`. Its
   `url` and `sha256` point at the PyPI sdist published in step 4. Read both
   straight from the PyPI JSON — take the object in `.urls` where
   `packagetype == "sdist"`, and use its `.url` (a `files.pythonhosted.org`
   link) as `url` and its `.digests.sha256` as `sha256`:

   ```
   curl -sL https://pypi.org/pypi/crude/X.Y.Z/json
   ```

   Update those fields in `Formula/crude.rb` in the overseers-desk/homebrew-od
   repo, then commit and push there. The formula is not part of this repo.

9. **Verify** the release carries both package types:

   ```
   gh release view vX.Y.Z --json assets --jq '[.assets[].name]'
   ```

   Both `crude_X.Y.Z_all.deb` and `crude-X.Y.Z-1.noarch.rpm` should be present.

## Install block (copied verbatim into the release notes)

PyPI (any platform):

```
pip install crude
# or, with uv, without installing:
uvx crude --help
```

Homebrew:

```
brew tap overseers-desk/od
brew install crude
```

Debian / Ubuntu:

```
sudo apt install ./crude_X.Y.Z_all.deb
```

Fedora / RHEL:

```
sudo dnf install ./crude-X.Y.Z-1.noarch.rpm
```

After installing by any method, register the Claude Code command so Claude Code
routes the relevant prompts through crude:

```
crude install-claude-command
```

## Recut

PyPI does not allow re-uploading a version, so a recut that needs new published
content requires a new version number. For GitHub/deb/rpm-only recuts, move the
`vX.Y.Z` tag to the new commit (`git tag -f vX.Y.Z && git push -f origin vX.Y.Z`),
then repeat the relevant build and upload steps. The Homebrew formula tracks the
PyPI sdist, so it changes only when a new version is published to PyPI.
