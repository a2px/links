# Links

A board of links worth keeping, published at https://a2px.github.io/links/.

Text a link to the Telegram bot and it shows up on the board within a few minutes,
sorted into a folder automatically. Put a folder name before the link to choose it
yourself (`fonts https://...`), `#New folder https://...` to start a new folder, or
`remove https://...` to take a link off.

- `links.json` holds every link, with the page's original title, description and
  preview image address under `source`.
- `img/` holds the square tile images.
- `scripts/board.py` adds and removes links; `scripts/telegram_inbox.py` reads the bot's messages.
- `.github/workflows/inbox.yml` checks for new messages every 5 minutes.
