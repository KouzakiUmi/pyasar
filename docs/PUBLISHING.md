# Publishing to PyPI

The package is published by `.github/workflows/publish.yml` using PyPI Trusted
Publishing. No long-lived API token is required.

## One-time account setup

Sign in to PyPI and open <https://pypi.org/manage/account/publishing/>. For a new
project, add a pending GitHub publisher with these exact values:

| Field | Value |
| --- | --- |
| PyPI project name | `asarx` |
| Owner | `KouzakiUmi` |
| Repository name | `pyasar` |
| Workflow name | `publish.yml` |
| Environment name | `pypi` |

If you already own the project on PyPI, add this publisher in the project's
Publishing settings instead. If the name belongs to somebody else, choose a
different distribution name and update the package metadata and workflow URL
before publishing. A pending publisher does not reserve the project name.

See [PyPI's Trusted Publishing documentation](https://docs.pypi.org/trusted-publishers/).

## Release

1. Ensure `project.version` in `pyproject.toml` is a new version on PyPI.
2. Commit and push the release changes to `main`.
3. Publish a GitHub release whose tag is `v<version>` (for example, `v0.1.0`),
   or manually run **Publish to PyPI** on `main` for the initial publication.
4. The workflow tests, builds, validates, and uploads both the wheel and source
   distribution. Check that the workflow succeeds and the version appears on
   PyPI before announcing the release.
5. Verify installation in a clean environment with `python -m pip install asarx`.
   The Python import name remains `pyasar`.

PyPI does not allow replacing an existing distribution. For changed package
contents after a successful release, increment the version before publishing.
