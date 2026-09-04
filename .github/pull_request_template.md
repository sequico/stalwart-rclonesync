Thanks for contributing! Please check the boxes that apply and keep the
description focused.

## Summary

<!-- What does this change do, and why? -->

## Safety review

This tool mirrors deletions and resolves conflicts — changes to those
semantics must keep the guarantees in the README.

- [ ] No change to deletion/conflict semantics, or semantics preserved and
      covered by tests
- [ ] No new runtime dependencies (stdlib + rclone only)
- [ ] Existing `state.json` files remain readable (no breaking format change
      without a migration path)
- [ ] README updated if user-facing behaviour changed
- [ ] CHANGELOG.md entry added under "Unreleased"

## Tests

- [ ] `pytest` passes locally
- [ ] `ruff check .` and `ruff format --check .` pass
- [ ] Tested on the lowest supported Python (3.9/3.10) if timestamp handling
      changed (see CONTRIBUTING.md)
