# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/2.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-11-08

### Added

- First release.
- `personaltube setup` asks for the feed language, the AI provider and the YouTube account, then builds an interest profile.
- YouTube import from the browser login (Chrome, Firefox, Safari, Edge, Brave, Chromium, Opera, Vivaldi) or from a Google Takeout export.
- AI providers: OpenRouter, any OpenAI-compatible API, a Claude subscription through the Claude Code CLI, and a ChatGPT subscription through the Codex CLI.
- Daily search for new videos with AI scoring from 0 to 10.
- A one-page feed with a built-in player. It learns from opened, liked and hidden videos, and every reload gives a new mix.

[unreleased]: https://github.com/byrodi/personaltube/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/byrodi/personaltube/releases/tag/v0.1.0
