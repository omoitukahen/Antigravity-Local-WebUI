# Third-party components

The MIT license at the repository root covers this project's own code.
Antigravity CLI is a separately installed Google product and is not included.

## Bundled browser libraries

| Component | Version | License | Included notice |
|---|---|---|---|
| [Marked](https://github.com/markedjs/marked) | 18.0.14 | MIT | static/vendor/marked-LICENSE.txt |
| [DOMPurify](https://github.com/cure53/DOMPurify) | 3.4.16 | Apache-2.0 OR MPL-2.0 | static/vendor/dompurify-LICENSE.txt |

Source URLs and SHA-256 hashes of the bundled JavaScript are recorded in
`static/vendor/manifest.json`. No CDN or remote fonts are used.

## Python dependencies

Python and the packages listed in `requirements.lock.txt` are installed separately
by the user; their binaries are not included in the source ZIP. Each package's own
license applies. Installed distribution metadata includes its license information
(`python -m pip show <package>`). This project does not relicense those packages.

Python: https://docs.python.org/3/license.html

PowerShell: https://github.com/PowerShell/PowerShell/blob/master/LICENSE.txt
