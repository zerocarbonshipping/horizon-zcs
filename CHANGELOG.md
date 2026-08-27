<!--
SPDX-FileCopyrightText: 2026 Fonden Mærsk Mc-Kinney Møller Center for Zero Carbon Shipping
SPDX-License-Identifier: CC-BY-4.0
-->

# Changelog
All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/)
and the project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed
- Include directives written into generated `.nav` files now use Navigate's
  current `Include` spelling instead of the legacy all-caps `INCLUDE`, which
  Navigate's grammar rejects. Every include line in a generated `.nav` was
  affected, so any template with includes failed to run.
- Templates still using `INCLUDE` (or lowercase `include`) are normalized to
  `Include` on generation, and the generated include lines now keep the
  template's own indentation.
- Include-line detection is word-boundary aware, so an attribute such as
  `IncludeRate = 5` is no longer mistaken for an include directive.

## [1.0.0] - 2026-08-17

Initial public release of Horizon, an open-source uncertainty analysis tool
for the [Navigate](https://github.com/zerocarbonshipping/navigate-zcs)
maritime transition model.
