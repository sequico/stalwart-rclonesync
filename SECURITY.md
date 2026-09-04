# Security Policy

## Reporting a vulnerability

Please **do not open a public issue** for security problems.

Contact the maintainer privately instead:

- GitHub private vulnerability reporting (preferred):
  https://github.com/sequico/stalwart-rclonesync/security/advisories/new

You should receive an acknowledgement within a few days. Please include:

- the affected version(s),
- a description of the issue and its impact,
- reproduction steps (configuration + commands),
- a suggested fix, if you have one.

## Scope

This tool reads and writes user files on both endpoints and mirrors
deletions. Report anything that could lead to:

- unintended data loss or corruption,
- disclosure of credentials or file contents,
- traversal or path-injection through crafted file names,
- bypass of the `--ignore-prefix` or conflict safeguards.

The Stalwart server and pCloud service themselves are out of scope — report
issues against those to their own projects.

## Supported versions

Only the latest release is supported. Please update before reporting.
