# Releasing crude

This is the end-to-end process for publishing a crude release. "Release X.Y.Z"
means publishing the version already present in the source tree, not choosing a
new version number. The version lives in `pyproject.toml`, `debian/changelog`,
and `crude.spec`; all three carry the same X.Y.Z before a release is cut.

crude is pure Python and both packages are architecture-independent
(`Architecture: all` / `BuildArch: noarch`), so the build host's architecture
does not affect the artifacts.

## Prerequisites

- `dpkg-buildpackage` (Debian packaging) and `rpmbuild` (RPM).
- `gh` authenticated against the `overseers-desk/crude` repository.
- For Homebrew users, no local tooling is needed; the formula references the
  GitHub-generated source tarball.

## Steps

1. **Sync the version.** Confirm `pyproject.toml`, `debian/changelog`, and
   `crude.spec` all name the same X.Y.Z, and that the changelog and the spec
   `%changelog` describe the user-visible changes.

2. **Commit** any source, test, and packaging changes for this release.

3. **Tag and push:** `git tag vX.Y.Z && git push origin vX.Y.Z`.

   Everything below depends only on this tag, not on the working tree. The two
   package builds (steps 4 and 5) and the Homebrew sha256 in the ot tap (step 7)
   are independent of one another and can run in parallel; step 6 needs both
   built packages, and step 8 verifies the finished release.

4. **Build the `.deb`** from the tagged source so the package matches the
   release exactly:

   ```
   git archive --format=tar --prefix=crude-X.Y.Z/ vX.Y.Z | tar -x -C /tmp/crude-build
   cd /tmp/crude-build/crude-X.Y.Z && dpkg-buildpackage -us -uc -b
   # artifact: /tmp/crude-build/crude_X.Y.Z_all.deb
   ```

5. **Build the `.rpm`.** `Source0` points at the GitHub tag tarball, so place
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

6. **Create the GitHub release** and attach both artifacts:

   ```
   gh release create vX.Y.Z --title "crude X.Y.Z" --notes-file <notes> \
     /tmp/crude-build/crude_X.Y.Z_all.deb \
     ~/rpmbuild/RPMS/noarch/crude-X.Y.Z-1.noarch.rpm
   ```

   The notes summarise the user-visible changes and carry the Install block
   below verbatim. GitHub auto-attaches the source tarball that the Homebrew
   formula points at.

7. **Bump the Homebrew formula sha256 in the ot tap.** The formula lives in the
   dedicated tap repo, overseers-desk/homebrew-ot, at `Formula/crude.rb`. Compute the
   sha256 of this release's source tarball, then in the overseers-desk/homebrew-ot
   repo update `Formula/crude.rb` with the new version and sha256 and push that
   commit:

   ```
   curl -sL https://github.com/overseers-desk/crude/archive/refs/tags/vX.Y.Z.tar.gz | sha256sum
   ```

8. **Verify** the release carries both package types:

   ```
   gh release view vX.Y.Z --json assets --jq '[.assets[].name]'
   ```

   Both `crude_X.Y.Z_all.deb` and `crude-X.Y.Z-1.noarch.rpm` should be present.

## Install block (copied verbatim into the release notes)

Homebrew:

```
brew tap overseers-desk/ot
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

A recut republishes the same version after the released artifacts change (for
example, adding a package format). Move the `vX.Y.Z` tag to the new commit
(`git tag -f vX.Y.Z && git push -f origin vX.Y.Z`), then repeat the build,
upload, and the ot-tap formula-sha256 step. Moving the tag changes the source
tarball, so the formula sha256 in overseers-desk/homebrew-ot is recomputed every recut.
