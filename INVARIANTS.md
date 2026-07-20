# Invariants

Rules whose breach is a design change, not a fix; changing one is the owner's decision.

- Each site ships as its own `crude-<site>` binary under the one `crude-<site> <resource> <verb>` grammar, and `src/` holds `crude_common` plus one `crude_<site>` package per binary: a site's code confined to its own package is what lets a site be added, broken, or deleted without touching the others, and the shared grammar is what a user's muscle memory and the launcher's index both depend on. `[project.scripts]` in `pyproject.toml` is the source of truth for the set; the file system and the docs follow it.
- Every site reads its own named section of the one config file: a per-site config file would give each user N files to manage and each site a reason to invent its own format, and the single file is what lets `crude config-sample` describe the whole surface at once.
