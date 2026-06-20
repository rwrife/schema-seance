# typed: false
# frozen_string_literal: true

# Homebrew formula template for schema-seance.
#
# This file is the starting point for a third-party tap (for example
# `homebrew-rwrife/schema-seance`). It is intentionally checked into this
# repo rather than published into Homebrew core: schema-seance is too young
# to meet homebrew/core's stability and popularity bar, and a tap lets us
# iterate quickly without waiting on review.
#
# Maintenance flow on every release (see docs/RELEASING.md):
#
#   1. After the PyPI publish for vX.Y.Z succeeds, grab the sdist URL and
#      sha256 from https://pypi.org/project/schema-seance/#files
#        URL = https://files.pythonhosted.org/packages/source/s/schema-seance/schema_seance-X.Y.Z.tar.gz
#   2. Update `url`, `sha256`, and (if needed) `version` below.
#   3. Refresh the `resource` blocks for runtime deps via:
#        brew update-python-resources Formula/schema-seance.rb
#      (run from the tap checkout, against a Python 3.12 env).
#   4. `brew install --build-from-source ./Formula/schema-seance.rb`
#      to verify locally, then `brew test schema-seance`.
#   5. Commit + push to the tap; users get the new version on
#      `brew upgrade schema-seance`.
#
# The TUI extra (`schema-seance[tui]`) is intentionally NOT installed by
# this formula — keep the default brew install lean and let TUI users
# `pip install` it into a separate venv if they want it.

class SchemaSeance < Formula
  include Language::Python::Virtualenv

  desc "Spooky CLI medium for messy data — profiles CSV/JSONL/Parquet/SQLite"
  homepage "https://github.com/rwrife/schema-seance"
  url "https://files.pythonhosted.org/packages/source/s/schema-seance/schema_seance-0.1.0.tar.gz"
  sha256 "REPLACE_WITH_SDIST_SHA256_ON_RELEASE"
  license "MIT"
  head "https://github.com/rwrife/schema-seance.git", branch: "main"

  depends_on "python@3.12"

  # Runtime dependencies mirrored from pyproject.toml ([project].dependencies).
  # Pinned versions / sha256s are placeholders — regenerate with
  # `brew update-python-resources` against this formula before release.

  resource "click" do
    url "https://files.pythonhosted.org/packages/source/c/click/click-8.1.7.tar.gz"
    sha256 "REPLACE_VIA_BREW_UPDATE_PYTHON_RESOURCES"
  end

  resource "rich" do
    url "https://files.pythonhosted.org/packages/source/r/rich/rich-13.7.1.tar.gz"
    sha256 "REPLACE_VIA_BREW_UPDATE_PYTHON_RESOURCES"
  end

  resource "duckdb" do
    url "https://files.pythonhosted.org/packages/source/d/duckdb/duckdb-1.0.0.tar.gz"
    sha256 "REPLACE_VIA_BREW_UPDATE_PYTHON_RESOURCES"
  end

  resource "httpx" do
    url "https://files.pythonhosted.org/packages/source/h/httpx/httpx-0.27.0.tar.gz"
    sha256 "REPLACE_VIA_BREW_UPDATE_PYTHON_RESOURCES"
  end

  def install
    virtualenv_install_with_resources
  end

  test do
    # `seance --version` should print the formula's version string.
    assert_match version.to_s, shell_output("#{bin}/seance --version")

    # End-to-end: profile a tiny CSV and confirm the JSON envelope shape.
    (testpath/"tiny.csv").write <<~CSV
      id,name,email
      1,Ada Lovelace,ada@example.com
      2,Alan Turing,alan@example.com
    CSV

    json = shell_output("#{bin}/seance summon #{testpath}/tiny.csv --json")
    assert_match "\"schema_version\"", json
    assert_match "\"columns\"", json
  end
end
