# Hydra engine

A small engine that runs several Instagram quote channels ("heads"), each in its own niche, from one set of
GitHub Actions workflows. Each post is a fixed image card with a new line of text, turned into a 15-second
Reel, with music attached by Instagram at publish time.

This repo holds only the code. Each head's content (config, card image, quote bank, posting log and
insights) lives in a separate private repository that the workflows check out at run time.

## How it works

- `post.yml` runs every half hour. `engine/run.py check` asks every head whether this is one of its time
  slots; if none is, the job ends after a few seconds. Otherwise it renders and posts for each due head in
  one job.
- Each head has 10 candidate time slots. For the first 14 days they are tested evenly (5 posts a day); after
  that the 3 best by engagement per 1,000 reach are kept and 2 keep rotating.
- Music comes from Instagram's Audio API: mood searches first, then the trending list; a Reel goes out
  silent rather than not at all if no track can be attached.
- `insights.yml` pulls Reel and account metrics every 3 hours into each head's `insights/` folder with a
  `REPORT.md`. `topup.yml` asks Gemini (free tier) once a week for new lines, keeps only those a second pass
  scores 8+/10, and drops near-duplicates.

## Content layout (private repo)

```
heads/<handle>/config.json     handle, card text box, music, slots, captions, top-up prompts, launch_date,
                               live, utc_offset_minutes, paused
heads/<handle>/card.png        the fixed 1080x1920 card
heads/<handle>/quotes.csv      id, theme, text, source, added
heads/<handle>/schedule.csv    posts per day by day number
heads/<handle>/posted.csv      written by the engine
heads/<handle>/insights/       written by the engine
```

`example-head/` shows the shape of a head folder.

## Secrets and variables (names only)

| Kind | Name | What |
|---|---|---|
| secret | `CONTENT_REPO` | owner/name of the private content repo |
| secret | `CONTENT_TOKEN` | fine-grained token: contents read and write on that repo only |
| secret | `META_TOKEN` | Meta system-user token |
| secret | `CF_API_TOKEN` | Cloudflare token, Pages edit (media hosting) |
| secret | `CLOUDFLARE_ACCOUNT_ID` | Cloudflare account id |
| secret | `GEMINI_API_KEY` | Google AI Studio key |
| variable | `SOCIAL_LIVE` | `true` to publish (each head also needs `"live": true`) |

## Safety

- Nothing publishes unless `SOCIAL_LIVE` is `true` and the head's config has `"live": true`. Manual runs are
  dry runs by default: they upload media and check the account and music lookup without posting.
- Logs of this repo are public: printed output hides long ids, tokens and e-mail addresses, account and page
  ids are masked, and new quote lines are never printed.
- Bot commits go only to the content repo, as `hydra-bot`.

Fonts: Lora, SIL Open Font License 1.1.
