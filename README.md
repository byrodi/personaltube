# PersonalTube

Your own YouTube home feed, picked by AI from your watch history. It runs on your computer.

You open one page and scroll. If nothing looks good, reload and you get a new mix. The feed learns from what you open, like and hide.

It also works across languages. If you watch mostly Russian streamers but want English videos, it finds English creators with the same topics and the same energy. You pick the language in setup.

## How it works

1. PersonalTube reads your YouTube history, liked videos and subscriptions. It uses the YouTube login from your browser (read-only) or a Google Takeout export.
2. An AI model turns that history into 12 to 20 interests: what you watch, which creators and formats, plus YouTube search queries and channels for each one.
3. It searches YouTube for every query and checks the latest uploads of good channels. The searches are anonymous and paced like a person browsing.
4. The AI rates every video it found from 0 to 10 for you. Videos in the wrong language, kids content and reupload spam get 0. Only videos rated 6 or higher reach the feed.
5. The page ranks videos by score, freshness and your clicks. Videos you saw but skipped sink, channels you like rise, and "Not interested" pushes a channel down. Channels the AI keeps rating high become sources for the next search.

The feed refreshes itself once a day while `personaltube serve` is running.

## Install

You need Python 3.10 or newer and [pipx](https://pipx.pypa.io) (or [uv](https://docs.astral.sh/uv/)).

```sh
pipx install git+https://github.com/byrodi/personaltube
# or
uv tool install git+https://github.com/byrodi/personaltube
```

Or from a clone:

```sh
git clone https://github.com/byrodi/personaltube
cd personaltube
pipx install .
```

Then run the setup. It asks a few questions and builds your interest profile:

```sh
personaltube setup
personaltube serve    # opens http://localhost:8777
```

The first search takes about 15 to 20 minutes. The page reloads by itself when it is done.

## Choose your AI

| Provider | What you need | Cost |
| --- | --- | --- |
| OpenRouter (default) | An API key from [openrouter.ai](https://openrouter.ai) | About $0.05 per daily update with Claude Haiku, $0.15 for a new profile with Claude Sonnet |
| Claude subscription | [Claude Code](https://claude.com/claude-code) installed and signed in (`claude`) | Uses your Pro or Max plan limits |
| ChatGPT subscription | [Codex CLI](https://github.com/openai/codex) installed and signed in (`codex login`) | Uses your ChatGPT plan limits |
| Any OpenAI-compatible API | A base URL and a model: OpenAI, Ollama, LM Studio and others | Free with a local model |

You can change the provider or the models later in `~/.personaltube/config.json`:

```json
{
  "language": "English",
  "ai": {
    "provider": "openrouter",
    "model": "anthropic/claude-haiku-5.5",
    "profile_model": "anthropic/claude-sonnet-5.5",
    "api_key": "",
    "base_url": ""
  },
  "youtube": { "browser": "chrome" },
  "port": 8777
}
```

`model` scores videos and runs on every update, so a small fast model is enough. `profile_model` builds the interest profile once, and a stronger model does it better. Empty values use the defaults of the provider. The API key can also come from `OPENROUTER_API_KEY` or `OPENAI_API_KEY`.

## Connect YouTube

By default PersonalTube asks [yt-dlp](https://github.com/yt-dlp/yt-dlp) to read your YouTube cookies from Chrome, Firefox, Safari, Edge, Brave, Chromium, Opera or Vivaldi. It reads up to 3,000 watched videos, 1,000 likes and all subscriptions, and changes nothing in your account. On macOS a Keychain window may ask to allow access to "Chrome Safe Storage". Click Allow.

You can also use Google Takeout. Export "YouTube and YouTube Music" at [takeout.google.com](https://takeout.google.com) (history in JSON or HTML), then:

```sh
personaltube connect --takeout ~/Downloads/takeout-2026.zip
```

Use Takeout if reading cookies fails. This often happens with Chrome on Windows; Firefox is a good alternative there.

## Commands

| Command | What it does |
| --- | --- |
| `personaltube setup` | First run: AI, YouTube account, interest profile |
| `personaltube serve` | Opens the feed and refreshes it once a day. `--no-open` skips the browser tab |
| `personaltube update` | Finds new videos now |
| `personaltube connect` | Imports your YouTube history again. `--browser firefox` or `--takeout PATH` |
| `personaltube profile` | Rebuilds the interest profile, for example after a few weeks of new history |

## Privacy

- Everything stays in `~/.personaltube` on your computer: history, profile, clicks and the feed. Set `PERSONALTUBE_HOME` to keep it somewhere else.
- The site listens on `127.0.0.1` only.
- Your browser login is used only by `connect`, to read your own history. Searching for new videos is anonymous.
- Your AI provider gets the titles and channel names of the videos you watched and the videos it scores.
- With a local model, nothing leaves your computer.

## Troubleshooting

If the player says "Sign in to confirm you're not a bot", or searches come back empty, YouTube is limiting your IP address. This often happens with VPNs and datacenter addresses. Try another network, or wait an hour.

Some videos can only play on youtube.com. For them the player shows "The owner does not allow this video on other sites" with a button that opens the video there.

If `connect` cannot read cookies, close the browser fully and run it again, try another browser, or use Takeout.

If the feed feels off, hide a few videos with "Not interested" and like a few good ones. Then run `personaltube profile` and `personaltube update`.

## Thanks

Inspired by [HackerTube](https://hackertube.org) by Andrej Karpathy, a YouTube made of videos Hacker News recommends. YouTube access goes through [yt-dlp](https://github.com/yt-dlp/yt-dlp).

## License

[MIT](LICENSE)
