# Security policy

Report suspected vulnerabilities privately through
[GitHub private vulnerability reporting](https://github.com/eigger/blesession/security/advisories/new).
Do not include exploit details, credentials or personal device data in a public issue.

Include the affected library version, relevant Home Assistant/backend versions,
the impact and a minimal reproduction with identifying data removed. Security
fixes target the latest published release; older releases may require an upgrade.

For connection failures, unsupported devices and ordinary bugs, use the device
integration's support channel or this repository's issue tracker as appropriate.

Dependencies are monitored by Dependabot. CI tests current and minimum supported
dependencies, and runs weekly. CodeQL scans Python and GitHub Actions. Release
publishing uses PyPI Trusted Publishing with a separate, restricted environment.

Dependabot pull requests from this repository are enrolled in squash auto-merge;
required branch-protection checks must pass before they merge. The automation
does not check out pull request code or bypass merge requirements. Merged pull
request branches are deleted automatically.
