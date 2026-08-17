<!--
SPDX-FileCopyrightText: 2026 Fonden Mærsk Mc-Kinney Møller Center for Zero Carbon Shipping
SPDX-License-Identifier: CC-BY-4.0
-->

# Code Style

This document describes the coding conventions for Horizon. It builds on the
`flake8` configuration (pep8) with the additional preferences below. When in
doubt, match the conventions of the file you are editing.

## General Guidelines

- Question the use of `isinstance()` in core modules. Type checking should be
  performed at the input level; no redundant checks are needed later.
- Local consistency wins. If a file already follows a different convention,
  match the file rather than imposing a global rule.
- Never use `print()`. Use the logging module (`logger.info()`,
  `logger.error()`, ...) so output ends up in `output.log` alongside the
  `.hor` file.
- Use lazy `%s` formatting in logging calls (`logger.info("wrote %s", path)`),
  not f-strings or `.format()`.

## Typing

Type hints are encouraged on new and modified function signatures — on
signatures only, not on local variables inside function bodies. Much of the
existing codebase predates this preference and relies on docstrings instead;
extend hints as you touch code rather than in bulk rewrites.

If type hints require imports that would cause circular references, use
deferred annotations and type-checking-only imports:

```python
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from horizon.module.file import Class
```

## Docstrings

Every module and every public function or class gets a docstring. Keep the
summary terse — describe *what* the function does and any non-obvious *why*.
Include a `Parameters` section describing every argument; one short line per
parameter is enough so a reader does not have to chase definitions.

Verbose docstrings are explicitly discouraged.

## Layout and control flow

- A two-line `if` that ends in `continue` / `return` / `break` / `raise` is a
  guard. Always follow it with a blank line before the next logical step, so
  the structural break is visible.
- Treat logging calls and other string-assembly statements as their own
  visual section: surround them with a blank line above and below so they
  read as commentary on the surrounding logic, not as part of it.
- Prefer guard clauses over deeply nested `if`/`else`.
- Group related local variables; do not interleave them with logic.
- Keep functions focused. If you cannot name what a function does without
  "and", consider splitting.

## Naming

- Prefer descriptive names. A single-letter parameter or opaque prefix forces
  every reader to chase its definition; spell it out instead.
- Prefer clear domain names over abbreviations: `scenario_combination`, not
  `sc_comb`. Abbreviations are acceptable only when they are the established
  term (`lhs`, `mc`, `prcc`).
- Prefer names that reveal intent (`pending_commands`) over names that
  describe shape (`command_list`).
- Avoid generic suffixes like `_data`, `_obj`, `_thing`.
- Boolean names should read positively (`is_active`, not `not_inactive`).
- Methods, classes, and files used only within their own module are prefixed
  with `_`.

## Errors and exceptions

- Raise the structured exceptions from `horizon/exceptions.py`
  (`ParseError`, `ValidationError`, ...) rather than bare `ValueError` /
  `RuntimeError`, and include the offending value and its context in the
  message.
- Never use a bare `except:`. When catching broadly, log the failure
  (`logger.exception` / `logger.warning`) or make the intent explicit with a
  comment.

## Comments

- Default to no comment. Add one only when the *why* is not obvious.
- Never restate what the code already says.
- Do not leave dated or relative references in comments ("added for the X
  flow") — they rot.
- Comments start with a lowercase letter, like ordinary running prose.
- Stick to plain ASCII punctuation. Avoid decorative characters like arrows
  or box-drawing.
- Short comments stay on a single `#` line. Longer comments are formatted as
  a multi-line block with a constrained width so they read as paragraphs.

Files should open with a module docstring explaining the purpose of the file.
