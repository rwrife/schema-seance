# Homebrew tap

`schema-seance` ships an optional Homebrew formula in
[`packaging/homebrew/schema-seance.rb`](./schema-seance.rb). It is
**not** in homebrew/core; it is intended to be served from a per-maintainer
tap, e.g.:

```bash
brew tap rwrife/schema-seance https://github.com/rwrife/homebrew-schema-seance
brew install schema-seance
seance --version
```

## Why a tap, not core

homebrew/core requires a project to be stable, popular, and notable. We are
none of those things yet. A tap lets the formula evolve at our pace and
keeps the canonical install surface (`pipx`/`pip`) authoritative.

## Updating the formula at release time

After a PyPI publish for `vX.Y.Z` lands:

1. Grab the sdist URL + sha256 from
   <https://pypi.org/project/schema-seance/#files> (look for the
   `schema_seance-X.Y.Z.tar.gz` source distribution).
2. Update `url`, `sha256`, and any `version` reference in
   `schema-seance.rb`.
3. From a tap checkout (Python 3.12 in PATH), refresh the runtime
   `resource` blocks:

   ```bash
   brew update-python-resources Formula/schema-seance.rb
   ```

4. Verify locally:

   ```bash
   brew install --build-from-source ./Formula/schema-seance.rb
   brew test schema-seance
   ```

5. Commit + push to the tap repo. Users pick it up via
   `brew upgrade schema-seance`.

## What's intentionally excluded

The `[tui]` extra (Textual + friends) is not installed by this formula.
`brew` users who want `seance parlor` should install it into a separate
virtualenv:

```bash
python3.12 -m venv ~/.local/seance-tui
~/.local/seance-tui/bin/pip install 'schema-seance[tui]'
```

This keeps the default brew install lean and avoids pulling Textual's
transitive deps for users who only want `summon` / `read`.
